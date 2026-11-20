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

    def test_note_is_an_okf_file(self):
        import yaml
        note_id = self.client.post("/api/v1/notes", json={}, headers=self.headers).json()["note_id"]
        self.client.post(f"/api/v1/notes/{note_id}/chunks", json={"text": 'Call "Ravi": 10am, then #review'}, headers=self.headers)
        saved = self.client.post(f"/api/v1/notes/{note_id}/finish", json={}, headers=self.headers).json()
        folder = self.database.parent / "notes"
        text = (folder / saved["file"]).read_text()
        self.assertTrue(text.startswith("---\n"))
        end = text.index("\n---\n", 3)
        fm, body = yaml.safe_load(text[4:end]), text[end + 5:]
        self.assertEqual(fm["type"], "Chintu Note")
        self.assertEqual(fm["id"], Path(saved["file"]).stem)
        self.assertEqual(fm["words"], 5)
        self.assertEqual(fm["sources"], [{"resource": "chintu://note/" + note_id, "title": "Voice note from Pixel"}])
        self.assertEqual(set(fm["generated"]), {"by", "at"})
        self.assertIn('Call "Ravi": 10am, then #review', body)
        index = (folder / "index.md").read_text()
        self.assertEqual(yaml.safe_load(index.split("---")[1]), {"okf_version": "0.2"})
        self.assertTrue((folder / "log.md").exists())

    def test_notes_folder_follows_data_home(self):
        from chintu_gateway import okf
        base = Path(self.temp.name)
        config = base / "data.env"
        db = base / "db.sqlite3"
        self.assertEqual(okf.notes_folder(None, db, {}, config), db.resolve().parent / "notes")
        config.write_text("# set by ataria-sync join\nDATA_HOME='" + str(base / "vault") + "'\n")
        self.assertEqual(okf.notes_folder(None, db, {}, config), (base / "vault").resolve() / "00-Inbox" / "Notes")
        self.assertEqual(okf.notes_folder(None, db, {"DATA_HOME": str(base / "other")}, config), (base / "other").resolve() / "00-Inbox" / "Notes")
        self.assertEqual(okf.notes_folder(base / "mine", db, {"DATA_HOME": str(base / "other")}, config), base / "mine")

    def test_note_file_stays_in_notes_folder(self):
        from chintu_gateway import okf
        folder = Path(self.temp.name) / "notes"
        folder.mkdir()
        for name in ("../escape.md", "sub/x.md", "/tmp/x.md"):
            with self.assertRaises(ValueError):
                okf.write_in(folder, name, {"type": "Note"}, "x")
        okf.write_in(folder, "ok.md", {"type": "Note"}, "x")
        self.assertTrue((folder / "ok.md").exists())

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
