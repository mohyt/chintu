import os
import tempfile
import time
import unittest
from pathlib import Path
from uuid import uuid4

from chintu_gateway import Settings, create_app
from starlette.testclient import TestClient

from test_gateway import CountingAdapter


def uid():
    return str(uuid4())


class ConversationTests(unittest.TestCase):
    """Voice and chat turns share one conversation: one session, one working directory."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "workspace"
        for folder in ("docs/releases", "src", ".git", "notes"):
            (self.root / folder).mkdir(parents=True)
        (self.root / "README.md").write_text("hello")
        (base / "outside").mkdir()
        (self.root / "escape").symlink_to(base / "outside")
        (self.root / "inner-link").symlink_to(self.root / "src")
        self.adapter = CountingAdapter()
        self.app = create_app(Settings(base / "gateway.sqlite3", projects={
            "fixture": {"display_name": "Fixture", "path": self.root, "execution_profiles": ["inspect", "develop"]}}),
            adapter=self.adapter)
        self.client = TestClient(self.app).__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = self.pair()

    def pair(self):
        response = self.client.post("/api/v1/pair", headers={"X-Chintu-Protocol": "1"}, json={
            "enrollment_secret": self.app.state.store.create_enrollment(), "client_name": "Phone"})
        return {"X-Chintu-Protocol": "1", "Authorization": "Bearer " + response.json()["token"]}

    def call(self, method, route, expected=200, schema=None, headers=None, **kwargs):
        response = self.client.request(method, route, headers=headers or self.headers, **kwargs)
        self.assertEqual(response.status_code, expected, response.text)
        if response.content:
            self.app.state.contract.validate("Error" if expected >= 400 else schema, response.json())
        return response.json() if response.content else None

    def create(self, working_directory="", expected=201, **overrides):
        value = {"conversation_id": uid(), "project_id": "fixture", "provider": "scripted",
                 "execution_profile": "inspect", "working_directory": working_directory, **overrides}
        return value, self.call("POST", "/api/v1/conversations", expected, "ConversationCreated", json=value)

    def turn(self, conversation_id, instruction, expected=202, profile="inspect"):
        value = {"submission_key": uid(), "project_id": "fixture", "provider": "scripted", "execution_profile": profile,
                 "instruction": instruction, "conversation_id": conversation_id}
        return self.call("POST", "/api/v1/tasks", expected, "TaskAccepted", json=value)

    def finished(self, task_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snapshot = self.call("GET", "/api/v1/tasks/" + task_id, schema="TaskSnapshot")
            if snapshot["state"] == "succeeded":
                return snapshot
            time.sleep(.01)
        self.fail("Turn did not finish: " + snapshot["state"])

    def test_turns_resume_one_session_inside_the_chosen_directory(self):
        value, created = self.create("docs/releases/")
        conversation = created["conversation"]
        self.assertEqual((conversation["working_directory"], conversation["state"], conversation["turns"]),
                         ("docs/releases", "idle", 0))
        self.assertIsNone(conversation["title"])
        first = self.finished(self.turn(value["conversation_id"], "What changed in the last release?")["task"]["task_id"])
        second = self.finished(self.turn(value["conversation_id"], "And before that?")["task"]["task_id"])
        sub = str((self.root / "docs" / "releases").resolve())
        self.assertEqual([command.project_path for command in self.adapter.commands], [sub, sub])
        self.assertIsNone(self.adapter.commands[0].provider_session_id)
        session = first["provider_session_id"]
        self.assertEqual(self.adapter.commands[1].provider_session_id, session)
        self.assertEqual(second["provider_session_id"], session)
        current = self.call("GET", "/api/v1/conversations/" + value["conversation_id"], schema="Conversation")
        self.assertEqual((current["provider_session_id"], current["turns"], current["state"], current["last_task_id"]),
                         (session, 2, "idle", second["task_id"]))
        self.assertEqual(current["title"], "What changed in the last release?")
        self.assertEqual(current["last_text"], "Scripted reply: And before that?")
        turns = self.call("GET", f"/api/v1/conversations/{value['conversation_id']}/tasks?limit=1", schema="TasksPage")
        self.assertEqual([task["instruction"] for task in turns["tasks"]], ["What changed in the last release?"])
        rest = self.call("GET", f"/api/v1/conversations/{value['conversation_id']}/tasks?cursor={turns['next_cursor']}",
                         schema="TasksPage")
        self.assertEqual([task["instruction"] for task in rest["tasks"]], ["And before that?"])

    def test_creation_is_idempotent_and_owned(self):
        value, first = self.create("src")
        replay = self.call("POST", "/api/v1/conversations", 200, "ConversationCreated", json=value)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["conversation"]["conversation_id"], first["conversation"]["conversation_id"])
        self.call("POST", "/api/v1/conversations", 409, json={**value, "working_directory": "docs"})
        self.finished(self.turn(value["conversation_id"], "first words become the title")["task"]["task_id"])
        retried = self.call("POST", "/api/v1/conversations", 200, "ConversationCreated", json=value)
        self.assertEqual(retried["conversation"]["title"], "first words become the title")
        other = self.pair()
        self.call("POST", "/api/v1/conversations", 409, headers=other, json=value)
        self.call("GET", "/api/v1/conversations/" + value["conversation_id"], 404, headers=other)
        self.call("GET", f"/api/v1/conversations/{value['conversation_id']}/tasks", 404, headers=other)
        response = self.client.post("/api/v1/tasks", headers=other, json={
            "submission_key": uid(), "project_id": "fixture", "provider": "scripted", "execution_profile": "inspect",
            "instruction": "hijack", "conversation_id": value["conversation_id"]})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.call("GET", "/api/v1/conversations", headers=other, schema="ConversationsPage")["conversations"], [])

    def test_working_directory_must_stay_inside_the_workspace(self):
        for path, status in (("../outside", 422), ("/etc", 422), ("~/x", 422), ("escape", 422),
                             ("missing", 404), ("README.md", 404), ("a\\b", 422)):
            with self.subTest(path=path):
                self.create(path, status)
        self.assertEqual(self.create("inner-link")[1]["conversation"]["working_directory"], "src")
        self.assertEqual(self.create("./")[1]["conversation"]["working_directory"], "")
        self.create(expected=404, project_id="unknown")
        self.create(expected=501, execution_profile="desktop")

    def test_unknown_conversation_id_starts_one_at_the_workspace_root(self):
        conversation_id = uid()
        task = self.finished(self.turn(conversation_id, "Summarize\n this   project, please, in great detail and with care for every single file")["task"]["task_id"])
        self.assertEqual(task["conversation_id"], conversation_id)
        self.assertEqual(self.adapter.commands[0].project_path, str(self.root.resolve()))
        listed = self.call("GET", "/api/v1/conversations", schema="ConversationsPage")["conversations"]
        self.assertEqual([(c["conversation_id"], c["working_directory"], c["turns"]) for c in listed], [(conversation_id, "", 1)])
        self.assertEqual(listed[0]["title"], "Summarize this project, please, in great detail and with ca…")
        self.assertEqual(len(listed[0]["title"]), 60)

    def test_a_conversation_keeps_its_profile_and_needs_its_directory(self):
        value, _ = self.create("src")
        failure = self.turn(value["conversation_id"], "edit things", 409, profile="develop")
        self.assertIn("start a new one", failure["error"]["message"])
        (self.root / "src").rmdir()
        failure = self.turn(value["conversation_id"], "still there?", 409)
        self.assertIn("no longer available", failure["error"]["message"])
        self.assertEqual(self.adapter.commands, [])

    def test_list_is_most_recent_first_rename_and_archive(self):
        ids = [self.create()[0]["conversation_id"] for _ in range(3)]
        self.finished(self.turn(ids[0], "bump the oldest")["task"]["task_id"])
        page = self.call("GET", "/api/v1/conversations?limit=2", schema="ConversationsPage")
        self.assertEqual(page["conversations"][0]["conversation_id"], ids[0])
        rest = self.call("GET", "/api/v1/conversations?limit=2&cursor=" + page["next_cursor"], schema="ConversationsPage")
        self.assertEqual(len(page["conversations"] + rest["conversations"]), 3)
        self.assertIsNone(rest["next_cursor"])
        self.assertEqual(set(c["conversation_id"] for c in page["conversations"] + rest["conversations"]), set(ids))
        self.call("GET", "/api/v1/conversations?cursor=bogus", 400)
        renamed = self.call("PATCH", "/api/v1/conversations/" + ids[1], schema="Conversation", json={"title": " Trip plan "})
        self.assertEqual(renamed["title"], "Trip plan")
        self.call("PATCH", "/api/v1/conversations/" + ids[1], 422, json={"title": "   "})
        busy = self.turn(ids[2], "[wait]")["task"]
        self.call("DELETE", "/api/v1/conversations/" + ids[2], 409)
        self.call("POST", f"/api/v1/tasks/{busy['task_id']}/cancel", schema="TaskAccepted", json={"message_id": uid()})
        self.assertEqual(self.client.delete("/api/v1/conversations/" + ids[1], headers=self.headers).status_code, 204)
        listed = [c["conversation_id"] for c in self.call("GET", "/api/v1/conversations", schema="ConversationsPage")["conversations"]]
        self.assertNotIn(ids[1], listed)
        self.assertIn("archived", self.turn(ids[1], "hello again", 409)["error"]["message"])

    def test_directories_list_visible_folders_only(self):
        top = self.call("GET", "/api/v1/directories?project_id=fixture", schema="Directories")
        self.assertEqual((top["path"], top["parent"], top["truncated"]), ("", None, False))
        # Hidden folders, files and links that leave the workspace are left out; inner links show their target.
        self.assertEqual([(e["name"], e["path"]) for e in top["entries"]],
                         [("docs", "docs"), ("inner-link", "src"), ("notes", "notes"), ("src", "src")])
        nested = self.call("GET", "/api/v1/directories?project_id=fixture&path=docs", schema="Directories")
        self.assertEqual((nested["parent"], [e["path"] for e in nested["entries"]]), ("", ["docs/releases"]))
        deeper = self.call("GET", "/api/v1/directories?project_id=fixture&path=docs/releases", schema="Directories")
        self.assertEqual((deeper["parent"], deeper["entries"]), ("docs", []))
        self.call("GET", "/api/v1/directories?project_id=fixture&path=../outside", 422)
        self.call("GET", "/api/v1/directories?project_id=fixture&path=escape", 422)
        self.call("GET", "/api/v1/directories?project_id=nope", 404)
        self.call("GET", "/api/v1/directories", 404)

    def test_tools_endpoint_reports_the_adapter_catalog(self):
        catalog = self.call("GET", "/api/v1/tools?project_id=fixture&execution_profile=inspect&working_directory=src",
                            schema="ToolCatalog")
        self.assertEqual((catalog["provider"], catalog["source"], catalog["tools"]), ("scripted", "static", []))
        self.call("GET", "/api/v1/tools?project_id=fixture&execution_profile=desktop", 501)
        self.call("GET", "/api/v1/tools?project_id=fixture&execution_profile=inspect&working_directory=..", 422)
        self.call("GET", "/api/v1/tools?project_id=other&execution_profile=inspect", 404)

    def test_quarantine_covers_folders_inside_and_around_a_blocked_one(self):
        store = self.app.state.store
        blocked = str((self.root / "docs").resolve())
        with store.transaction() as db:
            db.execute("INSERT INTO workspace_blocks VALUES ('t','fixture',?,NULL,'2026-01-01T00:00:00Z',NULL,NULL)", (blocked,))
        inner, _ = self.create("docs/releases")
        root, _ = self.create("")
        sibling, _ = self.create("src")
        for value in (inner, root):
            self.assertIn("reconciliation", self.turn(value["conversation_id"], "hello", 409)["error"]["message"])
        self.finished(self.turn(sibling["conversation_id"], "hello")["task"]["task_id"])


if __name__ == "__main__":
    unittest.main()
