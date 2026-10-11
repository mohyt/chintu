"""The /pair page: operator-only QR enrollment on the gateway's own screen."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from chintu_gateway import pairing_page
from chintu_gateway.app import create_app
from chintu_gateway.config import Settings


class FakeDiscovery:
    def __init__(self, result):
        self.result = result

    async def discover(self):
        return self.result


SELF = {"status": "available", "systems": [
    {"system_id": "mac.tail.ts.net", "name": "mac", "os": "macos", "online": True,
     "gateway_url": "https://mac.tail.ts.net", "is_self": True}]}


def make_client(discovery):
    folder = tempfile.mkdtemp()
    app = create_app(Settings(Path(folder) / "db.sqlite3"), adapter=None, systems_discovery=discovery)
    # The default TestClient peer is "testclient"; the page only answers genuine loopback peers.
    return TestClient(app, client=("127.0.0.1", 50000)), app


def proof(app, **extra):
    """Headers a trusted local caller sends: loopback Host for the test server's port 80 plus the secret."""
    return {"Host": "127.0.0.1", "X-Chintu-Local": app.state.local_secret, **extra}


class PairingPageTest(unittest.TestCase):
    def test_payload_shape_is_stable(self):
        value = json.loads(pairing_page.pairing_payload("https://h.ts.net", "Mac", "s" * 43, "mac"))
        self.assertEqual(value, {"chintu": 1, "url": "https://h.ts.net", "name": "Mac", "aliases": "mac", "secret": "s" * 43})

    def test_loopback_detection_rejects_proxied_requests(self):
        self.assertTrue(pairing_page.is_direct_loopback("127.0.0.1", []))
        self.assertTrue(pairing_page.is_direct_loopback("::1", ["accept"]))
        self.assertFalse(pairing_page.is_direct_loopback("100.64.0.9", []))
        self.assertFalse(pairing_page.is_direct_loopback("127.0.0.1", ["X-Forwarded-For"]))
        self.assertFalse(pairing_page.is_direct_loopback("127.0.0.1", ["Tailscale-User-Login"]))

    def test_local_request_requires_every_check(self):
        good = [("host", "127.0.0.1:8765"), ("accept", "*/*")]
        self.assertTrue(pairing_page.is_local_request("127.0.0.1", good, 8765, "s3cret", "s3cret"))
        self.assertTrue(pairing_page.is_local_request("::1", [("Host", "[::1]:8765")], 8765, "s3cret", "s3cret"))
        self.assertTrue(pairing_page.is_local_request("127.0.0.1", [("host", "localhost:8765")], 8765, "s3cret", "s3cret"))
        self.assertTrue(pairing_page.is_local_request("127.0.0.1", [("host", "localhost")], 80, "s3cret", "s3cret"))
        cases = [
            ("100.64.0.9", good, 8765, "s3cret", "s3cret"),
            ("127.0.0.1", good + [("X-Forwarded-For", "1.2.3.4")], 8765, "s3cret", "s3cret"),
            ("127.0.0.1", [("host", "127.0.0.1")], 8765, "s3cret", "s3cret"),
            ("127.0.0.1", [("host", "127.0.0.1:9999")], 8765, "s3cret", "s3cret"),
            ("127.0.0.1", [("host", "evil.example:8765")], 8765, "s3cret", "s3cret"),
            ("127.0.0.1", [], 8765, "s3cret", "s3cret"),
            ("127.0.0.1", good + [("Origin", "http://127.0.0.1:8765")], 8765, "s3cret", "s3cret"),
            ("127.0.0.1", good + [("Sec-Fetch-Site", "cross-site")], 8765, "s3cret", "s3cret"),
            ("127.0.0.1", good, 8765, "s3cret", "wrong"),
            ("127.0.0.1", good, 8765, "s3cret", None),
            ("127.0.0.1", good, 8765, "", ""),
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(pairing_page.is_local_request(*case))

    def test_render_embeds_qr_svg_and_escapes(self):
        page = pairing_page.render("https://mac.tail.ts.net", "<Mac>", "x" * 43, 600)
        self.assertIn("<svg", page)
        self.assertIn("&lt;Mac&gt;", page)
        self.assertIn("10 minutes", page)
        self.assertNotIn("x" * 43, re.sub(r"<svg.*?</svg>", "", page, flags=re.S))
        self.assertNotIn("fetch(", page)
        self.assertNotIn("<script", page)

    def test_page_serves_fresh_secret_with_query_proof_only(self):
        client, app = make_client(FakeDiscovery(SELF))
        key = app.state.local_secret
        response = client.get("/pair", params={"key": key}, headers={"Host": "127.0.0.1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("<svg", response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        refusals = {
            "no key": dict(headers={"Host": "127.0.0.1"}),
            "header instead of key": dict(headers=proof(app)),
            "wrong key": dict(params={"key": "x" * len(key)}, headers={"Host": "127.0.0.1"}),
            "proxied": dict(params={"key": key}, headers={"Host": "127.0.0.1", "X-Forwarded-For": "100.64.0.9"}),
            "rebinding host": dict(params={"key": key}, headers={"Host": "attacker.example"}),
            "cross-site": dict(params={"key": key}, headers={"Host": "127.0.0.1", "Sec-Fetch-Site": "cross-site"}),
        }
        for label, kwargs in refusals.items():
            with self.subTest(label=label):
                refused = client.get("/pair", **kwargs)
                self.assertEqual(refused.status_code, 403)
                self.assertNotIn("<svg", refused.text)
                self.assertEqual(refused.text, pairing_page.refused_page())

    def test_local_status_reports_presence(self):
        client, app = make_client(FakeDiscovery(SELF))
        before = client.get("/local/status", headers=proof(app)).json()
        self.assertEqual(before["clients"], [])
        self.assertFalse(before["phone_connected"])
        self.assertEqual(before["tailscale"]["gateway_url"], "https://mac.tail.ts.net")
        refusals = {
            "no proof": {"Host": "127.0.0.1"},
            "wrong proof": proof(app, **{"X-Chintu-Local": "nope"}),
            "proxied": proof(app, **{"X-Forwarded-For": "100.64.0.9"}),
            "origin": proof(app, Origin="http://127.0.0.1"),
            "cross-site": proof(app, **{"Sec-Fetch-Site": "cross-site"}),
            "foreign host": proof(app, Host="evil.example"),
            "wrong port": proof(app, Host="127.0.0.1:9999"),
            "default host header": {"X-Chintu-Local": app.state.local_secret},
        }
        bodies = set()
        for label, headers in refusals.items():
            with self.subTest(label=label):
                refused = client.get("/local/status", headers=headers)
                self.assertEqual(refused.status_code, 403)
                bodies.add(refused.text)
        self.assertEqual(len(bodies), 1)
        remote = TestClient(app, client=("100.64.0.9", 50000)).get("/local/status", headers=proof(app))
        self.assertEqual(remote.status_code, 403)
        self.assertEqual(remote.text, bodies.pop())
        secret = app.state.store.create_enrollment(60)
        paired = client.post("/api/v1/pair", json={"enrollment_secret": secret, "client_name": "Pixel"},
                             headers={"X-Chintu-Protocol": "1"})
        self.assertEqual(paired.status_code, 200, paired.text)
        after = client.get("/local/status", headers=proof(app)).json()
        self.assertEqual([c["name"] for c in after["clients"]], ["Pixel"])
        self.assertTrue(after["phone_connected"])
        self.assertGreater(after["clients"][0]["expires_at"], after["clients"][0]["paired_at"])

    def test_local_clients_list_and_revoke(self):
        client, app = make_client(FakeDiscovery(SELF))
        secret = app.state.store.create_enrollment(60)
        token = client.post("/api/v1/pair", json={"enrollment_secret": secret, "client_name": "Pixel"},
                            headers={"X-Chintu-Protocol": "1"}).json()["token"]
        phone = {"X-Chintu-Protocol": "1", "Authorization": "Bearer " + token}
        self.assertEqual(client.get("/api/v1/projects", headers=phone).status_code, 200)
        listed = client.get("/local/clients", headers=proof(app)).json()["clients"]
        self.assertEqual(set(listed[0]), {"client_id", "name", "paired_at", "last_seen", "expires_at", "connected"})
        self.assertNotIn(token, json.dumps(listed))
        client_id = listed[0]["client_id"]
        self.assertEqual(client.get("/local/clients", headers={"Host": "127.0.0.1"}).status_code, 403)
        self.assertEqual(client.post(f"/local/clients/{client_id}/revoke", headers={"Host": "127.0.0.1"}).status_code, 403)
        revoked = client.post(f"/local/clients/{client_id}/revoke", headers=proof(app))
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(revoked.json(), {"client_id": client_id, "revoked": True})
        self.assertEqual(client.get("/api/v1/projects", headers=phone).status_code, 401)
        self.assertEqual(client.get("/local/clients", headers=proof(app)).json()["clients"], [])
        self.assertEqual(client.post(f"/local/clients/{client_id}/revoke", headers=proof(app)).status_code, 404)
        self.assertEqual(client.post("/local/clients/not-a-uuid/revoke", headers=proof(app)).status_code, 422)

    def test_pairing_code_json_for_desktop_app(self):
        client, app = make_client(FakeDiscovery(SELF))
        response = client.post("/local/pairing-code", headers=proof(app))
        self.assertEqual(response.status_code, 200, response.text)
        value = response.json()
        payload = json.loads(value["payload"])
        self.assertEqual(payload["chintu"], 1)
        self.assertEqual(payload["url"], "https://mac.tail.ts.net")
        self.assertGreaterEqual(len(payload["secret"]), 16)
        self.assertEqual(value["expires_in"], 600)
        paired = client.post("/api/v1/pair", json={"enrollment_secret": payload["secret"], "client_name": "Pixel"},
                             headers={"X-Chintu-Protocol": "1"})
        self.assertEqual(paired.status_code, 200, paired.text)
        self.assertEqual(client.post("/local/pairing-code", headers=proof(app, **{"X-Forwarded-For": "100.64.0.9"})).status_code, 403)
        self.assertEqual(client.post("/local/pairing-code", headers={"Host": "127.0.0.1"}).status_code, 403)

    def test_page_reports_missing_tailscale(self):
        client, app = make_client(FakeDiscovery({"status": "unavailable", "reason": "Tailscale is not connected.", "systems": []}))
        response = client.get("/pair", params={"key": app.state.local_secret}, headers={"Host": "127.0.0.1"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("Tailscale is not connected.", response.text)


if __name__ == "__main__":
    unittest.main()
