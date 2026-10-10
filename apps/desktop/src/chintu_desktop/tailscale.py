"""Tailscale CLI discovery and `serve` status/enable for the gateway port.

Only two commands are ever run: `tailscale serve status` (read-only) and
`tailscale serve --bg <port>` when the user explicitly asks to enable it.
"""

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

MAC_APP_BINARY = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
COMMAND_TIMEOUT_SECONDS = 8

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
        first = output.splitlines()[0] if output else "exit code %d" % result.returncode
        return False, "tailscale serve failed: %s" % first
    return True, output or "Tailscale Serve enabled for port %d" % port
