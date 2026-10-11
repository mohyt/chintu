import contextlib
import io
import json
import logging
import os
import sqlite3
import stat
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from chintu_adapters import AdapterEvent, ScriptedAdapter
from chintu_gateway import Settings, Store, create_app
from chintu_gateway.app import RateLimiter, local_secret_path
from chintu_gateway.cli import _private_log_files, main
from chintu_gateway.store import MAX_OPEN_ENROLLMENTS, result
from starlette.testclient import TestClient


def uid():
    return str(uuid4())


class CountingAdapter(ScriptedAdapter):
    def __init__(self):
        super().__init__()
        self.commands = []

    async def run(self, command):
        self.commands.append(command)
        async for event in super().run(command):
            yield event


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "gateway.sqlite3"
        self.adapter = CountingAdapter()
        self.app = create_app(Settings(self.database), adapter=self.adapter)
        self.client = TestClient(self.app).__enter__()
        self.headers = self.pair(self.app.state.store.create_enrollment())

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def pair(self, secret):
        response = self.client.post("/api/v1/pair", headers={"X-Chintu-Protocol": "1"},
                                    json={"enrollment_secret": secret, "client_name": "Test phone"})
        self.assertEqual(response.status_code, 200, response.text)
        self.schema(response, "PairResponse")
        return {"X-Chintu-Protocol": "1", "Authorization": "Bearer " + response.json()["token"]}

    def schema(self, response, name):
        self.app.state.contract.validate(name, response.json())

    def post(self, route, value, expected=200):
        response = self.client.post(route, headers=self.headers, json=value)
        self.assertEqual(response.status_code, expected, response.text)
        self.schema(response, "Error" if expected >= 400 else "TaskAccepted")
        return response.json()

    def submit(self, instruction="hello", key=None):
        value = {"submission_key": key or uid(), "provider": "scripted", "project_id": "fixture",
                 "execution_profile": "inspect", "instruction": instruction}
        response = self.post("/api/v1/tasks", value, 202)
        return value, response["task"]

    def get(self, task_id):
        response = self.client.get("/api/v1/tasks/" + task_id, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.schema(response, "TaskSnapshot")
        return response.json()

    def wait(self, task_id, state):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snapshot = self.get(task_id)
            if snapshot["state"] == state:
                return snapshot
            time.sleep(.01)
        self.fail(f"Expected {state}, got {snapshot['state']}")

    def test_protocol_auth_single_use_secret_and_revocation(self):
        for headers, expected in (({}, 426), ({"X-Chintu-Protocol": "1"}, 401)):
            response = self.client.get("/api/v1/projects", headers=headers)
            self.assertEqual(response.status_code, expected)
            self.schema(response, "Error")
        secret = self.app.state.store.create_enrollment()
        other = self.pair(secret)
        response = self.client.post("/api/v1/pair", headers={"X-Chintu-Protocol": "1"},
                                    json={"enrollment_secret": secret, "client_name": "Replay"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(secret, response.text)
        self.schema(response, "Error")
        self.assertEqual(self.client.delete("/api/v1/clients/me", headers=other).status_code, 204)
        self.assertEqual(self.client.get("/api/v1/projects", headers=other).status_code, 401)
        with self.app.state.store.connection() as db:
            content = " ".join(str(tuple(r)) for r in db.execute("SELECT * FROM clients"))
            content += " ".join(str(tuple(r)) for r in db.execute("SELECT * FROM enrollments"))
        self.assertNotIn(secret, content)
        self.assertNotIn(other["Authorization"][7:], content)

    def test_expired_enrollment_never_consumes_successfully(self):
        secret = self.app.state.store.create_enrollment()
        with self.app.state.store.transaction() as db:
            db.execute("UPDATE enrollments SET expires=0")
        response = self.client.post("/api/v1/pair", headers={"X-Chintu-Protocol": "1"},
                                    json={"enrollment_secret": secret, "client_name": "Expired"})
        self.assertEqual(response.status_code, 401)
        self.schema(response, "Error")

    def test_concurrent_submission_retry_has_one_attempt(self):
        value = {"submission_key": uid(), "project_id": "fixture", "provider": "scripted",
                 "instruction": "one task", "execution_profile": "inspect"}
        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(lambda _: self.client.post("/api/v1/tasks", headers=self.headers, json=value), range(4)))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 200, 200, 202])
        self.assertEqual(len({r.json()["task"]["task_id"] for r in responses}), 1)
        for response in responses:
            self.schema(response, "TaskAccepted")
        self.wait(responses[0].json()["task"]["task_id"], "succeeded")
        self.assertEqual(len(self.adapter.commands), 1)
        value["instruction"] = "changed"
        response = self.post("/api/v1/tasks", value, 409)
        self.assertEqual(response["error"]["code"], "idempotency_conflict")

    def test_operation_keys_are_scoped_to_client(self):
        value, first = self.submit()
        second_headers = self.pair(self.app.state.store.create_enrollment())
        response = self.client.post("/api/v1/tasks", headers=second_headers, json=value)
        self.assertEqual(response.status_code, 202)
        self.assertNotEqual(first["task_id"], response.json()["task"]["task_id"])
        # Even the owner's other phone cannot see this phone's task.
        self.assertEqual(self.client.get("/api/v1/tasks/" + first["task_id"], headers=second_headers).status_code, 404)

    def test_tasks_are_private_to_the_pairing_client(self):
        _, mine = self.submit("[wait]")
        self.wait(mine["task_id"], "running")
        other = self.pair(self.app.state.store.create_enrollment())
        _, theirs = self.submit()
        route = "/api/v1/tasks/" + mine["task_id"]
        self.assertEqual(self.client.get(route, headers=other).status_code, 404)
        listed = self.client.get("/api/v1/tasks", headers=other).json()["tasks"]
        self.assertEqual(listed, [])
        self.assertEqual(self.client.get("/api/v1/events?after=0", headers=other).json()["events"], [])
        page = self.client.get(f"/api/v1/events?after=0&task_id={mine['task_id']}", headers=other).json()
        self.assertEqual(page["events"], [])
        for suffix, body in (("/cancel", {"message_id": uid()}),
                             ("/messages", {"message_id": uid(), "expected_task_version": 1, "instruction": "x"}),
                             (f"/inputs/{uid()}/answer", {"message_id": uid(), "expected_task_version": 1,
                                                         "attempt_id": uid(), "answer": "yes"}),
                             (f"/approvals/{uid()}/decision", {"message_id": uid(), "expected_task_version": 1,
                                                              "attempt_id": uid(), "action_digest": "a" * 64,
                                                              "decision": "allow"})):
            with self.subTest(suffix=suffix):
                response = self.client.post(route + suffix, headers=other, json=body)
                self.assertEqual(response.status_code, 404, response.text)
                self.schema(response, "Error")
        self.assertEqual(self.get(mine["task_id"])["state"], "running")
        self.assertEqual([t["task_id"] for t in self.client.get("/api/v1/tasks", headers=self.headers).json()["tasks"]],
                         [mine["task_id"], theirs["task_id"]])
        kinds = {e["task_id"] for e in self.client.get("/api/v1/events?after=0", headers=self.headers).json()["events"]}
        self.assertEqual(kinds, {mine["task_id"], theirs["task_id"]})
        self.post("/api/v1/tasks/" + mine["task_id"] + "/cancel", {"message_id": uid()})
        self.wait(mine["task_id"], "cancelled")

    def test_credentials_expire_and_can_be_revoked_locally(self):
        store = self.app.state.store
        with store.connection() as db:
            row = db.execute("SELECT expires_at, paired_at FROM clients").fetchone()
        self.assertAlmostEqual(row["expires_at"] - row["paired_at"], 30 * 86400, delta=5)
        with self.assertRaises(ValueError):
            Store(Path(self.temp.name) / "ttl.sqlite3", token_ttl_days=0)
        with store.transaction() as db:
            db.execute("UPDATE clients SET expires_at=?", (time.time() - 1,))
        response = self.client.get("/api/v1/projects", headers=self.headers)
        self.assertEqual(response.status_code, 401)
        self.schema(response, "Error")
        self.assertEqual(store.clients(), [])
        headers = self.pair(store.create_enrollment())
        client_id = store.clients()[0]["client_id"]
        self.assertTrue(store.revoke(client_id))
        self.assertFalse(store.revoke(client_id))
        self.assertFalse(store.revoke(uid()))
        self.assertEqual(self.client.get("/api/v1/projects", headers=headers).status_code, 401)
        self.pair(store.create_enrollment())
        self.pair(store.create_enrollment())
        # Two live clients plus the expired, never-revoked one from setUp.
        self.assertEqual(store.revoke_all(), 3)
        self.assertEqual(store.clients(), [])

    def test_revoke_cli_is_offline_and_reports_counts(self):
        store = self.app.state.store
        headers = self.pair(store.create_enrollment())
        client_id = store.clients()[-1]["client_id"]
        out = io.StringIO()
        with patch("sys.argv", ["chintu-gateway", "--database", str(self.database), "revoke", "--client-id", client_id]):
            with contextlib.redirect_stdout(out):
                main()
        self.assertEqual(json.loads(out.getvalue()), {"revoked": 1})
        self.assertEqual(self.client.get("/api/v1/projects", headers=headers).status_code, 401)
        out = io.StringIO()
        with patch("sys.argv", ["chintu-gateway", "--database", str(self.database), "revoke", "--all"]):
            with contextlib.redirect_stdout(out):
                main()
        self.assertEqual(json.loads(out.getvalue()), {"revoked": 1})
        self.assertEqual(self.client.get("/api/v1/projects", headers=self.headers).status_code, 401)

    def test_pair_url_cli_reads_the_local_secret(self):
        path = local_secret_path(Settings(self.database))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.read_text(), self.app.state.local_secret)
        out = io.StringIO()
        with patch("sys.argv", ["chintu-gateway", "--database", str(self.database), "pair-url", "--port", "9999"]):
            with contextlib.redirect_stdout(out):
                main()
        self.assertEqual(out.getvalue().strip(), "http://127.0.0.1:9999/pair?key=" + self.app.state.local_secret)
        path.unlink()
        with self.assertRaises(SystemExit):
            with patch("sys.argv", ["chintu-gateway", "--database", str(self.database), "pair-url"]):
                with contextlib.redirect_stderr(io.StringIO()):
                    main()

    def test_pairing_is_throttled(self):
        other = create_app(Settings(Path(self.temp.name) / "throttle.sqlite3"))
        with TestClient(other) as client:
            codes = []
            for _ in range(11):
                response = client.post("/api/v1/pair", headers={"X-Chintu-Protocol": "1"},
                                       json={"enrollment_secret": "x" * 43, "client_name": "guess"})
                codes.append(response.status_code)
            self.assertEqual(codes, [401] * 10 + [429])
            self.assertTrue(int(response.headers["Retry-After"]) >= 1)
            self.schema(response, "Error")
            self.assertEqual(response.json()["error"]["code"], "rate_limited")
        limiter = RateLimiter(limit=2, window=60)
        self.assertEqual([limiter.retry_after(0), limiter.retry_after(1), limiter.retry_after(2)], [0, 0, 59])
        self.assertEqual(limiter.retry_after(61), 0)

    def test_enrollments_are_purged_and_capped(self):
        store = self.app.state.store
        secrets_issued = [store.create_enrollment() for _ in range(MAX_OPEN_ENROLLMENTS + 2)]
        self.assertEqual(store.open_enrollments(), MAX_OPEN_ENROLLMENTS)
        stale = self.client.post("/api/v1/pair", headers={"X-Chintu-Protocol": "1"},
                                 json={"enrollment_secret": secrets_issued[0], "client_name": "old"})
        self.assertEqual(stale.status_code, 401)
        self.pair(secrets_issued[-1])
        with store.transaction() as db:
            db.execute("UPDATE enrollments SET expires=0")
        store.create_enrollment()
        with store.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0], 1)

    def test_info_logs_carry_sizes_not_text(self):
        with self.assertLogs("chintu.task", level="DEBUG") as captured:
            _, task = self.submit("PRIVATE INSTRUCTION PHRASE")
            self.wait(task["task_id"], "succeeded")
        info = [r.getMessage() for r in captured.records if r.levelno >= logging.INFO]
        debug = [r.getMessage() for r in captured.records if r.levelno == logging.DEBUG]
        self.assertTrue(info and all("PRIVATE INSTRUCTION" not in line for line in info))
        self.assertTrue(any("instruction_words=3" in line for line in info))
        self.assertTrue(any("PRIVATE INSTRUCTION PHRASE" in line for line in debug))

    def test_log_files_become_owner_only(self):
        target = Path(self.temp.name) / "gateway.log"
        target.write_text("")
        target.chmod(0o644)
        _private_log_files(str(target))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        _private_log_files(str(target.parent / "missing.log"))

    def test_global_worker_serialization_and_cancel_before_start(self):
        _, running = self.submit("[wait]")
        self.wait(running["task_id"], "running")
        _, queued = self.submit("must not execute")
        self.assertEqual(self.get(queued["task_id"])["state"], "queued")
        cancelled = self.post(f"/api/v1/tasks/{queued['task_id']}/cancel", {"message_id": uid()})["task"]
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertIsNone(cancelled["attempt_id"])
        self.assertEqual(len(self.adapter.commands), 1)
        self.post(f"/api/v1/tasks/{running['task_id']}/cancel", {"message_id": uid()})
        self.wait(running["task_id"], "cancelled")

    def test_running_cancel_retries_and_confirmed_outcome(self):
        _, snapshot = self.submit("[wait]")
        running = self.wait(snapshot["task_id"], "running")
        route = f"/api/v1/tasks/{snapshot['task_id']}/cancel"
        value = {"message_id": uid()}
        acknowledged = self.post(route, value)
        self.assertEqual(acknowledged["task"]["state"], "cancelling")
        self.assertIsNone(acknowledged["task"]["result"])
        terminal = self.wait(snapshot["task_id"], "cancelled")
        replay = self.post(route, value)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["task"], terminal)
        self.assertEqual(terminal["attempt_id"], running["attempt_id"])
        late = self.post(route, {"message_id": uid()})
        self.assertEqual(late["task"]["task_version"], terminal["task_version"])

    def test_late_cancel_does_not_overwrite_success(self):
        _, snapshot = self.submit()
        completed = self.wait(snapshot["task_id"], "succeeded")
        returned = self.post(f"/api/v1/tasks/{snapshot['task_id']}/cancel", {"message_id": uid()})["task"]
        self.assertEqual(returned, completed)

    def test_followup_new_attempt_and_replay_before_stale_version(self):
        _, snapshot = self.submit()
        completed = self.wait(snapshot["task_id"], "succeeded")
        route = f"/api/v1/tasks/{snapshot['task_id']}/messages"
        value = {"message_id": uid(), "instruction": "explain", "expected_task_version": completed["task_version"]}
        self.post(route, value)
        following = self.wait(snapshot["task_id"], "succeeded")
        self.assertEqual(following["provider_session_id"], completed["provider_session_id"])
        self.assertNotEqual(following["attempt_id"], completed["attempt_id"])
        self.assertEqual(self.adapter.commands[-1].instruction, "explain")
        self.assertTrue(self.post(route, value)["replayed"])
        self.assertEqual(len(self.adapter.commands), 2)
        stale = self.post(route, {**value, "message_id": uid()}, 409)
        self.assertEqual(stale["error"]["code"], "stale_task_version")
        conflict = self.post(route, {**value, "instruction": "different"}, 409)
        self.assertEqual(conflict["error"]["code"], "idempotency_conflict")

    def test_active_steering_and_failed_session_followup_rejected(self):
        _, snapshot = self.submit("[wait]")
        active = self.wait(snapshot["task_id"], "running")
        self.post(f"/api/v1/tasks/{snapshot['task_id']}/messages",
                  {"message_id": uid(), "expected_task_version": active["task_version"], "instruction": "new"}, 501)
        self.post(f"/api/v1/tasks/{snapshot['task_id']}/cancel", {"message_id": uid()})
        self.wait(snapshot["task_id"], "cancelled")
        _, failure = self.submit("[fail]")
        failed = self.wait(failure["task_id"], "failed")
        self.post(f"/api/v1/tasks/{failure['task_id']}/messages",
                  {"message_id": uid(), "expected_task_version": failed["task_version"], "instruction": "retry"}, 409)

    def test_event_pages_and_empty_filtered_cursor_preservation(self):
        _, snapshot = self.submit()
        self.wait(snapshot["task_id"], "succeeded")
        cursor, events = 0, []
        while True:
            response = self.client.get(f"/api/v1/events?after={cursor}&limit=2", headers=self.headers)
            self.schema(response, "EventsPage")
            page = response.json()
            events.extend(page["events"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        self.assertEqual([e["event_id"] for e in events], sorted(set(e["event_id"] for e in events)))
        self.assertEqual([e["task_version"] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual(events[-1]["kind"], "completed")
        response = self.client.get(f"/api/v1/events?after={cursor}&task_id={uid()}", headers=self.headers)
        self.assertEqual(response.json(), {"events": [], "next_cursor": cursor, "has_more": False})
        response = self.client.get(f"/api/v1/events?after={cursor + 1}", headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.schema(response, "Error")

    def test_restart_preserves_history_and_never_relaunches_active_attempt(self):
        value, task = self.submit("[wait]")
        active = self.wait(task["task_id"], "running")
        self.client.__exit__(None, None, None)
        self.adapter = CountingAdapter()
        self.app = create_app(Settings(self.database), adapter=self.adapter)
        self.client = TestClient(self.app).__enter__()
        recovered = self.get(task["task_id"])
        self.assertEqual(recovered["state"], "interrupted")
        self.assertEqual(recovered["attempt_id"], active["attempt_id"])
        self.assertIn("Reconciliation", recovered["result"]["summary"])
        self.assertEqual(len(self.adapter.commands), 0)
        replay = self.post("/api/v1/tasks", value)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["task"], recovered)

    def test_startup_reconciles_database_left_by_unclean_worker(self):
        self.client.__exit__(None, None, None)
        store = Store(self.database)
        client_id = store.authenticate(self.headers["Authorization"][7:])
        body = {"submission_key": uid(), "provider": "scripted", "project_id": "fixture",
                "instruction": "uncertain", "execution_profile": "inspect"}
        task, _ = store.submit(client_id, body, lambda _: None)
        claimed, _ = store.claim()
        self.app = create_app(Settings(self.database), adapter=CountingAdapter())
        self.client = TestClient(self.app).__enter__()
        restored = self.get(task["task_id"])
        self.assertEqual(restored["state"], "interrupted")
        self.assertEqual(restored["attempt_id"], claimed["attempt_id"])

    def test_failed_submission_commit_is_not_accepted_or_launched(self):
        original = self.app.state.store.transaction

        @contextmanager
        def fail_commit():
            with original() as db:
                yield db
                raise sqlite3.OperationalError("injected private error")

        with patch.object(self.app.state.store, "transaction", fail_commit):
            response = self.post("/api/v1/tasks", {"submission_key": uid(), "project_id": "fixture",
                "provider": "scripted", "instruction": "no launch", "execution_profile": "inspect"}, 503)
        self.assertTrue(response["error"]["retryable"])
        self.assertEqual(self.adapter.commands, [])
        self.assertEqual(self.app.state.store.tasks_page(100)["tasks"], [])
        self.assertEqual(self.app.state.store.events_page(0, 100)["events"], [])

    def test_queue_limit_and_retry_when_full(self):
        self.app.state.store.queue_limit = 1
        _, first = self.submit("[wait]")
        self.wait(first["task_id"], "running")
        value, queued = self.submit("pending")
        full = self.post("/api/v1/tasks", {**value, "submission_key": uid()}, 429)
        self.assertEqual(full["error"]["code"], "queue_full")
        self.assertTrue(self.post("/api/v1/tasks", value)["replayed"])
        self.assertEqual(self.get(queued["task_id"])["state"], "queued")

    def test_body_field_and_page_limits_and_unimplemented_controls(self):
        response = self.client.post("/api/v1/echo", headers=self.headers, content=b" " * (64 * 1024 + 1))
        self.assertEqual(response.status_code, 413)
        self.schema(response, "Error")
        for value in ({"text": "x", "audio": "forbidden"}, {"text": "x" * 2001}):
            response = self.client.post("/api/v1/echo", headers=self.headers, json=value)
            self.assertEqual(response.status_code, 422)
            self.schema(response, "Error")
        for route in ("/api/v1/events?limit=101", "/api/v1/tasks?limit=0"):
            response = self.client.get(route, headers=self.headers)
            self.assertEqual(response.status_code, 422)
            self.schema(response, "Error")
        response = self.client.get("/api/v1/artifacts/" + uid(), headers=self.headers)
        self.assertEqual(response.status_code, 501)
        self.schema(response, "Error")
        _, task = self.submit()
        self.post(f"/api/v1/tasks/{task['task_id']}/inputs/{uid()}/answer", {"message_id": uid(),
            "expected_task_version": 1, "attempt_id": uid(), "answer": "yes"}, 501)
        self.post(f"/api/v1/tasks/{task['task_id']}/approvals/{uid()}/decision", {"message_id": uid(),
            "expected_task_version": 1, "attempt_id": uid(), "action_digest": "a" * 64, "decision": "allow"}, 501)
        self.post(f"/api/v1/tasks/{uid()}/inputs/{uid()}/answer", {"message_id": uid(),
            "expected_task_version": 1, "attempt_id": uid(), "answer": "yes"}, 404)

    def test_unsupported_provider_profile_and_path_never_launch(self):
        base = {"submission_key": uid(), "provider": "scripted", "project_id": "fixture",
                "execution_profile": "inspect", "instruction": "hello"}
        self.post("/api/v1/tasks", {**base, "provider": "claude"}, 501)
        self.post("/api/v1/tasks", {**base, "execution_profile": "unrestricted"}, 422)
        self.post("/api/v1/tasks", {**base, "project_path": "/secret"}, 422)
        self.assertEqual(self.adapter.commands, [])

    def test_default_gateway_disables_all_providers(self):
        other = create_app(Settings(Path(self.temp.name) / "disabled.sqlite3"))
        with TestClient(other) as client:
            secret = other.state.store.create_enrollment()
            pair = client.post("/api/v1/pair", headers={"X-Chintu-Protocol": "1"},
                               json={"enrollment_secret": secret, "client_name": "disabled"})
            headers = {"X-Chintu-Protocol": "1", "Authorization": "Bearer " + pair.json()["token"]}
            capabilities = client.get("/api/v1/capabilities", headers=headers)
            self.schema(capabilities, "Capabilities")
            self.assertTrue(all(not p["available"] for p in capabilities.json()["providers"]))


class WorkerBoundaryTests(unittest.TestCase):
    def test_two_workers_cannot_claim_one_database(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp) / "db")
            first, second = create_app(settings, ScriptedAdapter()), create_app(settings, ScriptedAdapter())
            with TestClient(first):
                with self.assertRaisesRegex(RuntimeError, "Another gateway worker"):
                    with TestClient(second):
                        pass

    def test_malformed_and_unbounded_output_interrupted_without_false_success(self):
        class BrokenAdapter(ScriptedAdapter):
            async def run(self, command):
                yield AdapterEvent("started", {}, uid())
                for _ in range(257):
                    yield AdapterEvent("output", {"text": "bounded individual output"})
                yield AdapterEvent("succeeded", result("must never be committed"))

        with tempfile.TemporaryDirectory() as temp:
            app = create_app(Settings(Path(temp) / "db"), BrokenAdapter())
            with TestClient(app) as client:
                secret = app.state.store.create_enrollment()
                pair = client.post("/api/v1/pair", headers={"X-Chintu-Protocol": "1"},
                                   json={"client_name": "test", "enrollment_secret": secret}).json()
                headers = {"X-Chintu-Protocol": "1", "Authorization": "Bearer " + pair["token"]}
                accepted = client.post("/api/v1/tasks", headers=headers, json={"submission_key": uid(),
                    "project_id": "fixture", "provider": "scripted", "instruction": "overflow",
                    "execution_profile": "inspect"}).json()
                task_id = accepted["task"]["task_id"]
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    snapshot = app.state.store.get(task_id)
                    if snapshot["state"] == "interrupted":
                        break
                    time.sleep(.01)
                self.assertEqual(snapshot["state"], "interrupted")
                self.assertNotIn("must never", json.dumps(snapshot))
                events = app.state.store.events_page(0, 100)["events"]
                for event in events:
                    app.state.contract.validate("Event", event)


if __name__ == "__main__":
    unittest.main()
