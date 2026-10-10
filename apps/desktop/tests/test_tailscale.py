import subprocess
import unittest

from chintu_desktop import tailscale

# Captured from `tailscale serve status` 1.104 on macOS.
SAMPLE = """https://mohyt-mac.tail4ee147.ts.net (tailnet only)
|-- / proxy http://127.0.0.1:8765

"""

SAMPLE_MULTI = """https://host.tail.ts.net (tailnet only)
|-- /          proxy http://127.0.0.1:3000
|-- /other     proxy http://localhost:8765

https://host.tail.ts.net:8443 (Funnel on)
|-- /          proxy http://127.0.0.1:9000
"""


class ParseTests(unittest.TestCase):
    def test_enabled_for_our_port(self):
        self.assertEqual(tailscale.parse_serve_status(SAMPLE, 8765), (True, "https://mohyt-mac.tail4ee147.ts.net"))

    def test_other_port_is_not_ours(self):
        self.assertEqual(tailscale.parse_serve_status(SAMPLE, 9000), (False, None))

    def test_empty_or_no_config(self):
        self.assertEqual(tailscale.parse_serve_status("", 8765), (False, None))
        self.assertEqual(tailscale.parse_serve_status("No serve config\n", 8765), (False, None))

    def test_multiple_handlers_pick_matching_port(self):
        self.assertEqual(tailscale.parse_serve_status(SAMPLE_MULTI, 8765), (True, "https://host.tail.ts.net"))
        self.assertEqual(tailscale.parse_serve_status(SAMPLE_MULTI, 9000), (True, "https://host.tail.ts.net:8443"))


class BinaryDiscoveryTests(unittest.TestCase):
    def test_path_wins(self):
        found = tailscale.find_binary(which=lambda name: "/usr/local/bin/tailscale", exists=lambda p: True,
                                      platform="darwin")
        self.assertEqual(found, "/usr/local/bin/tailscale")

    def test_mac_app_bundle_fallback(self):
        found = tailscale.find_binary(which=lambda name: None, exists=lambda p: p == tailscale.MAC_APP_BINARY,
                                      platform="darwin")
        self.assertEqual(found, tailscale.MAC_APP_BINARY)

    def test_linux_without_cli(self):
        self.assertIsNone(tailscale.find_binary(which=lambda name: None, exists=lambda p: True, platform="linux"))


class CommandTests(unittest.TestCase):
    def test_serve_status_runs_read_only_command(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=SAMPLE, stderr="")

        result = tailscale.serve_status(8765, binary="/bin/tailscale", runner=runner)
        self.assertEqual(calls, [["/bin/tailscale", "serve", "status"]])
        self.assertTrue(result.available)
        self.assertTrue(result.enabled)
        self.assertEqual(result.url, "https://mohyt-mac.tail4ee147.ts.net")

    def test_serve_status_without_binary(self):
        real = tailscale.find_binary
        tailscale.find_binary = lambda: None
        try:
            result = tailscale.serve_status(8765, binary=None, runner=lambda *a, **k: self.fail("must not run"))
        finally:
            tailscale.find_binary = real
        self.assertFalse(result.available)
        self.assertIn("not found", result.message)

    def test_serve_status_failure_message(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="failed to connect to local tailscaled\n")
        result = tailscale.serve_status(8765, binary="/bin/tailscale", runner=runner)
        self.assertFalse(result.available)
        self.assertEqual(result.message, "failed to connect to local tailscaled")

    def test_enable_serve_command(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="Available within your tailnet:\n", stderr="")

        ok, message = tailscale.enable_serve(8765, binary="/bin/tailscale", runner=runner)
        self.assertTrue(ok)
        self.assertEqual(calls, [["/bin/tailscale", "serve", "--bg", "8765"]])
        self.assertIn("Available", message)

    def test_enable_serve_failure(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="serve is not enabled for this tailnet\n")
        ok, message = tailscale.enable_serve(8765, binary="/bin/tailscale", runner=runner)
        self.assertFalse(ok)
        self.assertIn("not enabled", message)


if __name__ == "__main__":
    unittest.main()
