"""Check the shared wire contract against the independently built gateway."""

import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from chintu_adapters import ScriptedAdapter
from chintu_gateway import Settings, create_app


ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "contracts/openapi.json").read_text())


class WireContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(
            Settings(database=Path(self.temp.name) / "state.sqlite"),
            adapter=ScriptedAdapter(),
        )
        self.client = self.enterContext(TestClient(self.app))
        enrollment = self.app.state.store.create_enrollment()
        response = self.client.post(
            "/api/v1/pair",
            headers={"X-Chintu-Protocol": "1"},
            json={"enrollment_secret": enrollment, "client_name": "contract-test"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.validate("PairResponse", response.json())
        self.headers = {
            "X-Chintu-Protocol": "1",
            "Authorization": f"Bearer {response.json()['token']}",
        }

    def validate(self, name, value):
        Draft202012Validator(
            {"$ref": f"#/components/schemas/{name}", "components": SPEC["components"]},
            format_checker=FormatChecker(),
        ).validate(value)

    def submit(self, instruction="Summarize this project", key=None):
        request = {
            "submission_key": key or str(uuid.uuid4()),
            "project_id": "fixture",
            "provider": "scripted",
            "instruction": instruction,
            "execution_profile": "inspect",
            "conversation_id": str(uuid.uuid4()),
        }
        response = self.client.post("/api/v1/tasks", headers=self.headers, json=request)
        self.assertEqual(response.status_code, 202, response.text)
        self.validate("TaskAccepted", response.json())
        return request, response.json()["task"]

    def await_state(self, task_id, states):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/v1/tasks/{task_id}", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            task = response.json()
            self.validate("TaskSnapshot", task)
            if task["state"] in states:
                return task
            time.sleep(0.01)
        self.fail(f"Task did not reach {states}; last state: {task['state']}")

    def test_discovery_echo_and_capabilities_match_contract(self):
        for route, schema in (
            ("/health", "Health"),
            ("/api/v1/info", "Info"),
            ("/api/v1/projects", "Projects"),
            ("/api/v1/capabilities", "Capabilities"),
        ):
            response = self.client.get(route, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.validate(schema, response.json())
        response = self.client.post("/api/v1/echo", headers=self.headers, json={"text": "Hello"})
        self.assertEqual(response.status_code, 200)
        self.validate("EchoResponse", response.json())
        self.assertEqual(response.json()["mode"], "echo")

    def test_submission_retry_preserves_task_and_rejects_changed_body(self):
        request, task = self.submit()
        response = self.client.post("/api/v1/tasks", headers=self.headers, json=request)
        self.assertEqual(response.status_code, 200, response.text)
        self.validate("TaskAccepted", response.json())
        self.assertTrue(response.json()["replayed"])
        self.assertEqual(response.json()["task"]["task_id"], task["task_id"])
        response = self.client.post(
            "/api/v1/tasks", headers=self.headers,
            json={**request, "instruction": "Changed instruction"},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.validate("Error", response.json())
        self.assertEqual(response.json()["error"]["code"], "idempotency_conflict")

    def test_result_and_event_replay_match_contract(self):
        _, initial = self.submit()
        task = self.await_state(initial["task_id"], {"succeeded"})
        self.assertTrue(task["result"]["speech_text"].startswith("Scripted reply:"))
        response = self.client.get("/api/v1/tasks", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.validate("TasksPage", response.json())
        response = self.client.get("/api/v1/events?after=0&limit=100", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        page = response.json()
        self.validate("EventsPage", page)
        self.assertEqual(page["events"][-1]["kind"], "completed")
        self.assertEqual(page["next_cursor"], page["events"][-1]["event_id"])
        replay = self.client.get(
            f"/api/v1/events?after={page['next_cursor']}", headers=self.headers,
        )
        self.validate("EventsPage", replay.json())
        self.assertEqual(replay.json()["events"], [])

    def test_follow_up_preserves_session_and_mutation_retry(self):
        _, initial = self.submit()
        finished = self.await_state(initial["task_id"], {"succeeded"})
        request = {
            "message_id": str(uuid.uuid4()),
            "expected_task_version": finished["task_version"],
            "instruction": "Explain the summary",
        }
        route = f"/api/v1/tasks/{finished['task_id']}/messages"
        response = self.client.post(route, headers=self.headers, json=request)
        self.assertEqual(response.status_code, 200, response.text)
        self.validate("TaskAccepted", response.json())
        followed = self.await_state(finished["task_id"], {"succeeded"})
        self.assertEqual(followed["provider_session_id"], finished["provider_session_id"])
        self.assertNotEqual(followed["attempt_id"], finished["attempt_id"])
        response = self.client.post(route, headers=self.headers, json=request)
        self.assertEqual(response.status_code, 200, response.text)
        self.validate("TaskAccepted", response.json())
        self.assertTrue(response.json()["replayed"])

    def test_cancel_is_confirmed_by_terminal_state(self):
        _, initial = self.submit("[wait]")
        task = self.await_state(initial["task_id"], {"running"})
        response = self.client.post(
            f"/api/v1/tasks/{task['task_id']}/cancel", headers=self.headers,
            json={"message_id": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.validate("TaskAccepted", response.json())
        cancelled = self.await_state(task["task_id"], {"cancelled"})
        self.assertEqual(cancelled["result"]["partial_effects"], [])

    def test_audio_payload_and_revoked_client_are_rejected(self):
        response = self.client.post(
            "/api/v1/echo", headers=self.headers,
            json={"text": "Hello", "audio": "unexpected"},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.validate("Error", response.json())
        response = self.client.delete("/api/v1/clients/me", headers=self.headers)
        self.assertEqual(response.status_code, 204, response.text)
        response = self.client.get("/api/v1/tasks", headers=self.headers)
        self.assertEqual(response.status_code, 401, response.text)
        self.validate("Error", response.json())


if __name__ == "__main__":
    unittest.main()
