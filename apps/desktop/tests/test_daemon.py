import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

from chintu_desktop import daemon
from chintu_desktop.daemon import GatewaySupervisor, port_is_listening, select_gateway_args


class ArgSelectionTests(unittest.TestCase):
    def test_provider_args_when_local_config_exists(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "var" / "gateway.local.json"
            config.parent.mkdir()
            config.write_text("{}")
            args = select_gateway_args(root, None, 8765)
        self.assertEqual(args, ["--config", "var/gateway.local.json", "serve", "--provider", "claude-agent",
                               "--port", "8765"])

    def test_scripted_fallback_without_config(self):
        with tempfile.TemporaryDirectory() as root:
            args = select_gateway_args(root, None, 8765)
        self.assertEqual(args, ["--database", "var/chintu.sqlite3", "serve", "--scripted",
                               "--contract", "contracts/openapi.json", "--port", "8765"])

    def test_explicit_args_are_shell_split_and_port_added(self):
        args = select_gateway_args("/nowhere", "--config 'var/my config.json' serve --provider codex", 9001)
        self.assertEqual(args, ["--config", "var/my config.json", "serve", "--provider", "codex", "--port", "9001"])

    def test_explicit_port_is_respected(self):
        args = select_gateway_args("/nowhere", "--database x serve --port 7000", 9001)
        self.assertEqual(args[-2:], ["--port", "7000"])

    def test_default_repo_env_override(self):
        with tempfile.TemporaryDirectory() as root:
            old = os.environ.get("CHINTU_REPO")
            os.environ["CHINTU_REPO"] = root
            try:
                self.assertEqual(daemon.default_repo(), Path(root).resolve())
            finally:
                if old is None:
                    del os.environ["CHINTU_REPO"]
                else:
                    os.environ["CHINTU_REPO"] = old

    def test_default_repo_from_package_location(self):
        os.environ.pop("CHINTU_REPO", None)
        repo = daemon.default_repo()
        self.assertTrue((repo / "apps" / "desktop" / "pyproject.toml").is_file(), repo)


class PortProbeTests(unittest.TestCase):
    def test_detects_real_listener_and_free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            self.assertTrue(port_is_listening(port))
        # Closed now; the same port should read as free.
        self.assertFalse(port_is_listening(port))


class SupervisorTests(unittest.TestCase):
    def _supervisor(self, root, listening=False, script=None):
        repo = Path(root)
        exe = repo / ".venv" / "bin" / "chintu-gateway"
        exe.parent.mkdir(parents=True)
        exe.write_text(script or "#!/bin/sh\necho started \"$@\"\nsleep 30\n")
        exe.chmod(0o755)
        return GatewaySupervisor(repo, 8765, None, listening=lambda port: listening)

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
            supervisor = GatewaySupervisor(root, 8765, None, listening=lambda port: False)
            self.assertIn("not found", supervisor.start())
            self.assertEqual(supervisor.describe(), "Gateway: not running")

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
            self.assertIn("started --database var/chintu.sqlite3 serve --scripted", log)
            self.assertIn("Stopped the gateway", supervisor.stop(grace_seconds=3))
            self.assertFalse(supervisor.child_running())
            self.assertEqual(supervisor.log_path, Path(root) / "var" / "desktop-gateway.log")

    def test_exit_code_shown_after_crash(self):
        with tempfile.TemporaryDirectory() as root:
            supervisor = self._supervisor(root, script="#!/bin/sh\nexit 3\n")
            supervisor.start()
            deadline = time.time() + 5
            while time.time() < deadline and supervisor.child_running():
                time.sleep(0.05)
            self.assertEqual(supervisor.describe(), "Gateway: exited with code 3 (see log)")


if __name__ == "__main__":
    unittest.main()
