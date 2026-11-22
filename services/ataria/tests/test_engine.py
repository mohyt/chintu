"""The engine core: paths, the merge and the server, against a fictional owner's vault."""

import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

from chintu_ataria import board, server, vault as okf

TOKEN = "e" * 48


def profile(base):
    return {
        "schema": 1, "vault": str(base / "vault"),
        "owner": {"name": "Sam", "timezone": "Asia/Singapore", "locale": "en-GB"},
        "personal": {"family": [{"name": "Robin", "relation": "child"}],
                     "growth": {"region_name": "Southeast Asia", "region_tag": "sea",
                                "course": {"name": "Rust", "slug": "rust", "lessons": [["1", "Ownership", "2 hours"]]}}},
        "work": {"enabled": True},
        "schedule": {"morning": "07:00", "midday": "13:30"},
        "delivery": {"recipients": [
            {"name": "Sam", "channel": "whatsapp", "areas": ["work", "personal", "family"], "digests": ["morning", "midday"]},
            {"name": "Alex", "channel": "whatsapp", "areas": ["family"], "digests": ["morning"]}]},
        "engine": {"state_dir": str(base / "state"), "token_sha256_file": str(base / "engine.sha256")},
    }


class EngineCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.profile = profile(self.base)
        self.v = okf.Vault(self.profile)
        self.today = datetime(2026, 11, 22, 7, 0, tzinfo=self.v.tz)
        (self.base / "engine.sha256").write_text(hashlib.sha256(TOKEN.encode()).hexdigest() + "\n")

    def concept(self, lane, kind, name, day=22, month=11, body="Why it matters.", **front):
        front = {"type": "Item" if kind == "items" else "Todo", "id": name, "updated_at": "2026-11-22T06:00:00+08:00",
                 "first_seen": f"2026-{month:02d}-{day:02d}", **front}
        okf.write(self.v.path(lane) / f"2026/{month:02d}/{day:02d}/{kind}/{name}.md", front, body)

    def merge(self, **kwargs):
        return board.Merge(self.profile, self.v).run(self.today, "morning", **kwargs)


class PathTests(EngineCase):
    def test_content_in_the_vault_operations_in_state(self):
        self.assertEqual(self.v.path("work/2026/11/22/items/a.md"),
                         self.base / "vault/20-Work/Ataria/work/2026/11/22/items/a.md")
        self.assertEqual(self.v.path("work/2026/11/22/run-0700.md"), self.base / "state/work/2026/11/22/run-0700.md")
        self.assertEqual(self.v.path("outbox/x.md"), self.base / "state/outbox/x.md")
        self.assertEqual(self.v.logical(self.v.path("app/2026/11/22/today.md")), "app/2026/11/22/today.md")
        legacy = okf.Vault({**self.profile, "engine": {"state_dir": str(self.base / "state"), "layout": "data"}})
        self.assertEqual(legacy.path("work"), self.base / "state/work")

    def test_time_is_the_owners(self):
        self.assertEqual(self.v.parse_dt("2026-11-22T07:00:00").utcoffset().total_seconds(), 8 * 3600)
        self.assertEqual(okf.canonical_type("Ataria Item"), "Item")


class MergeTests(EngineCase):
    def test_board(self):
        for n in range(5):
            self.concept("work", "items", f"p0-{n}", p="P0", title=f"Client escalation {n}", tt=f"Escalation {n}")
        self.concept("finance", "items", "card", p="P1", title="Card bill of 9,999", tt="Card bill due", tags=["home"])
        self.concept("finance", "todos", "card-pay", p="P1", item="card", tt="Pay the card")
        self.concept("personal", "items", "news", info=True, tt="A story", area="growth")
        self.concept("personal", "items", "old", day=2, month=10, state="closed", tt="Closed long ago")
        self.concept("personal", "items", "carried", day=20, tt="Still open", tags=["kids"])
        out, front = self.merge()
        items = {e["uid"]: e for e in front["items"]}
        self.assertTrue(out.is_file())
        # At most three P0s; the rest become capped P1s.
        self.assertEqual([items[f"work.p0-{n}"]["p"] for n in range(5)], ["P0", "P0", "P0", "P1", "P1"])
        self.assertTrue(items["work.p0-4"]["capped"])
        # Outside work only the safe title is shown.
        self.assertEqual(items["finance.card"]["title"], "Card bill due")
        self.assertEqual(items["work.p0-0"]["title"], "Client escalation 0")
        self.assertTrue(items["finance.card-pay"]["nested"])
        self.assertEqual(items["personal.news"]["group"], "info")
        self.assertNotIn("personal.old", items)
        self.assertTrue(items["personal.carried"]["backlog"])
        self.assertEqual(front["counts"]["P0"], 3)

        # A tick on the item ticks its todo; done today shows in the done group.
        okf.write(self.v.path("app/done/finance.card.md"), {"type": "Ataria Done", "done_at": "2026-11-22T06:30:00+08:00"})
        _, front = self.merge(outbox=False)
        items = {e["uid"]: e for e in front["items"]}
        self.assertEqual((items["finance.card"]["group"], items["finance.card-pay"]["linked_done"]), ("done", True))

    def test_messages_follow_the_profile(self):
        self.concept("personal", "items", "fees", p="P1", tt="School fees", tags=["kids"])
        self.merge()
        outbox = self.base / "state/outbox/2026/11/22"
        me = (outbox / "morning-me.md").read_text()
        self.assertIn("Home (for Alex):", me)
        self.assertIn("School fees", (outbox / "morning-alex.md").read_text())
        # Nothing new: no second home update.
        (outbox / "morning-alex.md").unlink()
        self.merge()
        self.assertFalse((outbox / "morning-alex.md").exists())
        # Alex gets the morning digest only.
        self.concept("personal", "items", "trip", p="P2", tt="Book the trip", tags=["home"])
        board.Merge(self.profile, self.v).run(self.today, "midday")
        self.assertFalse((outbox / "midday-alex.md").exists())
        self.assertTrue((outbox / "midday-me.md").exists())


class ServerTests(EngineCase):
    def setUp(self):
        super().setUp()
        self.concept("work", "items", "release", p="P1", title="Ship the release", tt="Release",
                     sources=[{"title": "PR", "resource": "https://example.com/pr/1"}])
        self.concept("work", "todos", "release-notes", p="P1", item="release", tt="Write notes")
        self.merge(outbox=False)
        engine = server.Engine(self.profile, self.v)
        handler = server.make_handler(engine, "/ataria", str(self.base / "engine.sha256"))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/ataria"

    def call(self, method, path, body=None, token=TOKEN, write=True):
        headers = {"Authorization": "Bearer " + token} if token else {}
        if write:
            headers["X-Ataria"] = "1"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method,
                                         headers={**headers, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if response.headers["content-type"].startswith("application/json") else raw)
        except urllib.error.HTTPError as error:
            return error.code, None

    def test_only_the_gateway_gets_in(self):
        self.assertEqual(self.call("GET", "/api/today", token=None)[0], 403)
        self.assertEqual(self.call("GET", "/api/today", token="x" * 48)[0], 403)
        self.assertEqual(self.call("POST", "/api/done", {"uid": "work.release"}, write=False)[0], 403)

    def test_dashboard_api(self):
        status, page = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"app.js", page)
        self.assertEqual(self.call("GET", "/app.js")[0], 200)
        self.assertEqual(self.call("GET", "/../server.py")[0], 404)
        config = self.call("GET", "/api/config")[1]
        self.assertEqual(config["owner"], "Sam")
        self.assertEqual(config["region"], {"name": "Southeast Asia", "tag": "sea"})
        self.assertTrue(config["kids"])
        today = self.call("GET", "/api/today")[1]["frontmatter"]
        self.assertEqual(len(today["items"]), 2)
        self.assertEqual(self.call("GET", "/api/days")[1][0]["date"], "2026-11-22")
        self.assertEqual(self.call("GET", "/api/today?date=2020-01-01")[0], 404)
        note = self.call("GET", "/api/md?path=work/2026/11/22/items/release.md")[1]
        self.assertEqual(note["frontmatter"]["id"], "release")
        self.assertEqual(self.call("GET", "/api/md?path=../engine.sha256")[0], 404)
        self.assertEqual(self.call("GET", "/api/md?path=app/2026/11/22/today.md")[0], 404)

    def test_contract_api_and_ticks(self):
        board_ = self.call("GET", "/api/v1/today")[1]
        self.assertEqual(board_["date"], "2026-11-22")
        item = next(i for i in board_["items"] if i["uid"] == "work.release")
        self.assertEqual(item["links"], [{"title": "PR", "url": "https://example.com/pr/1"}])
        todo = next(i for i in board_["items"] if i["kind"] == "todo")
        self.assertEqual(todo["parent"], "work.release")
        self.assertEqual(self.call("GET", "/api/v1/status")[1], {"latest_date": "2026-11-22"})

        # Ticking the only todo closes its item automatically; unticking reopens it.
        result = self.call("POST", "/api/v1/done", {"done": ["work.release-notes"], "undone": []})[1]
        self.assertEqual(result["done"], ["work.release-notes"])
        done = self.v.path("app/done")
        self.assertTrue((done / "work.release.md").exists())
        self.assertEqual(okf.read(done / "work.release-notes.md")[0]["generated"]["by"], "human:chintu")
        self.call("DELETE", "/api/done/work.release-notes")
        self.assertFalse((done / "work.release.md").exists())
        self.assertEqual(self.call("POST", "/api/done", {"done": ["../x"]})[0], 400)


if __name__ == "__main__":
    unittest.main()
