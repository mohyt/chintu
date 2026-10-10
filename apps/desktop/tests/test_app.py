"""Menu and CLI logic exercised with a stand-in for pystray, so no display is needed."""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

from chintu_desktop import app as desktop_app
from chintu_desktop import status, tailscale
from chintu_desktop.status import Presence

PAYLOAD = {
    "clients": [{"client_id": "c1", "name": "Pixel 8", "paired_at": 1.0, "last_seen": 100.0, "connected": True}],
    "phone_connected": True, "active_tasks": 1, "total_tasks": 4, "service": "chintu", "provider": "claude-agent",
    "tailscale": {"status": "available", "reason": None, "gateway_url": "https://mac.tail.ts.net", "name": "mac"},
    "pair_url": "http://127.0.0.1:8765/pair",
}


class FakeIcon:
    HAS_NOTIFICATION = False

    def __init__(self, name, image, title, menu=None):
        self.name, self.icon, self.title, self.menu = name, image, title, menu
        self.updates = 0

    def update_menu(self):
        self.updates += 1


class FakeMenuItem:
    def __init__(self, text, action, enabled=True, visible=True, default=False, **_):
        self._text, self.action, self.enabled, self.default = text, action, enabled, default

    @property
    def text(self):
        return self._text(self) if callable(self._text) else self._text


class FakeMenu:
    SEPARATOR = "---"

    def __init__(self, generator):
        self.generator = generator

    def items(self):
        return list(self.generator())


FAKE_PYSTRAY = types.SimpleNamespace(Icon=FakeIcon, MenuItem=FakeMenuItem, Menu=FakeMenu)


def _args(repo, **overrides):
    values = dict(port=8765, repo=Path(repo), gateway_args=None, interval=5.0, mono=False)
    values.update(overrides)
    return types.SimpleNamespace(**values)


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Run UI updates inline: there is no AppKit/GLib loop in the tests.
        self.app = desktop_app.DesktopApp(FAKE_PYSTRAY, _args(self.tmp.name), call_on_main=desktop_app._call_directly)
        # Never touch the real tailscale CLI or port from the tests.
        self.app.supervisor._listening = lambda port: False
        self.app.tailscale_binary = None
        tailscale_stub = lambda port, binary: tailscale.ServeStatus(True, False, None, None)  # noqa: E731
        self._real_serve_status = desktop_app.tailscale.serve_status
        desktop_app.tailscale.serve_status = tailscale_stub
        self.addCleanup(setattr, desktop_app.tailscale, "serve_status", self._real_serve_status)

    def _texts(self):
        return [item if isinstance(item, str) else item.text for item in self.app.icon.menu.items()]

    def test_menu_when_daemon_down(self):
        texts = self._texts()
        self.assertIn("Phone: unknown (gateway not running)", texts)
        self.assertIn("Gateway: not running", texts)
        self.assertIn("Start daemon", texts)
        self.assertNotIn("Stop daemon", texts)
        self.assertIn("Quit Chintu Desktop", texts)
        qr = next(item for item in self.app.icon.menu.items() if getattr(item, "text", None) == "Show pairing QR")
        self.assertFalse(qr.enabled)

    def test_menu_after_connected_poll(self):
        self.app._on_state(status.derive_state(PAYLOAD, now=0))
        texts = self._texts()
        self.assertIn("Phone: Pixel 8 connected", texts)
        self.assertIn("Active tasks: 1 (total 4)", texts)
        self.assertIn("Tailnet URL: https://mac.tail.ts.net", texts)
        self.assertIn("Tailscale Serve: off", texts)
        self.assertIn("Enable Tailscale Serve", texts)
        self.assertNotIn("Start daemon", texts)  # a gateway answers, so there is nothing to start
        self.assertIs(self.app.icon.icon, self.app.images[Presence.CONNECTED])
        self.assertEqual(self.app.icon.title, "Chintu: Pixel 8 connected")
        self.assertGreaterEqual(self.app.icon.updates, 1)

    def test_icon_only_changes_when_presence_changes(self):
        self.app._on_state(status.derive_state(PAYLOAD, now=0))
        first = self.app.icon.icon
        self.app._on_state(status.derive_state(dict(PAYLOAD, active_tasks=3), now=1))
        self.assertIs(self.app.icon.icon, first)
        self.app._on_state(status.derive_state(None, "down", now=2))
        self.assertIs(self.app.icon.icon, self.app.images[Presence.DOWN])


class MainThreadCallerTests(unittest.TestCase):
    def test_direct_caller_runs_inline(self):
        seen = []
        desktop_app._call_directly(seen.append, 1)
        self.assertEqual(seen, [1])

    def test_platform_caller_is_callable(self):
        caller = desktop_app.main_thread_caller()
        self.assertTrue(callable(caller))
        if sys.platform != "darwin":
            self.assertIs(caller, desktop_app._call_directly)
        else:
            # On macOS pyobjc's AppHelper.callAfter is used when pystray's backend is installed.
            self.assertNotEqual(caller.__name__, "_call_directly")


class CliTests(unittest.TestCase):
    def test_parser_defaults_and_subcommands(self):
        parser = desktop_app.build_parser()
        args = parser.parse_args([])
        self.assertEqual((args.port, args.command, args.mono), (8765, None, False))
        args = parser.parse_args(["--port", "9000", "--gateway-args", "--database x serve", "install-autostart"])
        self.assertEqual((args.port, args.command, args.gateway_args), (9000, "install-autostart", "--database x serve"))

    def test_status_snapshot_is_json(self):
        with tempfile.TemporaryDirectory() as repo:
            real = desktop_app.tailscale.serve_status
            desktop_app.tailscale.serve_status = lambda port, binary=None: tailscale.ServeStatus(False, False, None, "x")
            try:
                # Port 1 is reserved and never a Chintu gateway, so this is a "down" snapshot.
                data = desktop_app._snapshot(_args(repo, port=1))
            finally:
                desktop_app.tailscale.serve_status = real
        self.assertEqual(data["presence"], "down")
        self.assertEqual(data["gateway_command"][0], str(Path(repo) / ".venv" / "bin" / "chintu-gateway"))
        json.dumps(data, default=str)


if __name__ == "__main__":
    unittest.main()
