"""Menu and CLI logic exercised with a stand-in for pystray, so no display is needed."""

import json
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

from chintu_desktop import app as desktop_app
from chintu_desktop import local, status, tailscale
from chintu_desktop.status import Presence
from fake_gateway import FakeGateway, write_secret

PAYLOAD = {
    "clients": [{"client_id": "c1", "name": "Pixel 8", "paired_at": 1.0, "last_seen": 100.0, "connected": True}],
    "phone_connected": True, "active_tasks": 1, "total_tasks": 4, "service": "chintu", "provider": "claude-agent",
    "tailscale": {"status": "available", "reason": None, "gateway_url": "https://mac.tail.ts.net", "name": "mac"},
    "pair_url": "http://evil.example/pair",
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

    @property
    def submenu(self):
        return self.action if isinstance(self.action, FakeMenu) else None

    def click(self, icon=None):
        return self.action(icon, self)


class FakeMenu:
    SEPARATOR = "---"

    def __init__(self, *items):
        self._items = items

    def items(self):
        if len(self._items) == 1 and callable(self._items[0]) and not isinstance(self._items[0], FakeMenuItem):
            return list(self._items[0]())
        return list(self._items)


FAKE_PYSTRAY = types.SimpleNamespace(Icon=FakeIcon, MenuItem=FakeMenuItem, Menu=FakeMenu)


def _args(repo, **overrides):
    values = dict(port=8765, repo=Path(repo), gateway_args=None, interval=5.0, mono=False)
    values.update(overrides)
    return types.SimpleNamespace(**values)


def _texts(menu):
    return [item if isinstance(item, str) else item.text for item in menu.items()]


def _find(menu, text):
    for item in menu.items():
        if not isinstance(item, str) and item.text == text:
            return item
    raise AssertionError("no menu item %r in %r" % (text, _texts(menu)))


class _AppCase(unittest.TestCase):
    """A DesktopApp with inline UI updates, no tailscale CLI and a controllable port probe."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.listening = False
        self._serve = tailscale.ServeStatus(True, False, None, None)
        self._real_serve_status = desktop_app.tailscale.serve_status
        desktop_app.tailscale.serve_status = lambda port, binary=None: self._serve
        self.addCleanup(setattr, desktop_app.tailscale, "serve_status", self._real_serve_status)
        self.opened = []
        self.app = self._make_app()

    def _make_app(self, **overrides):
        app = desktop_app.DesktopApp(FAKE_PYSTRAY, _args(self.repo, **overrides), call_on_main=desktop_app._call_directly)
        app.supervisor._listening = lambda port: self.listening
        app.tailscale_binary = None
        app._open = self.opened.append
        return app

    def _connected(self, clients=None):
        self.listening = True
        self.app._on_state(status.derive_state(PAYLOAD, now=0, clients=clients))

    def _menu(self):
        return self.app.icon.menu

    def _run_pending_threads(self):
        for thread in threading.enumerate():
            if thread.name == "chintu-desktop-action":
                thread.join(timeout=5)


class MenuTests(_AppCase):
    def test_menu_when_daemon_down(self):
        texts = _texts(self._menu())
        self.assertIn("Phone: unknown (gateway not running)", texts)
        self.assertIn("Gateway: not running", texts)
        self.assertIn("Start daemon", texts)
        self.assertNotIn("Stop daemon", texts)
        self.assertNotIn("Paired phones (1)", texts)
        self.assertIn("Quit Chintu Desktop", texts)
        self.assertFalse(_find(self._menu(), "Show pairing QR").enabled)

    def test_menu_after_connected_poll(self):
        self._connected()
        texts = _texts(self._menu())
        self.assertIn("Phone: Pixel 8 connected", texts)
        self.assertIn("Gateway: listening on :8765 (started elsewhere)", texts)
        self.assertIn("Active tasks: 1 (total 4)", texts)
        self.assertIn("Tailnet URL: https://mac.tail.ts.net", texts)
        self.assertIn("Tailscale Serve: off", texts)
        self.assertIn("Enable Tailscale Serve…", texts)
        self.assertIn("Paired phones (1)", texts)
        self.assertNotIn("Start daemon", texts)  # a gateway answers, so there is nothing to start
        self.assertTrue(_find(self._menu(), "Show pairing QR").enabled)
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


class UnverifiedListenerTests(_AppCase):
    """F8: a port that accepts connections but does not prove itself is never treated as the gateway."""

    def test_port_in_use_by_something_else_offers_only_retry_and_quit(self):
        self.listening = True
        self.app._on_state(status.derive_state(None, local.Refused("refused"), now=0))
        texts = _texts(self._menu())
        self.assertIn("Port 8765 is in use by something that isn't Chintu", texts)
        self.assertIn("It refused the local proof (HTTP 403)", texts)
        actions = [t for t in texts if t not in ("---",) and _find(self._menu(), t).action is not None
                   and _find(self._menu(), t).enabled]
        self.assertEqual(actions, ["Retry", "Quit Chintu Desktop"])
        for absent in ("Start daemon", "Show pairing QR", "Open pairing page in browser", "Open console",
                       "Enable Tailscale Serve…", "Open daemon log"):
            self.assertNotIn(absent, texts)

    def test_wrong_service_is_also_not_chintu(self):
        self.listening = True
        self.app._on_state(status.derive_state(None, local.NotChintu("port 8765 is not a Chintu gateway"), now=0))
        texts = _texts(self._menu())
        self.assertIn("Port 8765 is in use by something that isn't Chintu", texts)
        self.assertIn("port 8765 is not a Chintu gateway", texts)
        self.assertIn("Retry", texts)

    def test_missing_secret_says_daemon_not_from_this_repo(self):
        self.listening = True
        self.app._on_state(status.derive_state(None, local.SecretMissing("no secret"), now=0))
        texts = _texts(self._menu())
        self.assertIn("Gateway: not running, or not started from this repository", texts)
        self.assertIn("Port 8765 is in use but var/local.secret is missing", texts)
        self.assertNotIn("Start daemon", texts)
        self.assertIn("Retry", texts)

    def test_retry_polls_again(self):
        self.listening = True
        self.app._on_state(status.derive_state(None, local.Refused("refused"), now=0))
        self.app.notice = "old"
        _find(self._menu(), "Retry").click()
        self.assertIsNone(self.app.notice)
        self.assertTrue(self.app.poller._wake.is_set())

    def test_free_port_still_offers_start(self):
        self.listening = False
        self.app._on_state(status.derive_state(None, local.SecretMissing("no secret"), now=0))
        texts = _texts(self._menu())
        self.assertIn("Gateway: not running", texts)
        self.assertIn("Start daemon", texts)

    def test_fault_line_falls_back_to_error_text(self):
        self.listening = True
        self.app._on_state(status.derive_state(None, local.Unreachable("gateway unreachable: timed out"), now=0))
        self.assertIn("gateway unreachable: timed out", _texts(self._menu()))


class PairUrlTests(_AppCase):
    def test_pair_page_url_is_built_locally_from_the_secret_file(self):
        self._connected()
        write_secret(self.repo, "first")
        _find(self._menu(), "Open pairing page in browser").click()
        write_secret(self.repo, "second")
        _find(self._menu(), "Open console").click()
        self.assertEqual(self.opened, ["http://127.0.0.1:8765/pair?key=first", "http://127.0.0.1:8765/pair?key=second"])
        self.assertNotIn("evil.example", "".join(self.opened))

    def test_pair_page_without_secret_reports_instead_of_opening(self):
        self._connected()
        _find(self._menu(), "Open pairing page in browser").click()
        self.assertEqual(self.opened, [])
        self.assertIn("not started from this repository", self.app.notice)


class PairedPhonesTests(_AppCase):
    def _clients(self, now=0.0):
        return local.parse_clients([
            {"client_id": "c1", "name": "Pixel 8", "connected": True, "expires_at": now + 10 * 86400},
            {"client_id": "c2", "name": "Old phone", "connected": False, "expires_at": now + 1.5 * 86400},
        ])

    def test_submenu_lists_clients_with_presence_and_expiry(self):
        now = time.time()
        self._connected(self._clients(now))
        phones = _find(self._menu(), "Paired phones (2)").submenu
        texts = _texts(phones)
        self.assertEqual(texts[0], "Pixel 8: connected, expires in 10 days")
        self.assertEqual(texts[1], "Old phone: away, expires in 2 days")
        self.assertIn("Revoke all phones…", texts)
        self.assertEqual(_texts(_find(phones, texts[0]).submenu), ["Revoke Pixel 8…"])

    def test_no_clients(self):
        self._connected([])
        phones = _find(self._menu(), "Paired phones: none").submenu
        self.assertEqual(_texts(phones), ["No phone is paired yet"])

    def test_revoke_needs_a_second_click_then_posts(self):
        write_secret(self.repo, "correct-secret")
        with FakeGateway(secret="correct-secret") as gateway:
            self.app = self._make_app(port=gateway.port)
            self._connected(self._clients())
            phones = _find(self._menu(), "Paired phones (2)").submenu
            pixel = _find(phones, _texts(phones)[0]).submenu
            _find(pixel, "Revoke Pixel 8…").click()
            self.assertEqual(gateway.paths("POST"), [])  # nothing sent yet
            self.assertIn("choose Confirm", self.app.notice)
            # The menu is rebuilt on the next open and now offers the confirmation.
            phones = _find(self._menu(), "Paired phones (2)").submenu
            pixel = _find(phones, _texts(phones)[0]).submenu
            self.assertEqual(_texts(pixel), ["Confirm revoke Pixel 8", "Cancel"])
            _find(pixel, "Confirm revoke Pixel 8").click()
            self._run_pending_threads()
            self.assertEqual(gateway.paths("POST"), ["/local/clients/c1/revoke"])
            self.assertEqual(gateway.revoked, ["c1"])
            self.assertEqual(self.app.notice, "Revoked Pixel 8.")
            self.assertTrue(self.app.poller._wake.is_set())
            headers = gateway.requests[-1][2]
            self.assertEqual(headers["X-Chintu-Local"], "correct-secret")
            self.assertEqual(headers["Host"], "127.0.0.1:%d" % gateway.port)

    def test_cancel_clears_the_pending_revoke(self):
        self._connected(self._clients())
        phones = _find(self._menu(), "Paired phones (2)").submenu
        pixel = _find(phones, _texts(phones)[0]).submenu
        _find(pixel, "Revoke Pixel 8…").click()
        phones = _find(self._menu(), "Paired phones (2)").submenu
        _find(_find(phones, _texts(phones)[0]).submenu, "Cancel").click()
        phones = _find(self._menu(), "Paired phones (2)").submenu
        self.assertEqual(_texts(_find(phones, _texts(phones)[0]).submenu), ["Revoke Pixel 8…"])

    def test_confirmation_expires(self):
        self._connected(self._clients())
        phones = _find(self._menu(), "Paired phones (2)").submenu
        _find(_find(phones, _texts(phones)[0]).submenu, "Revoke Pixel 8…").click()
        kind, target, asked = self.app._pending
        self.app._pending = (kind, target, asked - desktop_app.CONFIRM_WINDOW_SECONDS - 1)
        phones = _find(self._menu(), "Paired phones (2)").submenu
        self.assertEqual(_texts(_find(phones, _texts(phones)[0]).submenu), ["Revoke Pixel 8…"])

    def test_revoke_all_posts_each_client_after_confirmation(self):
        write_secret(self.repo, "correct-secret")
        with FakeGateway(secret="correct-secret") as gateway:
            self.app = self._make_app(port=gateway.port)
            self._connected(self._clients())
            phones = _find(self._menu(), "Paired phones (2)").submenu
            _find(phones, "Revoke all phones…").click()
            self.assertEqual(gateway.paths("POST"), [])
            phones = _find(self._menu(), "Paired phones (2)").submenu
            self.assertIn("Confirm revoke all 2 phones", _texts(phones))
            _find(phones, "Confirm revoke all 2 phones").click()
            self._run_pending_threads()
            self.assertEqual(sorted(gateway.revoked), ["c1", "c2"])
            self.assertEqual(self.app.notice, "Revoked Pixel 8, Old phone.")

    def test_revoke_failure_is_reported(self):
        write_secret(self.repo, "wrong-secret")
        with FakeGateway(secret="correct-secret") as gateway:
            self.app = self._make_app(port=gateway.port)
            self._connected(self._clients())
            phones = _find(self._menu(), "Paired phones (2)").submenu
            pixel = _find(phones, _texts(phones)[0]).submenu
            _find(pixel, "Revoke Pixel 8…").click()
            phones = _find(self._menu(), "Paired phones (2)").submenu
            _find(_find(phones, _texts(phones)[0]).submenu, "Confirm revoke Pixel 8").click()
            self._run_pending_threads()
            self.assertEqual(gateway.revoked, [])
            self.assertIn("Could not revoke Pixel 8", self.app.notice)
            self.assertIn("403", self.app.notice)


class ServeConfirmationTests(_AppCase):
    def setUp(self):
        super().setUp()
        self.enabled = []
        self._real_enable = desktop_app.tailscale.enable_serve
        desktop_app.tailscale.enable_serve = lambda port, binary=None: (self.enabled.append(port) or True, "Serve on")
        self.addCleanup(setattr, desktop_app.tailscale, "enable_serve", self._real_enable)

    def test_enable_serve_asks_first_then_runs_on_confirm(self):
        self._connected()
        _find(self._menu(), "Enable Tailscale Serve…").click()
        self.assertEqual(self.enabled, [])
        self.assertIn("every device on your tailnet", self.app.notice)
        texts = _texts(self._menu())
        self.assertIn("Confirm: expose the gateway to the whole tailnet", texts)
        self.assertIn("Cancel", texts)
        self.assertNotIn("Enable Tailscale Serve…", texts)
        _find(self._menu(), "Confirm: expose the gateway to the whole tailnet").click()
        self._run_pending_threads()
        self.assertEqual(self.enabled, [8765])
        self.assertEqual(self.app.notice, "Serve on")
        self.assertIn("Enable Tailscale Serve…", _texts(self._menu()))  # the stub still reports it off

    def test_cancel_leaves_serve_off(self):
        self._connected()
        _find(self._menu(), "Enable Tailscale Serve…").click()
        _find(self._menu(), "Cancel").click()
        self.assertEqual(self.enabled, [])
        self.assertIn("Enable Tailscale Serve…", _texts(self._menu()))

    def test_stale_confirm_does_not_run(self):
        self._connected()
        _find(self._menu(), "Enable Tailscale Serve…").click()
        confirm = _find(self._menu(), "Confirm: expose the gateway to the whole tailnet")
        self.app._pending = None  # e.g. cancelled from another click before this one landed
        confirm.click()
        self._run_pending_threads()
        self.assertEqual(self.enabled, [])
        self.assertIn("expired", self.app.notice)

    def test_no_prompt_when_serve_already_on(self):
        self._serve = tailscale.ServeStatus(True, True, "https://mac.tail.ts.net", None)
        self.app = self._make_app()
        self._connected()
        self.app.serve = self._serve
        texts = _texts(self._menu())
        self.assertIn("Tailscale Serve: on (https://mac.tail.ts.net)", texts)
        self.assertNotIn("Enable Tailscale Serve…", texts)


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

    def test_status_snapshot_without_secret(self):
        with tempfile.TemporaryDirectory() as repo:
            real = desktop_app.tailscale.serve_status
            desktop_app.tailscale.serve_status = lambda port, binary=None: tailscale.ServeStatus(False, False, None, "x")
            try:
                # No secret file in an empty repo: the snapshot says so without touching the network.
                data = desktop_app._snapshot(_args(repo, port=1))
            finally:
                desktop_app.tailscale.serve_status = real
        self.assertEqual((data["presence"], data["fault_kind"]), ("down", "no_secret"))
        self.assertEqual(data["secret_file"], str(Path(repo) / "var" / "local.secret"))
        self.assertEqual(data["gateway_command"][0], str(Path(repo) / ".venv" / "bin" / "chintu-gateway"))
        json.dumps(data, default=str)

    def test_status_snapshot_against_fake_gateway(self):
        with tempfile.TemporaryDirectory() as repo:
            write_secret(Path(repo), "correct-secret")
            real = desktop_app.tailscale.serve_status
            desktop_app.tailscale.serve_status = lambda port, binary=None: tailscale.ServeStatus(False, False, None, "x")
            try:
                with FakeGateway(secret="correct-secret") as gateway:
                    data = desktop_app._snapshot(_args(repo, port=gateway.port))
            finally:
                desktop_app.tailscale.serve_status = real
        self.assertEqual(data["presence"], "connected")
        self.assertEqual([c["name"] for c in data["clients"]], ["Pixel 8", "Old phone"])
        self.assertNotIn("pair_url", data)
        json.dumps(data, default=str)


if __name__ == "__main__":
    unittest.main()
