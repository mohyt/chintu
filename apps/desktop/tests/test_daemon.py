import json
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

from chintu_desktop.daemon import (FALLBACK_PORTS, GatewaySupervisor, port_is_listening, relocate_if_taken,
                                   resolve_port, save_port, saved_port, select_gateway_args)
from chintu_desktop.layout import Layout
from fake_gateway import repo_layout


def _installed_layout(root):
    """An installed layout under a temporary prefix: gateway at <root>/venv/bin, data in <root>/data."""
    root = Path(root)
    return Layout("installed", root / "data", (str(root / "venv" / "bin" / "chintu-gateway"),),
                  root / "share" / "openapi.json")


class ArgSelectionTests(unittest.TestCase):
    def test_provider_args_when_local_config_exists(self):
        with tempfile.TemporaryDirectory() as root:
            layout = repo_layout(root)
            layout.config.parent.mkdir()
            layout.config.write_text("{}")
            args = select_gateway_args(layout, None, 8765)
        self.assertEqual(args, ["--config", str(layout.config), "serve", "--provider", "claude-agent",
                               "--port", "8765"])
        self.assertTrue(Path(args[1]).is_absolute())

    def test_scripted_fallback_without_config(self):
        with tempfile.TemporaryDirectory() as root:
            layout = repo_layout(root)
            args = select_gateway_args(layout, None, 8765)
        self.assertEqual(args, ["--database", str(layout.database), "serve", "--scripted",
                               "--contract", str(layout.contract), "--port", "8765"])

    def test_scripted_fallback_omits_unknown_contract(self):
        layout = Layout("installed", Path("/data"), ("/venv/bin/chintu-gateway",), None)
        args = select_gateway_args(layout, None, 8765, config_exists=False)
        self.assertEqual(args, ["--database", "/data/chintu.sqlite3", "serve", "--scripted", "--port", "8765"])

    def test_explicit_args_are_shell_split_and_port_added(self):
        layout = Layout("installed", Path("/nowhere"), ("/nowhere/chintu-gateway",), None)
        args = select_gateway_args(layout, "--config 'var/my config.json' serve --provider codex", 9001)
        self.assertEqual(args, ["--config", "var/my config.json", "serve", "--provider", "codex", "--port", "9001"])

    def test_explicit_port_is_respected(self):
        layout = Layout("installed", Path("/nowhere"), ("/nowhere/chintu-gateway",), None)
        args = select_gateway_args(layout, "--database x serve --port 7000", 9001)
        self.assertEqual(args[-2:], ["--port", "7000"])


class PortProbeTests(unittest.TestCase):
    def test_detects_real_listener_and_free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            self.assertTrue(port_is_listening(port))
        # Closed now; the same port should read as free.
        self.assertFalse(port_is_listening(port))


SCRIPT = "#!/bin/sh\necho started \"$@\"\nsleep 30\n"


def _write_script(path, script=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script or SCRIPT)
    path.chmod(0o755)


class SupervisorTests(unittest.TestCase):
    def _supervisor(self, root, listening=False, script=None, layout=None):
        layout = layout or repo_layout(root)
        _write_script(layout.gateway_executable, script)
        return GatewaySupervisor(layout, 8765, None, listening=lambda port: listening)

    def test_never_starts_second_process_on_a_busy_port(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = self._supervisor(root, listening=True)
            message = supervisor.start()
            self.assertIn("already in use", message)
            self.assertFalse(supervisor.child_running())
            self.assertIn("not started here", supervisor.stop())

    def test_describe_never_calls_an_unverified_listener_the_gateway(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = self._supervisor(root, listening=True)
            self.assertEqual(supervisor.describe(verified=True), "Gateway: listening on :8765 (started elsewhere)")
            self.assertEqual(supervisor.describe(verified=False), "Port 8765 is in use by something that isn't Chintu")
            self.assertEqual(supervisor.describe(), "Gateway: something listens on :8765 (not verified yet)")

    def test_missing_executable_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = GatewaySupervisor(repo_layout(root), 8765, None, listening=lambda port: False)
            message = supervisor.start()
            self.assertIn("not found", message)
            self.assertIn(str(supervisor.layout.gateway_executable), message)
            self.assertEqual(supervisor.describe(), "Gateway: not running")

    def test_command_is_the_layout_prefix_plus_args(self):
        layout = Layout("bundle", Path("/data"), ("/Applications/Chintu.app/Contents/MacOS/Chintu", "gateway"),
                        Path("/bundle/contracts/openapi.json"))
        supervisor = GatewaySupervisor(layout, 8790, None, listening=lambda port: False)
        self.assertEqual(supervisor.executable, Path("/Applications/Chintu.app/Contents/MacOS/Chintu"))
        self.assertEqual(supervisor.command()[:2], ["/Applications/Chintu.app/Contents/MacOS/Chintu", "gateway"])
        self.assertEqual(supervisor.command()[2:], ["--database", "/data/chintu.sqlite3", "serve", "--scripted",
                                                    "--contract", "/bundle/contracts/openapi.json", "--port", "8790"])
        self.assertEqual(supervisor.log_path, Path("/data/desktop-gateway.log"))

    @unittest.skipIf(sys.platform == "win32", "POSIX process groups")
    def test_start_logs_and_stop_terminates_child(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = self._supervisor(root)
            message = supervisor.start()
            self.assertIn("Started the gateway", message)
            self.assertTrue(supervisor.child_running())
            self.assertIn("started here", supervisor.describe())
            deadline = time.time() + 5
            while time.time() < deadline and "started" not in supervisor.log_path.read_text():
                time.sleep(0.05)
            log = supervisor.log_path.read_text()
            self.assertIn("chintu-desktop start:", log)
            self.assertIn("started --database %s serve --scripted" % supervisor.layout.database, log)
            self.assertIn("Stopped the gateway", supervisor.stop(grace_seconds=3))
            self.assertFalse(supervisor.child_running())
            self.assertEqual(supervisor.log_path, Path(root).resolve() / "var" / "desktop-gateway.log")
            # A repository checkout never gets a generated config: the scripted fixture stays the fallback.
            self.assertFalse(supervisor.layout.config.exists())

    @unittest.skipIf(sys.platform == "win32", "POSIX process groups")
    def test_fresh_install_gets_a_config_and_the_real_provider(self):
        with tempfile.TemporaryDirectory() as root:
            layout = _installed_layout(root)
            supervisor = self._supervisor(root, layout=layout)
            self.assertFalse(layout.home.exists())
            self.assertIn("Started the gateway", supervisor.start())
            try:
                self.assertTrue(layout.config.is_file())
                self.assertEqual(layout.home.stat().st_mode & 0o777, 0o700)
                self.assertEqual(layout.config.stat().st_mode & 0o777, 0o600)
                self.assertTrue(json.loads(layout.config.read_text())["runtimes"]["claude-agent"]["enabled"])
                deadline = time.time() + 5
                while time.time() < deadline and "started" not in supervisor.log_path.read_text():
                    time.sleep(0.05)
                self.assertIn("started --config %s serve --provider claude-agent --port 8765" % layout.config,
                              supervisor.log_path.read_text())
            finally:
                supervisor.stop(grace_seconds=3)

    def test_exit_code_shown_after_crash(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = self._supervisor(root, script="#!/bin/sh\nexit 3\n")
            supervisor.start()
            deadline = time.time() + 5
            while time.time() < deadline and supervisor.child_running():
                time.sleep(0.05)
            self.assertEqual(supervisor.describe(), "Gateway: exited with code 3 (see log)")


class PortChoiceTests(unittest.TestCase):
    def test_saved_port_round_trip_and_precedence(self):
        with tempfile.TemporaryDirectory() as root:
            layout = _installed_layout(root)
            self.assertIsNone(saved_port(layout))
            self.assertEqual(resolve_port(layout), 8765)
            save_port(layout, 8766)
            self.assertEqual(saved_port(layout), 8766)
            self.assertEqual(resolve_port(layout), 8766)
            self.assertEqual(resolve_port(layout, 9000), 9000)
            self.assertEqual(layout.settings.stat().st_mode & 0o777, 0o600)

    def test_unreadable_settings_are_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            layout = _installed_layout(root)
            layout.ensure_home()
            for text in ("not json", "[]", '{"port": "8766"}', '{"port": 70000}'):
                layout.settings.write_text(text)
                self.assertIsNone(saved_port(layout), text)
            save_port(layout, 8770)  # rewrites a broken file instead of failing
            self.assertEqual(saved_port(layout), 8770)

    def test_repo_layout_never_saves(self):
        with tempfile.TemporaryDirectory() as root:
            layout = repo_layout(root)
            save_port(layout, 8766)
            self.assertFalse(layout.settings.exists())

    def test_free_or_own_port_is_kept(self):
        with tempfile.TemporaryDirectory() as root:
            layout = _installed_layout(root)
            self.assertEqual(relocate_if_taken(layout, 8765, lambda p: self.fail("not probed"),
                                               listening=lambda p: False), (8765, None))
            self.assertEqual(relocate_if_taken(layout, 8765, lambda p: True, listening=lambda p: True), (8765, None))
            self.assertFalse(layout.settings.exists())

    def test_foreign_listener_moves_to_next_free_port_and_saves_it(self):
        with tempfile.TemporaryDirectory() as root:
            layout = _installed_layout(root)
            busy = {8765, 8766}
            port, notice = relocate_if_taken(layout, 8765, lambda p: False, listening=lambda p: p in busy)
            self.assertEqual(port, 8767)
            self.assertIn("8765 is used by another program", notice)
            self.assertEqual(saved_port(layout), 8767)

    def test_every_fallback_busy_keeps_port_and_explains(self):
        with tempfile.TemporaryDirectory() as root:
            layout = _installed_layout(root)
            port, notice = relocate_if_taken(layout, 8765, lambda p: False, listening=lambda p: True)
            self.assertEqual(port, 8765)
            self.assertIn("%d-%d are busy" % (FALLBACK_PORTS[0], FALLBACK_PORTS[-1]), notice)
            self.assertFalse(layout.settings.exists())


if __name__ == "__main__":
    unittest.main()
