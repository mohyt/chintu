"""Voice notes: create, stream chunks, finish to a Markdown file owned by the pairing client."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from chintu_gateway import Settings, create_app


class NotesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "db.sqlite3"
        self.app = create_app(Settings(self.database))
        self.client = TestClient(self.app)
        secret = self.app.state.store.create_enrollment(60)
        paired = self.client.post("/api/v1/pair", json={"enrollment_secret": secret, "client_name": "Pixel"},
                                  headers={"X-Chintu-Protocol": "1"}).json()
        self.headers = {"X-Chintu-Protocol": "1", "Authorization": "Bearer " + paired["token"]}

    def tearDown(self):
        self.temp.cleanup()

    def test_note_round_trip_writes_markdown(self):
        created = self.client.post("/api/v1/notes", json={}, headers=self.headers)
        self.assertEqual(created.status_code, 201, created.text)
        note_id = created.json()["note_id"]
        first = self.client.post(f"/api/v1/notes/{note_id}/chunks", json={"text": "Remember to call the plumber"}, headers=self.headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["chunks"], 1)
        second = self.client.post(f"/api/v1/notes/{note_id}/chunks", json={"text": "and buy filters."}, headers=self.headers).json()
        self.assertEqual(second["words"], 8)
        saved = self.client.post(f"/api/v1/notes/{note_id}/finish", json={}, headers=self.headers)
        self.assertEqual(saved.status_code, 200, saved.text)
        value = saved.json()
        self.assertTrue(value["file"].endswith(".md"))
        self.assertEqual(value["words"], 8)
        content = (self.database.parent / "notes" / value["file"]).read_text()
        self.assertIn("Remember to call the plumber", content)
        self.assertIn("and buy filters.", content)
        again = self.client.post(f"/api/v1/notes/{note_id}/chunks", json={"text": "late"}, headers=self.headers)
        self.assertEqual(again.status_code, 409)
        twice = self.client.post(f"/api/v1/notes/{note_id}/finish", json={}, headers=self.headers)
        self.assertEqual(twice.status_code, 200)
        self.assertEqual(twice.json()["file"], value["file"])

    def test_configured_notes_dir_is_used(self):
        folder = Path(self.temp.name) / "vault" / "Chintu Notes"
        app = create_app(Settings(Path(self.temp.name) / "db2.sqlite3", notes_dir=folder))
        client = TestClient(app)
        secret = app.state.store.create_enrollment(60)
        paired = client.post("/api/v1/pair", json={"enrollment_secret": secret, "client_name": "Pixel"}, headers={"X-Chintu-Protocol": "1"}).json()
        headers = {"X-Chintu-Protocol": "1", "Authorization": "Bearer " + paired["token"]}
        note_id = client.post("/api/v1/notes", json={}, headers=headers).json()["note_id"]
        client.post(f"/api/v1/notes/{note_id}/chunks", json={"text": "milk eggs bread"}, headers=headers)
        saved = client.post(f"/api/v1/notes/{note_id}/finish", json={}, headers=headers).json()
        self.assertTrue((folder / saved["file"]).exists())

    def test_empty_note_writes_no_file(self):
        note_id = self.client.post("/api/v1/notes", json={}, headers=self.headers).json()["note_id"]
        saved = self.client.post(f"/api/v1/notes/{note_id}/finish", json={}, headers=self.headers).json()
        self.assertEqual(saved["words"], 0)
        self.assertEqual(saved["file"], "")
        self.assertFalse((self.database.parent / "notes").exists())

    def test_other_client_cannot_touch_note(self):
        note_id = self.client.post("/api/v1/notes", json={}, headers=self.headers).json()["note_id"]
        secret = self.app.state.store.create_enrollment(60)
        other = self.client.post("/api/v1/pair", json={"enrollment_secret": secret, "client_name": "Other"},
                                 headers={"X-Chintu-Protocol": "1"}).json()
        headers = {"X-Chintu-Protocol": "1", "Authorization": "Bearer " + other["token"]}
        self.assertEqual(self.client.post(f"/api/v1/notes/{note_id}/chunks", json={"text": "hi"}, headers=headers).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/notes/{}/chunks".format(note_id), json={"text": ""}, headers=self.headers).status_code, 422)


if __name__ == "__main__":
    unittest.main()
