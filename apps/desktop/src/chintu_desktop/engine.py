"""The Ataria engine beside the gateway: prepared, started and stopped by the tray (docs/13-ataria.md).

The engine runs only on a computer that has an Ataria profile (`ataria.json` in the Chintu
folder); a computer installed with `--no-ataria-runtime` has none and never starts it.

Before the first start, `prepare` wires the two daemons together, never replacing what is there:

- `ataria.token` (mode 0600) in the Chintu folder: the token the gateway presents to the engine;
- the engine's copy of that token as a SHA-256 (`engine.token_sha256_file` in the profile, or
  `engine-token.sha256` in the engine's state folder), rewritten whenever it no longer matches;
- an `ataria` block in `gateway.local.json` pointing at the engine, when the config has none
  (the gateway reads it at its next start).
"""

import hashlib
import json
import os
import secrets
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

from .daemon import port_is_listening

DEFAULT_PORT = 8787
DEFAULT_BASE = "/ataria"


def profile_path(layout):
    return Path(os.environ.get("ATARIA_PROFILE") or Path(layout.home) / "ataria.json")


def read_profile(layout):
    """The profile as JSON, or None when this computer has none (or it cannot be read)."""
    try:
        return json.loads(profile_path(layout).read_text())
    except (OSError, ValueError):
        return None


def engine_settings(profile, layout):
    engine = (profile or {}).get("engine") or {}
    state = Path(engine.get("state_dir") or Path(layout.home) / "ataria").expanduser()
    return {"port": int(engine.get("port") or DEFAULT_PORT), "base": engine.get("base") or DEFAULT_BASE,
            "hash_file": Path(engine.get("token_sha256_file") or state / "engine-token.sha256").expanduser()}


def _private_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def prepare(layout):
    """Token, hash and gateway config for the engine. Returns what changed, or None without a profile."""
    profile = read_profile(layout)
    if profile is None:
        return None
    settings = engine_settings(profile, layout)
    changes = []
    config_path = Path(layout.config)
    config = None
    try:
        config = json.loads(config_path.read_text())
    except (OSError, ValueError):
        pass
    block = (config or {}).get("ataria") if isinstance(config, dict) else None
    token_file = Path(block["token_file"]) if isinstance(block, dict) and block.get("token_file") else Path("ataria.token")
    if not token_file.is_absolute():
        token_file = config_path.parent / token_file
    try:
        token = token_file.read_text().strip()
    except OSError:
        token = ""
    if not 32 <= len(token) <= 512:
        token = secrets.token_urlsafe(48)
        _private_write(token_file, token + "\n")
        changes.append("created %s" % token_file.name)
    digest = hashlib.sha256(token.encode()).hexdigest()
    try:
        current = settings["hash_file"].read_text().strip().lower()
    except OSError:
        current = ""
    if current != digest:
        _private_write(settings["hash_file"], digest + "\n")
        changes.append("wrote the engine's token hash")
    if isinstance(config, dict) and block is None:
        config["ataria"] = {"url": "http://127.0.0.1:%d%s/api/v1" % (settings["port"], settings["base"]),
                            "token_file": token_file.name if token_file.parent == config_path.parent else str(token_file)}
        _private_write(config_path, json.dumps(config, indent=2) + "\n")
        changes.append("connected the gateway to the engine (applies at its next start)")
    return changes


class AtariaSupervisor:
    """Starts and stops one `chintu-desktop ataria serve` child, the way GatewaySupervisor does the gateway."""

    def __init__(self, layout, listening=port_is_listening):
        self.layout = layout
        self.log_path = Path(layout.home) / "ataria-engine.log"
        self._listening = listening
        self._process = None
        self._log = None
        self._lock = threading.Lock()
        self.last_message = None

    def enabled(self):
        return read_profile(self.layout) is not None

    def port(self):
        return engine_settings(read_profile(self.layout), self.layout)["port"]

    def command(self):
        return [str(self.layout.desktop_executable), "ataria", "serve"]

    def child_running(self):
        with self._lock:
            if self._process is None:
                return False
            if self._process.poll() is None:
                return True
            self._close_log()
            return False

    def describe(self):
        if not self.enabled():
            return None
        if self.child_running():
            return "Ataria engine: running (pid %d)" % self._process.pid
        if self._listening(self.port()):
            return "Ataria engine: listening on :%d (started elsewhere)" % self.port()
        with self._lock:
            code = self._process.returncode if self._process is not None else None
        return "Ataria engine: exited with code %d (see its log)" % code if code is not None else "Ataria engine: not running"

    def start(self):
        if not self.enabled():
            return self._remember("Ataria is not set up on this computer (no ataria.json).")
        if self.child_running():
            return self._remember("The Ataria engine is already running.")
        if self._listening(self.port()):
            return self._remember("Port %d is already in use; not starting a second Ataria engine." % self.port())
        try:
            changes = prepare(self.layout) or []
        except OSError as error:
            return self._remember("Could not prepare the Ataria engine: %s" % error)
        command = self.command()
        with self._lock:
            self._close_log()
            self._log = open(self.log_path, "ab")
            self._log.write(("\n=== %s chintu-desktop start: %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"),
                             " ".join(shlex.quote(part) for part in command))).encode())
            self._log.flush()
            try:
                self._process = subprocess.Popen(command, cwd=str(self.layout.home), stdin=subprocess.DEVNULL,
                                                 stdout=self._log, stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as error:
                self._close_log()
                return self._remember("Could not start the Ataria engine: %s" % error)
        note = " (%s)" % "; ".join(changes) if changes else ""
        return self._remember("Started the Ataria engine (pid %d)%s." % (self._process.pid, note))

    def stop(self, grace_seconds=10.0):
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return self._remember("No Ataria engine child process to stop.")
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
        return self._remember("Stopped the Ataria engine (exit code %s)." % process.returncode)

    def _close_log(self):
        if self._log is not None:
            try:
                self._log.close()
            finally:
                self._log = None

    def _remember(self, message):
        self.last_message = message
        return message
