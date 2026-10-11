"""Local proof: secret loading, header injection, listener verification and revoke against a fake gateway."""

import socket
import tempfile
import unittest
from pathlib import Path

from chintu_desktop import local
from fake_gateway import FakeGateway, write_secret


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class SecretTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def test_path_is_var_local_secret_under_repo(self):
        self.assertEqual(local.secret_path(self.repo), self.repo / "var" / "local.secret")

    def test_missing_or_empty_file_raises(self):
        with self.assertRaises(local.SecretMissing) as caught:
            local.read_secret(self.repo)
        self.assertEqual(caught.exception.kind, "no_secret")
        self.assertIn("not running", str(caught.exception))
        write_secret(self.repo, "")
        with self.assertRaises(local.SecretMissing):
            local.read_secret(self.repo)

    def test_reads_fresh_value_on_every_call(self):
        write_secret(self.repo, "first")
        client = local.LocalClient(self.repo, 1)
        self.assertEqual(client.secret(), "first")
        write_secret(self.repo, "second")  # the daemon restarted
        self.assertEqual(client.secret(), "second")
        self.assertEqual(client.pair_url(), "http://127.0.0.1:1/pair?key=second")

    def test_whitespace_is_stripped(self):
        write_secret(self.repo, "  abc  ")
        self.assertEqual(local.read_secret(self.repo), "abc")


class HeaderTests(unittest.TestCase):
    def test_proof_headers_have_exact_host_and_no_origin(self):
        headers = local.proof_headers(8765, "s3cret")
        self.assertEqual(headers["Host"], "127.0.0.1:8765")
        self.assertEqual(headers["X-Chintu-Local"], "s3cret")
        self.assertNotIn("Origin", headers)

    def test_request_injects_fresh_secret_and_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_secret(repo, "one")
            seen = []

            def transport(port, method, path, headers, body, timeout):
                seen.append((port, method, path, dict(headers), body))
                return 200, b'{"ok": true}'

            client = local.LocalClient(repo, 9000, transport=transport)
            client.get_json("/local/status")
            write_secret(repo, "two")
            client.post_json("/local/clients/c1/revoke")
        self.assertEqual(seen[0][:3], (9000, "GET", "/local/status"))
        self.assertEqual(seen[0][3]["X-Chintu-Local"], "one")
        self.assertEqual(seen[0][3]["Host"], "127.0.0.1:9000")
        self.assertIsNone(seen[0][4])
        self.assertEqual(seen[1][:3], (9000, "POST", "/local/clients/c1/revoke"))
        self.assertEqual(seen[1][3]["X-Chintu-Local"], "two")
        self.assertEqual(seen[1][4], b"")
        for entry in seen:
            self.assertNotIn("Origin", entry[3])

    def test_pair_url_is_built_locally_and_quoted(self):
        self.assertEqual(local.pair_url(8765, "a/b c+d"), "http://127.0.0.1:8765/pair?key=a%2Fb%20c%2Bd")

    def test_no_request_without_secret(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            client = local.LocalClient(tmp, 9000, transport=lambda *a: calls.append(a) or (200, b"{}"))
            with self.assertRaises(local.SecretMissing):
                client.status()
        self.assertEqual(calls, [])


class ErrorMappingTests(unittest.TestCase):
    def _client(self, code, body=b"{}"):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        write_secret(Path(self.tmp.name), "s")
        return local.LocalClient(self.tmp.name, 9000, transport=lambda *a: (code, body))

    def test_403_is_refused(self):
        with self.assertRaises(local.Refused) as caught:
            self._client(403).status()
        self.assertEqual(caught.exception.kind, "refused")
        self.assertIn("9000", str(caught.exception))

    def test_other_http_error_keeps_code_and_body(self):
        with self.assertRaises(local.HttpError) as caught:
            self._client(503, b'{"error": {"message": "no tailscale"}}').request("POST", "/local/pairing-code")
        self.assertEqual((caught.exception.code, caught.exception.kind), (503, "http"))
        self.assertIn(b"no tailscale", caught.exception.body)

    def test_wrong_service_or_bad_json_is_not_chintu(self):
        with self.assertRaises(local.NotChintu):
            self._client(200, b'{"service": "other"}').status()
        with self.assertRaises(local.NotChintu):
            self._client(200, b"<html>").status()

    def test_connection_failure_is_unreachable(self):
        def transport(*args):
            raise ConnectionRefusedError(61, "Connection refused")
        with tempfile.TemporaryDirectory() as tmp:
            write_secret(Path(tmp), "s")
            with self.assertRaises(local.Unreachable) as caught:
                local.LocalClient(tmp, 9000, transport=transport).status()
        self.assertEqual(caught.exception.kind, "unreachable")

    def test_revoke_rejects_malformed_ids(self):
        client = self._client(200)
        with self.assertRaises(ValueError):
            client.revoke("")
        with self.assertRaises(ValueError):
            client.revoke("../x")


class ListenerVerificationTests(unittest.TestCase):
    """Real HTTP over an ephemeral port: the same proof checks the gateway makes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def test_right_secret_is_verified(self):
        write_secret(self.repo, "correct-secret")
        with FakeGateway(secret="correct-secret") as gateway:
            payload = local.LocalClient(self.repo, gateway.port).status()
            method, path, headers = gateway.requests[0]
        self.assertEqual(payload["service"], "chintu")
        self.assertEqual((method, path), ("GET", "/local/status"))
        self.assertEqual(headers["Host"], "127.0.0.1:%d" % gateway.port)
        self.assertEqual(headers["X-Chintu-Local"], "correct-secret")
        self.assertNotIn("Origin", headers)

    def test_wrong_secret_is_refused(self):
        write_secret(self.repo, "stale-secret")
        with FakeGateway(secret="correct-secret") as gateway:
            with self.assertRaises(local.Refused):
                local.LocalClient(self.repo, gateway.port).status()

    def test_other_service_on_port_is_not_chintu(self):
        write_secret(self.repo, "correct-secret")
        with FakeGateway(secret="correct-secret", service="someone-else") as gateway:
            with self.assertRaises(local.NotChintu):
                local.LocalClient(self.repo, gateway.port).status()

    def test_nothing_listening_is_unreachable(self):
        write_secret(self.repo, "s")
        with self.assertRaises(local.Unreachable):
            local.LocalClient(self.repo, _free_port(), timeout=1.0).status()

    def test_clients_listing_and_revoke(self):
        write_secret(self.repo, "correct-secret")
        with FakeGateway(secret="correct-secret") as gateway:
            client = local.LocalClient(self.repo, gateway.port)
            clients = client.clients()
            self.assertEqual([c.name for c in clients], ["Pixel 8", "Old phone"])
            self.assertEqual(clients[0].expires_at, 10 * 86400.0)
            client.revoke("c2")
            self.assertEqual(gateway.revoked, ["c2"])
            self.assertEqual([c.client_id for c in client.clients()], ["c1"])
            self.assertIn("/local/clients/c2/revoke", gateway.paths("POST"))
            with self.assertRaises(local.HttpError) as caught:
                client.revoke("c2")  # already gone
        self.assertEqual(caught.exception.code, 404)


class ParsingTests(unittest.TestCase):
    def test_parse_clients_accepts_list_or_wrapper_and_skips_junk(self):
        items = [{"client_id": "a", "name": "A", "connected": True, "expires_at": "2026-10-20T00:00:00Z"},
                 "junk", {"name": "no id"}]
        for payload in (items, {"clients": items}):
            clients = local.parse_clients(payload)
            self.assertEqual(len(clients), 1)
            self.assertEqual(clients[0].name, "A")
            self.assertAlmostEqual(clients[0].expires_at, 1792454400.0)
        with self.assertRaises(local.NotChintu):
            local.parse_clients("nope")

    def test_expiry_text(self):
        now = 1000.0
        self.assertEqual(local.expiry_text(None, now), "")
        self.assertEqual(local.expiry_text(now - 1, now), "expired")
        self.assertEqual(local.expiry_text(now + 3600 * 5, now), "expires in 5 hours")
        self.assertEqual(local.expiry_text(now + 86400, now), "expires in 1 day")
        self.assertEqual(local.expiry_text(now + 86400 * 2.5, now), "expires in 3 days")


if __name__ == "__main__":
    unittest.main()
