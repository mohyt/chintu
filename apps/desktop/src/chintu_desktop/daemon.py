"""Gateway daemon supervision: port probing, argument selection and the child process.

The desktop app never runs a second gateway next to something that is already
listening; it only starts the gateway named by the `Layout` (`<repo>/.venv/bin/chintu-gateway`,
`<bundle> gateway` or `<venv>/bin/chintu-gateway`) when the loopback port is free.
Whether a listener really is the gateway is decided by a proven `/local/status`
call (status.py / local.py), not by the TCP probe here.

The port is chosen once per tray start (`resolve_port`, `relocate_if_taken`): an explicit
`--port` wins, then the port saved in `<home>/desktop.json`, then 8765. When another program
already holds the port, the tray moves to the next free one and saves it, so the login item,
the app menu launcher and `chintu-desktop status` all agree without extra arguments.
"""

import json
import os
import shlex
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

from .status import DEFAULT_PORT


def select_gateway_args(layout, explicit=None, port=DEFAULT_PORT, config_exists=None):
    """Choose the gateway CLI arguments (without the executable).

    With the operator config present the real provider is used; otherwise the
    scripted no-I/O fixture runs against the layout's database and contract.
    `explicit` is the raw `--gateway-args` string; it is split like a shell would and
    used as-is apart from adding `--port` after `serve` when the caller omitted it.
    """
    if explicit:
        args = shlex.split(explicit)
    else:
        exists = layout.config.is_file() if config_exists is None else config_exists
        if exists:
            args = ["--config", str(layout.config), "serve", "--provider", "claude-agent"]
        else:
            args = ["--database", str(layout.database), "serve", "--scripted"]
            if layout.contract is not None:
                args += ["--contract", str(layout.contract)]
    if "serve" in args and "--port" not in args:
        args = args + ["--port", str(port)]
    return args


FALLBACK_PORTS = range(DEFAULT_PORT + 1, DEFAULT_PORT + 35)  # 8766-8799


def saved_port(layout):
    """The port saved in the layout's desktop.json, or None when absent or unreadable."""
    try:
        port = json.loads(layout.settings.read_text()).get("port")
    except (OSError, ValueError, AttributeError):
        return None
    return port if isinstance(port, int) and 0 < port < 65536 else None


def save_port(layout, port):
    """Persist the chosen port; a repository checkout keeps no tray settings."""
    if layout.kind == "repo":
        return
    layout.ensure_home()
    try:
        data = json.loads(layout.settings.read_text())
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data["port"] = port
    temporary = layout.settings.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(data, indent=2) + "\n")
    os.replace(temporary, layout.settings)


def resolve_port(layout, explicit=None):
    """An explicit --port, else the saved port, else the default."""
    return explicit or saved_port(layout) or DEFAULT_PORT


def relocate_if_taken(layout, port, is_ours, listening=None):
    """Return `(port, notice)`: a free port when another program holds `port`.

    `is_ours(port)` must prove the listener is this layout's gateway (a `/local/status` call
    with its secret). Nothing changes when the port is free or already ours; the new port is
    saved so later starts use it directly.
    """
    listening = listening or port_is_listening
    if not listening(port) or is_ours(port):
        return port, None
    for candidate in FALLBACK_PORTS:
        if candidate != port and not listening(candidate):
            save_port(layout, candidate)
            return candidate, ("Port %d is used by another program, so Chintu now uses port %d "
                               "(saved in %s)." % (port, candidate, layout.settings))
    return port, ("Port %d is used by another program and ports %d-%d are busy too; quit the other "
                  "program or start Chintu with --port." % (port, FALLBACK_PORTS[0], FALLBACK_PORTS[-1]))


def port_is_listening(port, host="127.0.0.1", timeout=0.5):
    """True when something accepts TCP connections on the loopback port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class GatewaySupervisor:
    """Starts/stops one gateway child process and reports who owns the port."""

    def __init__(self, layout, port=DEFAULT_PORT, gateway_args=None, log_path=None, listening=port_is_listening):
        self.layout = layout
        self.port = port
        self.gateway_args = gateway_args
        self.log_path = Path(log_path) if log_path else layout.gateway_log
        self._listening = listening
        self._process = None
        self._log = None
        self._lock = threading.Lock()
        self.last_message = None

    @property
    def executable(self):
        return self.layout.gateway_executable

    def command(self):
        return list(self.layout.gateway_command) + select_gateway_args(self.layout, self.gateway_args, self.port)

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

    def describe(self, verified=None):
        """One-line gateway status for the menu.

        `verified` says whether a proven `/local/status` call identified the listener
        as the gateway (True), failed to (False), or has not been tried (None). A
        port that merely accepts connections is never called the gateway.
        """
        if self.child_running():
            return "Gateway: running (pid %d, started here)" % self._process.pid
        if self.listening():
            if verified:
                return "Gateway: listening on :%d (started elsewhere)" % self.port
            if verified is None:
                return "Gateway: something listens on :%d (not verified yet)" % self.port
            return "Port %d is in use by something that isn't Chintu" % self.port
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
            return self._remember("Port %d is already in use; not starting a second process on it." % self.port)
        if self.child_running():
            return self._remember("The gateway child process is already running.")
        if not self.executable.is_file():
            return self._remember("Gateway executable not found: %s" % self.executable)
        try:
            self.layout.ensure_home()
            if self.layout.kind != "repo":
                # A fresh install gets the real provider straight away instead of the scripted fixture.
                self.layout.ensure_config()
        except OSError as error:
            return self._remember("Could not prepare the Chintu folder %s: %s" % (self.layout.home, error))
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
                    command, cwd=str(self.layout.home), stdin=subprocess.DEVNULL, stdout=self._log,
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
