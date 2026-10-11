import unittest

from chintu_gateway import cli


class TailscaleCheckTests(unittest.TestCase):
    def test_cli_on_path(self):
        self.assertTrue(cli.tailscale_installed(which=lambda name: "/usr/bin/tailscale", exists=lambda p: False, platform="linux"))

    def test_mac_app_bundle_counts(self):
        self.assertTrue(cli.tailscale_installed(which=lambda name: None, exists=lambda p: p == cli.MAC_TAILSCALE_APP_BINARY,
                                                platform="darwin"))

    def test_missing_everywhere(self):
        self.assertFalse(cli.tailscale_installed(which=lambda name: None, exists=lambda p: True, platform="linux"))
        self.assertFalse(cli.tailscale_installed(which=lambda name: None, exists=lambda p: False, platform="darwin"))
