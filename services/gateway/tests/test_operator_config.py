import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from chintu_gateway import Settings, Store, create_app
from chintu_gateway.cli import main
from chintu_gateway.config import load_config
from chintu_gateway.errors import ApiError
from chintu_gateway.auth_env import assert_auth_environment
from starlette.testclient import TestClient


def uid():
    return str(uuid4())


class OperatorConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "fake-provider"
        fixture = Path(__file__).resolve().parents[3] / "packages/agent-adapters/tests/fixture_runtime.py"
        self.runtime.write_text(fixture.read_text())
        self.runtime.chmod(0o700)
        self.review = self.root / "fixture-review.md"
        self.review.write_text("Fixture subprocess review only. No real account, confinement or billing evidence.\n")
        self.config_path = self.root / "gateway.json"
        self.evidence = {
            "version": "1.2.3", "execution_profiles": ["inspect"],
            "auth_verified": True, "protocol_verified": True, "confinement_verified": True,
            "cancellation_verified": True, "continuation_verified": True, "record": "fixture-review.md"
        }
        self.value = {
            "version": 1, "database": "db.sqlite3", "queue_limit": 10,
            "projects": [{"project_id": "local", "display_name": "Fixture workspace",
                          "path": str(self.root), "execution_profiles": ["inspect"]}],
            "runtimes": {name: {"enabled": False, "executable": str(self.runtime),
                               "login_method": None, "evidence": None} for name in ("claude", "codex")}
        }

    def load(self, value=None):
        self.config_path.write_text(json.dumps(self.value if value is None else value))
        return load_config(self.config_path)

    def selected(self, provider="codex"):
        self.value["runtimes"][provider].update(enabled=True, login_method="api_key", evidence=self.evidence)
        settings, adapter = self.load().selected(provider)
        adapter.stop_seconds = .05
        return settings, adapter

    def client(self, app):
        client = TestClient(app).__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        secret = app.state.store.create_enrollment()
        paired = client.post("/api/v1/pair", headers={"X-Chintu-Protocol": "1"},
                             json={"client_name": "Local fake-runtime test", "enrollment_secret": secret})
        self.assertEqual(paired.status_code, 200)
        headers = {"X-Chintu-Protocol": "1", "Authorization": "Bearer " + paired.json()["token"]}
        return client, headers

    def submit(self, client, headers, text="hello", expected=202, project="local", provider="codex"):
        response = client.post("/api/v1/tasks", headers=headers, json={"submission_key": uid(),
            "provider": provider, "project_id": project, "execution_profile": "inspect", "instruction": text})
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def wait(self, app, task_id, state):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            snapshot = app.state.store.get(task_id)
            if snapshot["state"] == state:
                app.state.contract.validate("TaskSnapshot", snapshot)
                return snapshot
            time.sleep(.01)
        self.fail(f"Expected {state}; got {snapshot['state']}")

    def test_example_is_disabled_and_load_does_not_probe_or_create_database(self):
        with patch("asyncio.create_subprocess_exec", side_effect=AssertionError("config must not probe")):
            config = self.load()
            self.assertFalse(config.settings.database.exists())
            self.assertTrue(all(not r["enabled"] for r in config.runtimes.values()))
            with self.assertRaisesRegex(ValueError, "not explicitly enabled"):
                config.selected("codex")
        example = Path(__file__).resolve().parents[3] / "config/gateway.example.json"
        self.assertTrue(all(not r["enabled"] for r in load_config(example).runtimes.values()))

    def test_reject_unknown_fields_types_paths_profiles_and_credential_fields(self):
        mutations = [
            lambda v: v.update(secret_key="not accepted"),
            lambda v: v.update(version=True),
            lambda v: v.update(queue_limit=0),
            lambda v: v["projects"][0].update(path="relative/path"),
            lambda v: v["projects"][0].update(execution_profiles=["unrestricted"]),
            lambda v: v["projects"].append(deepcopy(v["projects"][0])),
            lambda v: v["runtimes"]["codex"].update(enabled="true"),
            lambda v: v["runtimes"]["codex"].update(api_key="not accepted"),
            lambda v: v["runtimes"]["codex"].update(login_method="auto"),
        ]
        for mutate in mutations:
            value = deepcopy(self.value)
            mutate(value)
            with self.subTest(value=value), self.assertRaises((ValueError, ApiError)):
                self.load(value)

    def test_enabled_flag_or_login_choice_cannot_replace_evidence(self):
        self.value["runtimes"]["codex"].update(enabled=True, login_method="api_key")
        with self.assertRaisesRegex(ValueError, "reviewed runtime evidence"):
            self.load().selected("codex")
        self.value["runtimes"]["codex"]["evidence"] = self.evidence
        for flag in ("auth_verified", "protocol_verified", "confinement_verified", "cancellation_verified"):
            self.evidence[flag] = False
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "unavailable"):
                self.load().selected("codex")
            self.evidence[flag] = True
        self.value["runtimes"]["codex"]["login_method"] = None
        with self.assertRaisesRegex(ValueError, "login method"):
            self.load().selected("codex")

    def test_review_file_and_available_runtime_required_before_app(self):
        settings, adapter = self.selected()
        self.assertTrue(adapter.capabilities().available)
        self.review.write_text("   \n")
        with self.assertRaisesRegex(ValueError, "nonempty"):
            create_app(settings, adapter)
        with self.assertRaisesRegex(ValueError, "nonempty"):
            self.load().selected("codex")
        self.review.write_text("fixture-only review")
        with self.assertRaisesRegex(ValueError, "reviewed"):
            create_app(replace(settings, runtime_evidence=None), adapter)
        with self.assertRaisesRegex(ValueError, "reviewed"):
            create_app(replace(settings, login_method=None), adapter)
        mismatched = replace(settings.runtime_evidence, version="9.9.9")
        with self.assertRaisesRegex(ValueError, "reviewed"):
            create_app(replace(settings, runtime_evidence=mismatched), adapter)
        self.runtime.unlink()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.load().selected("codex")

    def test_both_fake_local_provider_subprocesses_succeed_and_followup(self):
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                settings, adapter = self.selected(provider)
                settings = replace(settings, database=self.root / (provider + ".sqlite3"))
                app = create_app(settings, adapter)
                client, headers = self.client(app)
                # Metacharacters remain structured instruction data; no shell executes them.
                text = "hello; $(touch BAD) `echo no`"
                task = self.submit(client, headers, text, provider=provider)["task"]
                first = self.wait(app, task["task_id"], "succeeded")
                self.assertFalse((self.root / "BAD").exists())
                self.assertIn(text, first["result"]["summary"])
                followup = client.post(f"/api/v1/tasks/{task['task_id']}/messages", headers=headers,
                    json={"message_id": uid(), "expected_task_version": first["task_version"], "instruction": "follow"})
                self.assertEqual(followup.status_code, 200, followup.text)
                app.state.contract.validate("TaskAccepted", followup.json())
                second = self.wait(app, task["task_id"], "succeeded")
                self.assertEqual(second["provider_session_id"], first["provider_session_id"])
                self.assertNotEqual(second["attempt_id"], first["attempt_id"])
                self.assertEqual(adapter._processes, {})
                for event in app.state.store.events_page(0, 100)["events"]:
                    app.state.contract.validate("Event", event)

    def test_fake_runtime_cancel_reaps_process_before_terminal(self):
        settings, adapter = self.selected()
        app = create_app(settings, adapter)
        client, headers = self.client(app)
        task = self.submit(client, headers, "[hang]")["task"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not adapter._processes:
            time.sleep(.01)
        self.assertTrue(adapter._processes)
        process = next(iter(adapter._processes.values()))
        response = client.post(f"/api/v1/tasks/{task['task_id']}/cancel", headers=headers, json={"message_id": uid()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task"]["state"], "cancelling")
        terminal = self.wait(app, task["task_id"], "cancelled")
        self.assertIsNotNone(process.returncode)
        self.assertTrue(terminal["result"]["partial_effects"])
        self.assertEqual(app.state.store.workspace_blocks(), [])

    def test_unconfirmed_provider_death_quarantines_canonical_workspace(self):
        settings, adapter = self.selected()
        projects = {**settings.projects, "alias": {**settings.projects["local"], "display_name": "Same path alias"}}
        app = create_app(replace(settings, projects=projects), adapter)
        client, headers = self.client(app)
        task = self.submit(client, headers, "[death]")["task"]
        interrupted = self.wait(app, task["task_id"], "interrupted")
        self.assertTrue(interrupted["result"]["partial_effects"])
        self.assertEqual(len(app.state.store.workspace_blocks()), 1)
        for project in ("local", "alias"):
            denied = self.submit(client, headers, "new task", expected=409, project=project)
            app.state.contract.validate("Error", denied)
            self.assertEqual(denied["error"]["code"], "invalid_state")

    def test_unclean_restart_keeps_queued_real_work_blocked_until_local_review(self):
        settings, adapter = self.selected()
        app = create_app(replace(settings, enable_worker=False), adapter)
        client, headers = self.client(app)
        active = self.submit(client, headers, "uncertain prior launch")["task"]
        queued = self.submit(client, headers, "queued work")["task"]
        claimed, _ = app.state.store.claim("codex")
        self.assertEqual(claimed["task_id"], active["task_id"])
        client.__exit__(None, None, None)
        # No OS process launched above: this simulates durable metadata left by a crash.
        restored = create_app(settings, adapter)
        recovered_client, recovered_headers = self.client(restored)
        interrupted = restored.state.store.get(active["task_id"])
        self.assertEqual(interrupted["state"], "interrupted")
        self.assertEqual(restored.state.store.get(queued["task_id"])["state"], "queued")
        self.assertEqual(adapter._processes, {})
        events_before = restored.state.store.events_page(0, 100)["events"]
        # Release is an offline operator assertion, never an HTTP request.
        args = ["chintu-gateway", "--config", str(self.config_path), "reconcile", "--task-id", active["task_id"],
                "--confirmed-stopped", "--record", str(self.review)]
        with patch("sys.argv", args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(error.exception.code, 2)
        recovered_client.__exit__(None, None, None)
        with patch("sys.argv", args), contextlib.redirect_stdout(io.StringIO()) as output:
            main()
        self.assertFalse(json.loads(output.getvalue())["task_relaunched"])
        store = Store(settings.database)
        self.assertEqual(store.workspace_blocks(), [])
        self.assertEqual(store.get(active["task_id"]), interrupted)
        self.assertEqual(store.events_page(0, 100)["events"], events_before)
        restarted = create_app(settings, adapter)
        with TestClient(restarted):
            self.wait(restarted, queued["task_id"], "succeeded")
            self.assertEqual(restarted.state.store.get(active["task_id"])["state"], "interrupted")

    def test_change_selected_provider_leaves_other_queued_provider_unlaunched(self):
        settings, codex = self.selected("codex")
        app = create_app(replace(settings, enable_worker=False), codex)
        client, headers = self.client(app)
        queued = self.submit(client, headers)["task"]
        client.__exit__(None, None, None)
        _, claude = self.selected("claude")
        other = create_app(settings, claude)
        with TestClient(other):
            time.sleep(.05)
            self.assertEqual(other.state.store.get(queued["task_id"])["state"], "queued")
            self.assertEqual(claude._processes, {})

    def test_check_config_cli_has_no_provider_side_effects(self):
        self.load()
        with patch("sys.argv", ["chintu-gateway", "--config", str(self.config_path), "check-config"]), \
                patch("asyncio.create_subprocess_exec", side_effect=AssertionError("no probe")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            main()
        value = json.loads(output.getvalue())
        self.assertFalse(value["runtime_verified"])
        self.assertFalse(value["inference_performed"])
        self.assertFalse((self.root / "db.sqlite3").exists())

    def test_auth_environment_override_rejected_for_both_saved_login_methods(self):
        cases = {"codex": ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_IDENTITY_TOKEN_FILE", "CODEX_HOME"),
                 "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
                            "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_FOUNDRY_BASE_URL", "CLAUDE_CONFIG_DIR")}
        for provider, names in cases.items():
            with patch.dict(os.environ, {}, clear=True):
                settings, adapter = self.selected(provider)
                for login_method in ("subscription", "api_key"):
                    self.value["runtimes"][provider]["login_method"] = login_method
                    config = self.load()
                    for name in names:
                        with self.subTest(provider=provider, method=login_method, name=name), \
                                patch.dict(os.environ, {name: "DO_NOT_READ_OR_REPORT_THIS_VALUE"}):
                            for action in (lambda: config.selected(provider),
                                           lambda: create_app(replace(settings, login_method=login_method), adapter)):
                                with self.assertRaisesRegex(ValueError, "environment override") as error:
                                    action()
                                self.assertNotIn("DO_NOT_READ", str(error.exception))
                # Presence, including an empty value, is enough to reject.
                with patch.dict(os.environ, {names[0]: ""}), self.assertRaises(ValueError):
                    config.selected(provider)

    def test_auth_guard_does_not_read_environment_values(self):
        class NamesOnly(dict):
            def __getitem__(self, key):
                raise AssertionError("Secret value was read")

        with self.assertRaises(ValueError):
            assert_auth_environment("codex", NamesOnly(CODEX_API_KEY=object()))
        assert_auth_environment("scripted", NamesOnly(CODEX_API_KEY=object()))


if __name__ == "__main__":
    unittest.main()


class AgentRuntimeConfigTests(unittest.TestCase):
    """The claude-agent runtime block: explicit enable plus profiles, no CLI protocol evidence."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.executable = self.root / "claude"
        self.executable.write_text("#!/bin/sh\nexit 0\n")
        self.executable.chmod(0o700)
        self.config_path = self.root / "gateway.json"
        self.agent = {"enabled": True, "executable": str(self.executable), "model": "claude-opus-5-5",
                      "profiles": ["inspect", "develop"], "load_user_settings": True}
        self.value = {
            "version": 1, "database": "db.sqlite3", "queue_limit": 10,
            "projects": [{"project_id": "local", "display_name": "Fixture workspace",
                          "path": str(self.root), "execution_profiles": ["inspect", "develop", "desktop"]}],
            "runtimes": {"claude-agent": self.agent}
        }
        fixture = Path(__file__).resolve().parents[3] / "packages/agent-adapters/tests/fake_agent_sdk.py"
        spec = importlib.util.spec_from_file_location("fake_agent_sdk", fixture)
        self.fake = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fake)
        patcher = patch.dict(sys.modules, {"claude_agent_sdk": self.fake})
        patcher.start()
        self.addCleanup(patcher.stop)

    def load(self, value=None):
        self.config_path.write_text(json.dumps(self.value if value is None else value))
        return load_config(self.config_path)

    def client(self, app):
        client = TestClient(app).__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        secret = app.state.store.create_enrollment()
        paired = client.post("/api/v1/pair", headers={"X-Chintu-Protocol": "1"},
                             json={"client_name": "Agent config test", "enrollment_secret": secret})
        headers = {"X-Chintu-Protocol": "1", "Authorization": "Bearer " + paired.json()["token"]}
        return client, headers

    def wait(self, app, task_id, state):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            snapshot = app.state.store.get(task_id)
            if snapshot["state"] == state:
                app.state.contract.validate("TaskSnapshot", snapshot)
                return snapshot
            time.sleep(.01)
        self.fail(f"Expected {state}; got {snapshot['state']}")

    def test_example_block_is_disabled_and_validates(self):
        example = Path(__file__).resolve().parents[3] / "config/gateway.example.json"
        config = load_config(example)
        self.assertFalse(config.runtimes["claude-agent"]["enabled"])
        self.assertEqual(config.runtimes["claude-agent"]["profiles"], ["inspect", "develop"])
        with self.assertRaisesRegex(ValueError, "not explicitly enabled"):
            config.selected("claude-agent")

    def test_invalid_agent_blocks_are_rejected(self):
        mutations = [
            lambda a: a.update(login_method="subscription"),
            lambda a: a.update(api_key="not accepted"),
            lambda a: a.update(enabled="yes"),
            lambda a: a.update(profiles=["unrestricted"]),
            lambda a: a.update(profiles=[]),
            lambda a: a.update(profiles=["inspect", "inspect"]),
            lambda a: a.update(model=""),
            lambda a: a.update(load_user_settings=1),
            lambda a: a.update(max_turns=0),
            lambda a: a.update(timeout_seconds=5),
            lambda a: a.pop("profiles"),
        ]
        for mutate in mutations:
            value = deepcopy(self.value)
            mutate(value["runtimes"]["claude-agent"])
            with self.subTest(value=value["runtimes"]["claude-agent"]), self.assertRaises((ValueError, ApiError)):
                self.load(value)
        value = deepcopy(self.value)
        value["projects"][0]["execution_profiles"] = ["desktop", "desktop"]
        with self.assertRaises((ValueError, ApiError)):
            self.load(value)

    def test_token_ttl_days_is_optional_and_bounded(self):
        self.assertEqual(self.load().settings.token_ttl_days, 30)
        self.value["token_ttl_days"] = 7
        self.assertEqual(self.load().settings.token_ttl_days, 7)
        for bad in (0, 366, "30", 1.5, None):
            self.value["token_ttl_days"] = bad
            with self.assertRaises(ValueError):
                self.load()

    def test_check_config_reports_enabled_agent_runtime(self):
        self.load()
        with patch("sys.argv", ["chintu-gateway", "--config", str(self.config_path), "check-config"]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            main()
        value = json.loads(output.getvalue())
        self.assertEqual(value["enabled_runtimes"], ["claude-agent"])
        self.assertFalse(value["inference_performed"])
        self.assertEqual(self.fake.clients, [])

    def test_selected_adapter_reflects_config_and_keeps_desktop_opt_in(self):
        settings, adapter = self.load().selected("claude-agent")
        self.assertEqual(adapter.capabilities().provider, "claude-agent")
        self.assertEqual(adapter.capabilities().execution_profiles, ("inspect", "develop"))
        self.assertEqual(adapter.model, "claude-opus-5-5")
        self.assertTrue(adapter.load_user_settings)
        self.assertIsNone(settings.runtime_evidence)
        self.agent.update(profiles=["inspect", "develop", "desktop"], max_turns=12, timeout_seconds=120,
                          load_user_settings=False)
        _, adapter = self.load().selected("claude-agent")
        self.assertIn("desktop", adapter.capabilities().execution_profiles)
        self.assertEqual((adapter.max_turns, adapter.timeout_seconds, adapter.load_user_settings), (12, 120, False))

    def test_unavailable_runtime_or_env_override_blocks_selection(self):
        self.executable.unlink()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.load().selected("claude-agent")
        self.executable.write_text("#!/bin/sh\nexit 0\n")
        self.executable.chmod(0o700)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "DO_NOT_READ"}), \
                self.assertRaisesRegex(ValueError, "environment override") as error:
            self.load().selected("claude-agent")
        self.assertNotIn("DO_NOT_READ", str(error.exception))
        with patch.dict(sys.modules, {"claude_agent_sdk": None}), self.assertRaisesRegex(ValueError, "not installed"):
            self.load().selected("claude-agent")

    def test_app_runs_agent_task_followup_and_rejects_unlisted_desktop(self):
        settings, adapter = self.load().selected("claude-agent")
        adapter.stop_seconds = .2
        app = create_app(settings, adapter)
        client, headers = self.client(app)
        capabilities = client.get("/api/v1/capabilities", headers=headers).json()["providers"]
        app.state.contract.validate("Capabilities", {"providers": capabilities})
        enabled = {p["provider"]: p["available"] for p in capabilities}
        self.assertEqual(enabled, {"claude": False, "codex": False, "claude-agent": True, "scripted": False})
        body = {"submission_key": uid(), "provider": "claude-agent", "project_id": "local",
                "execution_profile": "desktop", "instruction": "hello; $(touch BAD)"}
        self.assertEqual(client.post("/api/v1/tasks", headers=headers, json=body).status_code, 501)
        body.update(submission_key=uid(), execution_profile="develop")
        response = client.post("/api/v1/tasks", headers=headers, json=body)
        self.assertEqual(response.status_code, 202, response.text)
        task = response.json()["task"]
        first = self.wait(app, task["task_id"], "succeeded")
        self.assertFalse((self.root / "BAD").exists())
        self.assertEqual(first["provider_session_id"], self.fake.SESSION)
        self.assertIn("hello; $(touch BAD)", first["result"]["summary"])
        self.assertEqual(self.fake.clients[-1].prompt, "hello; $(touch BAD)")
        self.assertEqual(self.fake.clients[-1].options.kwargs["permission_mode"], "dontAsk")
        self.assertEqual(self.fake.clients[-1].options.kwargs["allowed_tools"], [])
        self.assertIn("PreToolUse", self.fake.clients[-1].options.kwargs["hooks"])
        followup = client.post(f"/api/v1/tasks/{task['task_id']}/messages", headers=headers,
            json={"message_id": uid(), "expected_task_version": first["task_version"], "instruction": "follow"})
        self.assertEqual(followup.status_code, 200, followup.text)
        second = self.wait(app, task["task_id"], "succeeded")
        self.assertEqual(second["provider_session_id"], first["provider_session_id"])
        self.assertEqual(self.fake.clients[-1].options.kwargs["resume"], self.fake.SESSION)
        for event in app.state.store.events_page(0, 100)["events"]:
            app.state.contract.validate("Event", event)
        self.assertEqual(adapter._clients, {})
