import tempfile
import unittest
from pathlib import Path

from chintu_desktop import local, status
from chintu_desktop.status import Presence, derive_state, fetch_status, phone_line, tasks_line, tooltip
from fake_gateway import FakeGateway, repo_layout, write_secret

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
        self.assertIsNone(state.fault_kind)
        self.assertEqual(state.clients[0].client_id, "c1")
        self.assertEqual(phone_line(state), "Phone: Pixel 8 connected")
        self.assertEqual(tasks_line(state), "Active tasks: 2 (total 9)")
        self.assertEqual(tooltip(state), "Chintu: Pixel 8 connected")
        self.assertFalse(hasattr(state, "pair_url"))  # never taken from the server

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

    def test_explicit_clients_listing_wins_over_payload(self):
        listing = local.parse_clients([{"client_id": "z", "name": "Zed", "connected": False, "expires_at": 5.0}])
        state = derive_state(CONNECTED_PAYLOAD, now=0, clients=listing)
        self.assertEqual([c.name for c in state.clients], ["Zed"])
        self.assertEqual(state.clients[0].expires_at, 5.0)
        self.assertEqual(state.paired_count, 1)

    def test_daemon_down(self):
        state = derive_state(None, local.Unreachable("gateway unreachable: Connection refused"), port=9000, now=0)
        self.assertIs(state.presence, Presence.DOWN)
        self.assertFalse(state.daemon_running)
        self.assertEqual(state.fault_kind, "unreachable")
        self.assertIn("Connection refused", state.error)
        self.assertEqual(phone_line(state), "Phone: unknown (gateway not running)")
        self.assertEqual(tooltip(state), "Chintu: gateway not running")

    def test_fault_kinds_are_carried(self):
        for error, kind in ((local.SecretMissing("x"), "no_secret"), (local.Refused("x"), "refused"),
                            (local.NotChintu("x"), "not_chintu"), ("poll failed: boom", "error"), (None, "unreachable")):
            state = derive_state(None, error, now=0)
            self.assertEqual(state.fault_kind, kind, error)
            self.assertEqual(state.clients, ())

    def test_tolerates_missing_fields(self):
        state = derive_state({"service": "chintu"}, now=0)
        self.assertIs(state.presence, Presence.AWAY)
        self.assertEqual((state.active_tasks, state.total_tasks), (0, 0))


class FetchStatusTests(unittest.TestCase):
    """Against the fake gateway so the proof headers travel over a real socket."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.layout = repo_layout(self.repo)

    def test_ok_sends_proof(self):
        write_secret(self.repo, "correct-secret")
        with FakeGateway(secret="correct-secret") as gateway:
            payload, error = fetch_status(gateway.port, self.layout)
            headers = gateway.requests[0][2]
        self.assertIsNone(error)
        self.assertTrue(payload["phone_connected"])
        self.assertEqual(headers["X-Chintu-Local"], "correct-secret")
        self.assertEqual(headers["Host"], "127.0.0.1:%d" % gateway.port)

    def test_403_is_refused_fault(self):
        write_secret(self.repo, "wrong")
        with FakeGateway(secret="correct-secret") as gateway:
            payload, error = fetch_status(gateway.port, self.layout)
        self.assertIsNone(payload)
        self.assertEqual(error.kind, "refused")

    def test_missing_secret_makes_no_request(self):
        with FakeGateway(secret="correct-secret") as gateway:
            payload, error = fetch_status(gateway.port, self.layout)
            self.assertEqual(gateway.requests, [])
        self.assertIsNone(payload)
        self.assertEqual(error.kind, "no_secret")

    def test_other_service_on_port(self):
        write_secret(self.repo, "correct-secret")
        with FakeGateway(secret="correct-secret", service="other") as gateway:
            payload, error = fetch_status(gateway.port, self.layout)
        self.assertIsNone(payload)
        self.assertEqual(error.kind, "not_chintu")
        self.assertIn("not a Chintu gateway", str(error))

    def test_connection_refused(self):
        write_secret(self.repo, "s")
        client = local.LocalClient(self.layout, 1, transport=_refuse)
        payload, error = fetch_status(1, self.layout, client=client)
        self.assertIsNone(payload)
        self.assertEqual(error.kind, "unreachable")
        self.assertIn("unreachable", str(error))


def _refuse(*args):
    raise ConnectionRefusedError(61, "Connection refused")


class PollerTests(unittest.TestCase):
    def test_poll_once_merges_clients_listing(self):
        seen = []
        listing = local.parse_clients([{"client_id": "c1", "name": "Pixel 8", "connected": True, "expires_at": 9.0}])
        poller = status.Poller(8765, None, on_state=seen.append, fetch=lambda port, layout: (CONNECTED_PAYLOAD, None),
                               fetch_clients=lambda port, layout: (listing, None))
        state = poller.poll_once()
        self.assertIs(state.presence, Presence.CONNECTED)
        self.assertEqual(state.clients[0].expires_at, 9.0)
        self.assertEqual(seen, [state])

    def test_poll_once_falls_back_to_payload_clients(self):
        poller = status.Poller(8765, None, fetch=lambda port, layout: (CONNECTED_PAYLOAD, None),
                               fetch_clients=lambda port, layout: (None, local.Refused("x")))
        state = poller.poll_once()
        self.assertEqual([c.name for c in state.clients], ["Pixel 8"])

    def test_poll_once_skips_clients_when_status_fails(self):
        calls = []
        poller = status.Poller(8765, None, fetch=lambda port, layout: (None, local.SecretMissing("x")),
                               fetch_clients=lambda port, layout: calls.append(1))
        state = poller.poll_once()
        self.assertEqual((state.fault_kind, calls), ("no_secret", []))


if __name__ == "__main__":
    unittest.main()
