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

    def test_render_embeds_qr_svg_and_escapes(self):
        page = pairing_page.render("https://mac.tail.ts.net", "<Mac>", "x" * 43, 600)
        self.assertIn("<svg", page)
        self.assertIn("&lt;Mac&gt;", page)
        self.assertIn("10 minutes", page)
        self.assertNotIn("x" * 43, re.sub(r"<svg.*?</svg>", "", page, flags=re.S))

    def test_page_serves_fresh_secret_on_loopback_only(self):
        client, app = make_client(FakeDiscovery(SELF))
        response = client.get("/pair")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("<svg", response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        proxied = client.get("/pair", headers={"X-Forwarded-For": "100.64.0.9"})
        self.assertEqual(proxied.status_code, 403)
        self.assertNotIn("<svg", proxied.text)

    def test_local_status_reports_presence(self):
        client, app = make_client(FakeDiscovery(SELF))
        before = client.get("/local/status").json()
        self.assertEqual(before["clients"], [])
        self.assertFalse(before["phone_connected"])
        self.assertEqual(before["tailscale"]["gateway_url"], "https://mac.tail.ts.net")
        self.assertEqual(client.get("/local/status", headers={"X-Forwarded-For": "100.64.0.9"}).status_code, 403)
        page = client.get("/pair").text
        # The secret only lives inside the QR; mint a second one through the store for the test.
        secret = app.state.store.create_enrollment(60) if hasattr(app.state, "store") else None
        if secret is None:
            self.skipTest("store not exposed on app.state")
        paired = client.post("/api/v1/pair", json={"enrollment_secret": secret, "client_name": "Pixel"},
                             headers={"X-Chintu-Protocol": "1"})
        self.assertEqual(paired.status_code, 200, paired.text)
        after = client.get("/local/status").json()
        self.assertEqual([c["name"] for c in after["clients"]], ["Pixel"])
        self.assertTrue(after["phone_connected"])
        self.assertIn("<svg", page)

    def test_pairing_code_json_for_desktop_app(self):
        client, _ = make_client(FakeDiscovery(SELF))
        response = client.post("/local/pairing-code")
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
        self.assertEqual(client.post("/local/pairing-code", headers={"X-Forwarded-For": "100.64.0.9"}).status_code, 403)

    def test_page_reports_missing_tailscale(self):
        client, _ = make_client(FakeDiscovery({"status": "unavailable", "reason": "Tailscale is not connected.", "systems": []}))
        response = client.get("/pair")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Tailscale is not connected.", response.text)


if __name__ == "__main__":
    unittest.main()
