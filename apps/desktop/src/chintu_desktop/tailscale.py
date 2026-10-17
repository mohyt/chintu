"""Tailscale CLI discovery and `serve` status/enable for the gateway port.

Only three commands are ever run: `tailscale status --json` and `tailscale serve status` (read-only) and
`tailscale serve --bg <port>` when the user explicitly asks to enable it.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

MAC_APP_BINARY = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
COMMAND_TIMEOUT_SECONDS = 8
INSTALL_URL = "https://tailscale.com/download"
LINUX_INSTALL_COMMAND = "curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up"

_URL_LINE = re.compile(r"^(https?://\S+)")
_PROXY_LINE = re.compile(r"proxy\s+(?:https?://)?(?:127\.0\.0\.1|localhost|\[::1\]):(\d+)\b")


def find_binary(which=shutil.which, exists=os.path.exists, platform=None):
    """Path to the `tailscale` CLI, or `None`.

    On macOS the App Store / standalone app ships the CLI inside the bundle and does
    not put it on `PATH`; `tailscale` on `PATH` (Homebrew or tailscaled) wins when present.
    """
    found = which("tailscale")
    if found:
        return found
    if (platform or sys.platform) == "darwin" and exists(MAC_APP_BINARY):
        return MAC_APP_BINARY
    return None


@dataclass(frozen=True)
class Installation:
    """Whether Tailscale is installed and connected on this computer.

    The phone only reaches the gateway over the tailnet, so a missing or signed-out
    Tailscale is the first thing the tray and the installers point at.
    """
    installed: bool
    binary: str | None
    backend_state: str | None   # `BackendState` from `tailscale status --json`, e.g. Running, NeedsLogin, Stopped
    message: str                # one human line for the menu / installer
    hint: str | None            # what to do about it, if anything

    @property
    def connected(self):
        return self.installed and self.backend_state == "Running"


def install_hint(platform=None):
    if (platform or sys.platform) == "darwin":
        return "Install Tailscale from %s, open it and sign in with the same account as the phone." % INSTALL_URL
    return "Install it with: %s" % LINUX_INSTALL_COMMAND


def installation(binary=None, runner=subprocess.run, platform=None, find=None):
    """Read-only check: is the CLI there, and is the daemon connected? Runs `tailscale status --json` only."""
    platform = platform or sys.platform
    binary = binary or (find or find_binary)()
    if binary is None:
        return Installation(False, None, None, "Tailscale is not installed", install_hint(platform))
    try:
        result = _run(binary, ["status", "--json"], runner)
    except (OSError, subprocess.TimeoutExpired) as error:
        return Installation(True, binary, None, "Tailscale is installed but did not answer: %s" % error,
                            "Start the Tailscale app or `tailscaled`, then sign in.")
    state = None
    try:
        state = json.loads(result.stdout or "{}").get("BackendState")
    except ValueError:
        state = None
    if state == "Running":
        return Installation(True, binary, state, "Tailscale: connected", None)
    if state == "NeedsLogin":
        return Installation(True, binary, state, "Tailscale: installed but not signed in",
                            "Sign in to Tailscale with the same account as the phone (`tailscale up`).")
    if state in {"Stopped", "NoState"}:
        return Installation(True, binary, state, "Tailscale: installed but stopped",
                            "Turn Tailscale on (`tailscale up`) so the phone can reach this computer.")
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    first = detail[0] if detail else ("state %s" % state if state else "exit code %d" % result.returncode)
    return Installation(True, binary, state, "Tailscale: installed but not running (%s)" % first,
                        "Start the Tailscale app or `tailscaled`, then sign in.")


@dataclass(frozen=True)
class ServeStatus:
    available: bool          # a CLI was found and answered
    enabled: bool            # a serve handler proxies to our loopback port
    url: str | None          # the https tailnet URL that fronts the port
    message: str | None      # why it is unavailable, if it is


def parse_serve_status(text, port):
    """Return `(enabled, url)` from `tailscale serve status` text.

    The text groups handlers under the public URL they are served from:

        https://host.tailnet.ts.net (tailnet only)
        |-- / proxy http://127.0.0.1:8765

    Only a handler proxying to our own loopback port counts.
    """
    current_url = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        url_match = _URL_LINE.match(line)
        if url_match:
            current_url = url_match.group(1).rstrip(":")
            continue
        proxy = _PROXY_LINE.search(line)
        if proxy and int(proxy.group(1)) == port and current_url:
            return True, current_url
    return False, None


def _run(binary, args, runner=subprocess.run):
    return runner([binary] + args, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False)


def serve_status(port, binary=None, runner=subprocess.run):
    binary = binary or find_binary()
    if binary is None:
        return ServeStatus(False, False, None, "tailscale CLI not found")
    try:
        result = _run(binary, ["serve", "status"], runner)
    except (OSError, subprocess.TimeoutExpired) as error:
        return ServeStatus(False, False, None, "tailscale serve status failed: %s" % error)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        return ServeStatus(False, False, None, detail[0] if detail else "tailscale exited with %d" % result.returncode)
    enabled, url = parse_serve_status(result.stdout or "", port)
    return ServeStatus(True, enabled, url, None)


def operator_hint(user=None):
    """The one command that lets a non-root user run `tailscale serve` on Linux."""
    user = user or os.environ.get("USER") or "$USER"
    return ("Tailscale Serve needs permission on this computer. Run once in a terminal: "
            "sudo tailscale set --operator=%s, then choose Enable Tailscale Serve again." % user)


def _needs_operator(output):
    text = output.lower()
    return "access denied" in text or "--operator" in text or "permission denied" in text


def enable_serve(port, binary=None, runner=subprocess.run):
    """Run `tailscale serve --bg <port>`; returns `(ok, message)`."""
    binary = binary or find_binary()
    if binary is None:
        return False, "tailscale CLI not found"
    try:
        result = _run(binary, ["serve", "--bg", str(port)], runner)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, "tailscale serve failed: %s" % error
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        if _needs_operator(output):
            return False, operator_hint()
        first = output.splitlines()[0] if output else "exit code %d" % result.returncode
        return False, "tailscale serve failed: %s" % first
    return True, output or "Tailscale Serve enabled for port %d" % port
