import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace

from chintu_desktop import remote

CODE = json.dumps({"chintu": 1, "url": "https://box.tail.ts.net", "name": "Box", "aliases": "", "secret": "s" * 43})


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FakeGateway:
    """Answers like a gateway and records each request."""

    def __init__(self):
        self.seen, self.fail = [], None

    def __call__(self, request, timeout=None):
        self.seen.append((request.get_method(), request.full_url, dict(request.header_items()),
                          json.loads(request.data) if request.data else None))
        if self.fail:
            raise urllib.error.HTTPError(request.full_url, self.fail, "x", {},
                                         io.BytesIO(json.dumps({"error": {"message": "Enrollment secret is invalid or expired."}}).encode()))
        if request.full_url.endswith("/api/v1/pair"):
            return FakeResponse(json.dumps({"client_id": "c-1", "token": "T" * 64, "token_type": "Bearer"}).encode())
        if request.full_url.endswith("/api/v1/ataria/link"):
            return FakeResponse(json.dumps({"path": "/ataria/open?code=" + "a" * 43, "expires_in": 60}).encode())
        return FakeResponse(b"")


class RemoteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.layout = SimpleNamespace(home=Path(temp.name))
        self.gateway = FakeGateway()

    def test_codes(self):
        self.assertEqual(remote.parse_code(CODE), ("https://box.tail.ts.net", "s" * 43, "Box"))
        for bad in ("", "nonsense", json.dumps({"chintu": 2}), CODE.replace("https", "http"),
                    CODE.replace("ts.net", "ts.net/x"), CODE.replace("https://", "https://u:p@")):
            with self.assertRaises(remote.RemoteError, msg=bad):
                remote.parse_code(bad)

    def test_connect_link_and_disconnect(self):
        self.assertIsNone(remote.remote_link(self.layout, self.gateway))
        url, name = remote.connect(self.layout, CODE + "\n", self.gateway, hostname="mac")
        self.assertEqual((url, name), ("https://box.tail.ts.net", "Box"))
        method, address, headers, body = self.gateway.seen[-1]
        self.assertEqual((method, address), ("POST", "https://box.tail.ts.net/api/v1/pair"))
        self.assertEqual(body, {"enrollment_secret": "s" * 43, "client_name": "mac (desktop)"})
        saved = remote.remote_file(self.layout)
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertEqual(remote.connected_name(self.layout), "Box")

        link = remote.remote_link(self.layout, self.gateway)
        self.assertEqual(link, "https://box.tail.ts.net/ataria/open?code=" + "a" * 43)
        headers = self.gateway.seen[-1][2]
        self.assertEqual(headers["Authorization"], "Bearer " + "T" * 64)

        self.assertTrue(remote.disconnect(self.layout, self.gateway))
        self.assertEqual(self.gateway.seen[-1][:2], ("DELETE", "https://box.tail.ts.net/api/v1/clients/me"))
        self.assertFalse(saved.exists())
        self.assertFalse(remote.disconnect(self.layout, self.gateway))

    def test_a_refused_code_saves_nothing(self):
        self.gateway.fail = 401
        with self.assertRaisesRegex(remote.RemoteError, "invalid or expired"):
            remote.connect(self.layout, CODE, self.gateway)
        self.assertFalse(remote.remote_file(self.layout).exists())

    def test_an_open_file_is_refused(self):
        remote.connect(self.layout, CODE, self.gateway)
        remote.remote_file(self.layout).chmod(0o644)
        with self.assertRaisesRegex(remote.RemoteError, "readable by other users"):
            remote.remote_link(self.layout, self.gateway)
        self.assertIsNone(remote.connected_name(self.layout))

    def test_this_computer_first(self):
        class Local:
            port = 8765

            def post_json(self, path):
                self.path = path
                return {"path": "/ataria/open?code=" + "b" * 43, "expires_in": 60}
        local = Local()
        remote.connect(self.layout, CODE, self.gateway)
        self.assertEqual(remote.dashboard_url(self.layout, local, True, self.gateway),
                         "http://127.0.0.1:8765/ataria/open?code=" + "b" * 43)
        self.assertEqual(local.path, "/local/ataria/link")
        self.assertTrue(remote.dashboard_url(self.layout, local, False, self.gateway).startswith("https://box.tail.ts.net/"))


if __name__ == "__main__":
    unittest.main()
