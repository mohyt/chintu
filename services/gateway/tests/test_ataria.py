import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from chintu_gateway import Settings, create_app
from chintu_gateway import ataria
from chintu_gateway.config import load_config
from starlette.testclient import TestClient

TOKEN = "t" * 40
EXAMPLE = Path(__file__).resolve().parents[3] / "contracts/examples/ataria-board.json"


class FakeEngine(BaseHTTPRequestHandler):
    """A stand-in Ataria engine: records requests, answers from the class attributes."""
    board = None
    status = {"latest_date": "2026-11-22"}
    reply = None
    seen = []

    def log_message(self, *args):
        pass

    def answer(self, code, value):
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorised(self):
        FakeEngine.seen.append((self.command, self.path, dict(self.headers)))
        if self.headers.get("Authorization") != "Bearer " + TOKEN or self.headers.get("X-Ataria") != "1":
            self.answer(403, {"error": "not allowed"})
            return False
        return True

    def do_GET(self):
        if not self.authorised():
            return
        if self.path == "/ataria/api/v1/status":
            return self.answer(200, FakeEngine.status)
        if self.path.startswith("/ataria/api/v1/today"):
            if "date=2020-01-01" in self.path:
                return self.answer(404, {"error": "no board"})
            return self.answer(200, FakeEngine.board)
        if self.path == "/ataria/":
            return self.page(200, "text/html; charset=utf-8", b"<!doctype html><title>Ataria</title>")
        if self.path == "/ataria/api/today?date=2026-11-21":
            return self.answer(200, {"frontmatter": {"date": "2026-11-21"}})
        if self.path == "/ataria/odd":
            return self.page(200, "application/x-sh", b"#!/bin/sh")
        self.answer(404, {"error": "not found"})

    def page(self, code, kind, body):
        self.send_response(code)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.authorised():
            return
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        FakeEngine.posted = body
        if self.path == "/ataria/api/done":
            return self.answer(200, {"ok": True})
        self.answer(200, FakeEngine.reply or {**body, "done_at": "2026-11-22T09:00:00+08:00", "extra": "dropped"})


class AtariaCase(unittest.TestCase):
    """A fake engine and a gateway paired with one phone."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeEngine)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        board = json.loads(EXAMPLE.read_text())
        # The engine may send fields the contract does not know and text longer than the limits.
        board["items"][0]["path"] = "meetings/2026/11/22/items/x.md"
        board["items"][0]["why"] = "w" * 5000
        board["internal"] = {"anything": True}
        FakeEngine.board, FakeEngine.reply, FakeEngine.seen = board, None, []
        FakeEngine.status = {"latest_date": "2026-11-22"}
        self.token = self.base / "ataria.token"
        self.token.write_text(TOKEN + "\n")
        self.token.chmod(0o600)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/ataria/api/v1"
        self.client, self.headers, self.app = self.start(ataria.AtariaConfig(self.url, self.token))

    def start(self, config, name="gateway"):
        app = create_app(Settings(self.base / f"{name}.sqlite3", enable_worker=False, ataria=config))
        # The phone reaches the gateway over HTTPS (Tailscale Serve), so Secure cookies are sent back.
        client = TestClient(app, base_url="https://testserver").__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        response = client.post("/api/v1/pair", headers={"X-Chintu-Protocol": "1"}, json={
            "enrollment_secret": app.state.store.create_enrollment(), "client_name": "Phone"})
        return client, {"X-Chintu-Protocol": "1", "Authorization": "Bearer " + response.json()["token"]}, app

    def call(self, method, route, expected=200, schema=None, client=None, headers=None, app=None, **kwargs):
        response = (client or self.client).request(method, route, headers=headers or self.headers, **kwargs)
        self.assertEqual(response.status_code, expected, response.text)
        (app or self.app).state.contract.validate("Error" if expected >= 400 else schema, response.json())
        return response.json()


class AtariaTests(AtariaCase):
    def test_status_reports_engine(self):
        status = self.call("GET", "/api/v1/ataria", schema="AtariaStatus")
        self.assertEqual(status, {"enabled": True, "reachable": True, "latest_date": "2026-11-22", "reason": None})
        method, path, headers = FakeEngine.seen[-1]
        self.assertEqual((method, path, headers["Authorization"]), ("GET", "/ataria/api/v1/status", "Bearer " + TOKEN))

    def test_board_is_trimmed_to_the_contract(self):
        board = self.call("GET", "/api/v1/ataria/today", schema="AtariaBoard")
        self.assertNotIn("internal", board)
        self.assertNotIn("path", board["items"][0])
        self.assertEqual(len(board["items"][0]["why"]), 4000)
        self.assertEqual(len(board["items"]), len(FakeEngine.board["items"]))
        self.call("GET", "/api/v1/ataria/today?date=2026-11-22", schema="AtariaBoard")
        self.assertTrue(FakeEngine.seen[-1][1].endswith("/today?date=2026-11-22"))

    def test_board_errors(self):
        self.assertEqual(self.call("GET", "/api/v1/ataria/today?date=2020-01-01", 404)["error"]["code"], "not_found")
        self.call("GET", "/api/v1/ataria/today?date=22-11-2026", 400)
        FakeEngine.board = {"date": "2026-11-22", "items": [{"uid": "../../etc", "lane": "work"}]}
        self.assertEqual(self.call("GET", "/api/v1/ataria/today", 502)["error"]["code"], "ataria_error")

    def test_done_round_trip(self):
        result = self.call("POST", "/api/v1/ataria/done", schema="AtariaDoneResult",
                           json={"done": ["work.mail-release-notes-2026-11-21"], "undone": ["personal.growth-1"]})
        self.assertEqual(result, {"done": ["work.mail-release-notes-2026-11-21"], "undone": ["personal.growth-1"],
                                  "done_at": "2026-11-22T09:00:00+08:00"})
        self.assertEqual(FakeEngine.posted, {"done": ["work.mail-release-notes-2026-11-21"], "undone": ["personal.growth-1"]})

    def test_done_validation(self):
        self.call("POST", "/api/v1/ataria/done", 422, json={"done": [], "undone": []})
        self.call("POST", "/api/v1/ataria/done", 422, json={"done": ["work.a"], "undone": ["work.a"]})
        self.call("POST", "/api/v1/ataria/done", 422, json={"done": ["../x"], "undone": []})
        self.call("POST", "/api/v1/ataria/done", 422, json={"done": ["work.a"]})
        self.assertFalse([seen for seen in FakeEngine.seen if seen[0] == "POST"])

    def test_requires_pairing(self):
        response = self.client.get("/api/v1/ataria/today", headers={"X-Chintu-Protocol": "1"})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(FakeEngine.seen)

    def test_not_configured(self):
        client, headers, app = self.start(None, "plain")
        status = self.call("GET", "/api/v1/ataria", schema="AtariaStatus", client=client, headers=headers, app=app)
        self.assertFalse(status["enabled"])
        error = self.call("GET", "/api/v1/ataria/today", 503, client=client, headers=headers, app=app)
        self.assertEqual(error["error"]["code"], "ataria_unavailable")

    def test_engine_down_or_token_problems(self):
        client, headers, app = self.start(ataria.AtariaConfig("http://127.0.0.1:9/ataria/api/v1", self.token), "down")
        status = self.call("GET", "/api/v1/ataria", schema="AtariaStatus", client=client, headers=headers, app=app)
        self.assertEqual((status["enabled"], status["reachable"]), (True, False))
        self.assertEqual(self.call("GET", "/api/v1/ataria/today", 503, client=client, headers=headers, app=app)
                         ["error"]["retryable"], True)
        self.token.chmod(0o644)
        self.assertIn("readable by other users", self.call("GET", "/api/v1/ataria/today", 503)["error"]["message"])
        self.token.unlink()
        self.assertIn("missing", self.call("GET", "/api/v1/ataria/today", 503)["error"]["message"])
        self.assertFalse([seen for seen in FakeEngine.seen if seen[1].startswith("/ataria/api/v1/today")])

    def test_config_accepts_only_loopback(self):
        for url in ("https://127.0.0.1/x", "http://10.0.0.5/x", "http://example.com/x", "http://u:p@127.0.0.1/x"):
            with self.assertRaises(ValueError, msg=url):
                ataria.parse_config({"url": url, "token_file": "t"}, self.base)
        with self.assertRaises(ValueError):
            ataria.parse_config({"url": self.url}, self.base)
        parsed = ataria.parse_config({"url": "http://localhost:8787/ataria/api/v1/", "token_file": "ataria.token"}, self.base)
        self.assertEqual((parsed.url, parsed.token_file), ("http://localhost:8787/ataria/api/v1", self.token.resolve()))

    def test_operator_config_block(self):
        path = self.base / "gateway.json"
        path.write_text(json.dumps({"version": 1, "database": "g.sqlite3", "projects": [], "runtimes": {},
                                    "ataria": {"url": self.url, "token_file": "ataria.token"}}))
        self.assertEqual(load_config(path).settings.ataria, ataria.AtariaConfig(self.url, self.token.resolve()))


if __name__ == "__main__":
    unittest.main()
