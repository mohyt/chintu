"""Gateway daemon supervision: port probing, argument selection and the child process.

The desktop app never runs a second gateway next to one that is already listening;
it only starts `<repo>/.venv/bin/chintu-gateway` when the loopback port is free.
"""

import os
import shlex
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

from .status import DEFAULT_PORT

GATEWAY_RELATIVE = Path(".venv") / "bin" / "chintu-gateway"
LOCAL_CONFIG = Path("var") / "gateway.local.json"
LOG_RELATIVE = Path("var") / "desktop-gateway.log"

# Preferred: the reviewed operator config with the Claude Agent SDK provider.
PROVIDER_ARGS = ["--config", str(LOCAL_CONFIG), "serve", "--provider", "claude-agent"]
# Fallback when no local config exists: the scripted no-I/O fixture against the shared contract.
SCRIPTED_ARGS = ["--database", "var/chintu.sqlite3", "serve", "--scripted", "--contract", "contracts/openapi.json"]


def default_repo():
    """Repository root: `CHINTU_REPO` wins, else resolve from this package location.

    apps/desktop/src/chintu_desktop/daemon.py -> parents[4] is the repository root.
    """
    override = os.environ.get("CHINTU_REPO")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[4]


def select_gateway_args(repo, explicit=None, port=DEFAULT_PORT, config_exists=None):
    """Choose the gateway CLI arguments (without the executable).

    `explicit` is the raw `--gateway-args` string; it is split like a shell would and
    used as-is apart from adding `--port` after `serve` when the caller omitted it.
    """
    repo = Path(repo)
    if explicit:
        args = shlex.split(explicit)
    else:
        exists = (repo / LOCAL_CONFIG).is_file() if config_exists is None else config_exists
        args = list(PROVIDER_ARGS if exists else SCRIPTED_ARGS)
    if "serve" in args and "--port" not in args:
        args = args + ["--port", str(port)]
    return args


def port_is_listening(port, host="127.0.0.1", timeout=0.5):
    """True when something accepts TCP connections on the loopback port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class GatewaySupervisor:
    """Starts/stops one gateway child process and reports who owns the port."""

    def __init__(self, repo, port=DEFAULT_PORT, gateway_args=None, log_path=None, listening=port_is_listening):
        self.repo = Path(repo)
        self.port = port
        self.gateway_args = gateway_args
        self.log_path = Path(log_path) if log_path else self.repo / LOG_RELATIVE
        self._listening = listening
        self._process = None
        self._log = None
        self._lock = threading.Lock()
        self.last_message = None

    @property
    def executable(self):
        return self.repo / GATEWAY_RELATIVE

    def command(self):
        return [str(self.executable)] + select_gateway_args(self.repo, self.gateway_args, self.port)

    def child_running(self):
        with self._lock:
            return self._reap_locked()

    def _reap_locked(self):
        """True while the child lives; releases the log handle once it has exited."""
        if self._process is None:
            return False
        if self._process.poll() is None:
            return True
        self._close_log()
        return False

    def listening(self):
        return self._listening(self.port)

    def describe(self):
        """One-line gateway status for the menu."""
        if self.child_running():
            return "Gateway: running (pid %d, started here)" % self._process.pid
        if self.listening():
            return "Gateway: listening on :%d (started elsewhere)" % self.port
        exit_code = self._exit_code()
        if exit_code is not None:
            return "Gateway: exited with code %d (see log)" % exit_code
        return "Gateway: not running"

    def _exit_code(self):
        with self._lock:
            if self._process is None or self._reap_locked():
                return None
            return self._process.returncode

    def start(self):
        """Launch the daemon unless one already answers. Returns a human-readable message."""
        if self.listening():
            return self._remember("A gateway is already listening on port %d; attached to it." % self.port)
        if self.child_running():
            return self._remember("The gateway child process is already running.")
        if not self.executable.is_file():
            return self._remember("Gateway executable not found: %s" % self.executable)
        command = self.command()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._close_log()
            self._log = open(self.log_path, "ab")
            self._log.write(("\n=== %s chintu-desktop start: %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"),
                             " ".join(shlex.quote(part) for part in command))).encode())
            self._log.flush()
            try:
                self._process = subprocess.Popen(
                    command, cwd=str(self.repo), stdin=subprocess.DEVNULL, stdout=self._log,
                    stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as error:
                self._close_log()
                return self._remember("Could not start the gateway: %s" % error)
        return self._remember("Started the gateway (pid %d); log: %s" % (self._process.pid, self.log_path))

    def stop(self, grace_seconds=10.0):
        """Terminate the child we started; a gateway started elsewhere is left alone."""
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            if self.listening():
                return self._remember("The listening gateway was not started here; stop it where it was started.")
            return self._remember("No gateway child process to stop.")
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        with self._lock:
            self._close_log()
        return self._remember("Stopped the gateway (exit code %s)." % process.returncode)

    def _close_log(self):
        if self._log is not None:
            try:
                self._log.close()
            finally:
                self._log = None

    def _remember(self, message):
        self.last_message = message
        return message
