import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from chintu_desktop import local, pairing
from fake_gateway import FakeGateway, write_secret

PAYLOAD = '{"chintu":1,"url":"https://mac.tail.ts.net","name":"Mac","aliases":"","secret":"not-a-real-secret"}'
RESPONSE = {"payload": PAYLOAD, "url": "https://mac.tail.ts.net", "name": "Mac", "expires_in": 600}


class ParseTests(unittest.TestCase):
    def test_parses_response(self):
        code = pairing.parse_pairing_code(json.dumps(RESPONSE).encode())
        self.assertEqual(code.payload, PAYLOAD)
        self.assertEqual(code.url, "https://mac.tail.ts.net")
        self.assertEqual(code.name, "Mac")
        self.assertEqual(code.expires_in, 600)

    def test_rejects_missing_payload(self):
        with self.assertRaises(pairing.PairingError):
            pairing.parse_pairing_code(b'{"url": "x"}')
        with self.assertRaises(pairing.PairingError):
            pairing.parse_pairing_code(b"not json")
        with self.assertRaises(pairing.PairingError):
            pairing.parse_pairing_code(b"[]")


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def _client(self, code, body=b"{}"):
        write_secret(self.repo, "s")
        return local.LocalClient(self.repo, 8765, transport=lambda *a: (code, body))

    def test_posts_to_proven_loopback_endpoint(self):
        write_secret(self.repo, "correct-secret")
        with FakeGateway(secret="correct-secret", pairing_body=RESPONSE) as gateway:
            code = pairing.request_pairing_code(gateway.port, self.repo)
            method, path, headers = gateway.requests[0]
        self.assertEqual((method, path), ("POST", "/local/pairing-code"))
        self.assertEqual(headers["X-Chintu-Local"], "correct-secret")
        self.assertEqual(headers["Host"], "127.0.0.1:%d" % gateway.port)
        self.assertEqual(code.name, "Mac")

    def test_503_surfaces_gateway_message(self):
        body = json.dumps({"error": {"code": "unavailable", "message": "Tailscale is not connected."}}).encode()
        with self.assertRaises(pairing.PairingError) as caught:
            pairing.request_pairing_code(8765, self.repo, client=self._client(503, body))
        self.assertEqual(str(caught.exception), "Tailscale is not connected.")

    def test_403_names_the_proof(self):
        with self.assertRaises(pairing.PairingError) as caught:
            pairing.request_pairing_code(8765, self.repo, client=self._client(403))
        self.assertIn("refused the local proof", str(caught.exception))

    def test_missing_secret_is_readable(self):
        with self.assertRaises(pairing.PairingError) as caught:
            pairing.request_pairing_code(8765, self.repo)
        self.assertIn("not started from this repository", str(caught.exception))

    def test_connection_refused_is_readable(self):
        def transport(*args):
            raise ConnectionRefusedError(61, "Connection refused")
        write_secret(self.repo, "s")
        client = local.LocalClient(self.repo, 8765, transport=transport)
        with self.assertRaises(pairing.PairingError) as caught:
            pairing.request_pairing_code(8765, self.repo, client=client)
        self.assertIn("not running on port 8765", str(caught.exception))


class RenderTests(unittest.TestCase):
    def test_png_bytes_with_valid_header_and_square_size(self):
        png = pairing.render_qr_png(PAYLOAD, scale=4)
        self.assertTrue(png)
        self.assertTrue(png.startswith(pairing.PNG_SIGNATURE))
        # IHDR chunk: 4-byte length, "IHDR", width, height (big-endian).
        self.assertEqual(png[12:16], b"IHDR")
        width, height = struct.unpack(">II", png[16:24])
        self.assertEqual(width, height)
        self.assertGreater(width, 100)
        # The IHDR CRC must match, proving the stream is a well-formed PNG.
        crc = struct.unpack(">I", png[29:33])[0]
        self.assertEqual(crc, zlib.crc32(png[12:29]) & 0xFFFFFFFF)

    def test_countdown_text(self):
        self.assertEqual(pairing.countdown_text(600), "Expires in 10:00")
        self.assertEqual(pairing.countdown_text(61), "Expires in 1:01")
        self.assertEqual(pairing.countdown_text(0), "Expired. Press Refresh for a new code.")


if __name__ == "__main__":
    unittest.main()
