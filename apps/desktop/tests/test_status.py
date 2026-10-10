import io
import json
import unittest
import urllib.error

from chintu_desktop import status
from chintu_desktop.status import Presence, derive_state, fetch_status, phone_line, tasks_line, tooltip

CONNECTED_PAYLOAD = {
    "clients": [{"client_id": "c1", "name": "Pixel 8", "paired_at": 1.0, "last_seen": 100.0, "connected": True}],
    "phone_connected": True, "active_tasks": 2, "total_tasks": 9, "service": "chintu", "provider": "claude-agent",
    "tailscale": {"status": "available", "reason": None, "gateway_url": "https://mac.tail.ts.net", "name": "mac"},
    "pair_url": "http://127.0.0.1:8765/pair",
}

AWAY_PAYLOAD = {
    "clients": [{"client_id": "c1", "name": "Old phone", "paired_at": 1.0, "last_seen": 5.0, "connected": False},
                {"client_id": "c2", "name": "Pixel 8", "paired_at": 2.0, "last_seen": 50.0, "connected": False}],
    "phone_connected": False, "active_tasks": 0, "total_tasks": 3, "service": "chintu", "provider": None,
    "tailscale": {"status": "unavailable", "reason": "tailscaled is not running", "gateway_url": None, "name": None},
    "pair_url": "http://127.0.0.1:8765/pair",
}


class DeriveStateTests(unittest.TestCase):
    def test_connected_phone(self):
        state = derive_state(CONNECTED_PAYLOAD, now=0)
        self.assertIs(state.presence, Presence.CONNECTED)
        self.assertTrue(state.daemon_running)
        self.assertEqual(state.phone_name, "Pixel 8")
        self.assertEqual((state.active_tasks, state.total_tasks, state.paired_count), (2, 9, 1))
        self.assertEqual(state.gateway_url, "https://mac.tail.ts.net")
        self.assertEqual(state.provider, "claude-agent")
        self.assertEqual(phone_line(state), "Phone: Pixel 8 connected")
        self.assertEqual(tasks_line(state), "Active tasks: 2 (total 9)")
        self.assertEqual(tooltip(state), "Chintu: Pixel 8 connected")

    def test_paired_but_away_prefers_most_recently_seen(self):
        state = derive_state(AWAY_PAYLOAD, now=0)
        self.assertIs(state.presence, Presence.AWAY)
        self.assertEqual(state.phone_name, "Pixel 8")
        self.assertEqual(state.paired_count, 2)
        self.assertIsNone(state.gateway_url)
        self.assertEqual(state.tailscale_reason, "tailscaled is not running")
        self.assertEqual(phone_line(state), "Phone: Pixel 8 away")
        self.assertEqual(status.gateway_url_line(state), "Tailnet URL: unavailable (tailscaled is not running)")

    def test_no_clients_is_away_not_paired(self):
        payload = dict(AWAY_PAYLOAD, clients=[])
        state = derive_state(payload, now=0)
        self.assertIs(state.presence, Presence.AWAY)
        self.assertIsNone(state.phone_name)
        self.assertEqual(phone_line(state), "Phone: not paired yet")

    def test_daemon_down(self):
        state = derive_state(None, "gateway unreachable: Connection refused", port=9000, now=0)
        self.assertIs(state.presence, Presence.DOWN)
        self.assertFalse(state.daemon_running)
        self.assertEqual(state.pair_url, "http://127.0.0.1:9000/pair")
        self.assertIn("Connection refused", state.error)
        self.assertEqual(phone_line(state), "Phone: unknown (gateway not running)")
        self.assertEqual(tooltip(state), "Chintu: gateway not running")

    def test_tolerates_missing_fields(self):
        state = derive_state({"service": "chintu"}, now=0)
        self.assertIs(state.presence, Presence.AWAY)
        self.assertEqual((state.active_tasks, state.total_tasks), (0, 0))


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FetchStatusTests(unittest.TestCase):
    def test_ok(self):
        def opener(request, timeout):
            self.assertEqual(request.full_url, "http://127.0.0.1:8765/local/status")
            return _Response(json.dumps(CONNECTED_PAYLOAD).encode())
        payload, error = fetch_status(8765, opener=opener)
        self.assertIsNone(error)
        self.assertTrue(payload["phone_connected"])

    def test_connection_refused(self):
        def opener(request, timeout):
            raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        payload, error = fetch_status(8765, opener=opener)
        self.assertIsNone(payload)
        self.assertIn("unreachable", error)

    def test_http_error(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b"{}"))
        payload, error = fetch_status(8765, opener=opener)
        self.assertIsNone(payload)
        self.assertEqual(error, "gateway answered HTTP 403")

    def test_other_service_on_port(self):
        payload, error = fetch_status(8765, opener=lambda r, timeout: _Response(b'{"service": "other"}'))
        self.assertIsNone(payload)
        self.assertIn("not a Chintu gateway", error)


class PollerTests(unittest.TestCase):
    def test_poll_once_reports_state(self):
        seen = []
        poller = status.Poller(8765, on_state=seen.append, fetch=lambda port: (CONNECTED_PAYLOAD, None))
        state = poller.poll_once()
        self.assertIs(state.presence, Presence.CONNECTED)
        self.assertEqual(seen, [state])


if __name__ == "__main__":
    unittest.main()
