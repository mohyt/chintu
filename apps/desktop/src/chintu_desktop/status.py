"""Gateway status polling and the pure state model behind the tray icon.

`derive_state` is a pure function from one `/local/status` payload (or a fetch
error) to a `DesktopState`; the tray only renders that value. `fetch_status`
is the only network call and uses the standard library so the companion stays
dependency-light.
"""

import enum
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_PORT = 8765
POLL_INTERVAL_SECONDS = 5.0


class Presence(enum.Enum):
    """The three icon states."""

    CONNECTED = "connected"  # daemon up and the paired phone was seen recently
    AWAY = "away"            # daemon up, phone paired but not currently reachable (or none paired)
    DOWN = "down"            # no gateway answering on the loopback port


@dataclass(frozen=True)
class DesktopState:
    presence: Presence
    daemon_running: bool
    phone_connected: bool
    phone_name: str | None
    paired_count: int
    active_tasks: int
    total_tasks: int
    gateway_url: str | None
    tailscale_status: str | None
    tailscale_reason: str | None
    provider: str | None
    pair_url: str
    error: str | None
    checked_at: float


def local_url(port, path):
    return "http://127.0.0.1:%d%s" % (port, path)


def fetch_status(port=DEFAULT_PORT, timeout=2.0, opener=None):
    """Return `(payload, error)`; exactly one of them is `None`.

    Connection refused means the daemon is down. A 403 would mean the request was
    proxied, which cannot happen on loopback, so it is reported as a plain error.
    """
    request = urllib.request.Request(local_url(port, "/local/status"), headers={"Accept": "application/json"})
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        return None, "gateway answered HTTP %d" % error.code
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        reason = getattr(error, "reason", error)
        return None, "gateway unreachable: %s" % reason
    try:
        payload = json.loads(body)
    except ValueError:
        return None, "gateway returned invalid JSON"
    if not isinstance(payload, dict) or payload.get("service") != "chintu":
        return None, "port %d is not a Chintu gateway" % port
    return payload, None


def _phone_name(clients):
    """Prefer a connected client, then the most recently seen one."""
    if not clients:
        return None
    connected = [c for c in clients if c.get("connected")]
    if connected:
        return connected[0].get("name")
    seen = sorted(clients, key=lambda c: c.get("last_seen") or 0, reverse=True)
    return seen[0].get("name")


def derive_state(payload, error=None, port=DEFAULT_PORT, now=None):
    """Map one poll result to the state the tray renders."""
    checked_at = time.time() if now is None else now
    pair_url = local_url(port, "/pair")
    if payload is None:
        return DesktopState(
            presence=Presence.DOWN, daemon_running=False, phone_connected=False, phone_name=None,
            paired_count=0, active_tasks=0, total_tasks=0, gateway_url=None, tailscale_status=None,
            tailscale_reason=None, provider=None, pair_url=pair_url, error=error or "gateway unreachable",
            checked_at=checked_at)
    clients = [c for c in payload.get("clients") or [] if isinstance(c, dict)]
    tailscale = payload.get("tailscale") or {}
    phone_connected = bool(payload.get("phone_connected"))
    return DesktopState(
        presence=Presence.CONNECTED if phone_connected else Presence.AWAY,
        daemon_running=True,
        phone_connected=phone_connected,
        phone_name=_phone_name(clients),
        paired_count=len(clients),
        active_tasks=int(payload.get("active_tasks") or 0),
        total_tasks=int(payload.get("total_tasks") or 0),
        gateway_url=tailscale.get("gateway_url"),
        tailscale_status=tailscale.get("status"),
        tailscale_reason=tailscale.get("reason"),
        provider=payload.get("provider"),
        pair_url=payload.get("pair_url") or pair_url,
        error=None,
        checked_at=checked_at)


def phone_line(state):
    """Menu text summarising phone presence."""
    if not state.daemon_running:
        return "Phone: unknown (gateway not running)"
    if state.paired_count == 0:
        return "Phone: not paired yet"
    name = state.phone_name or "paired phone"
    if state.phone_connected:
        return "Phone: %s connected" % name
    return "Phone: %s away" % name


def tasks_line(state):
    if not state.daemon_running:
        return "Tasks: unavailable"
    return "Active tasks: %d (total %d)" % (state.active_tasks, state.total_tasks)


def gateway_url_line(state):
    if state.gateway_url:
        return "Tailnet URL: %s" % state.gateway_url
    if state.tailscale_reason:
        return "Tailnet URL: unavailable (%s)" % state.tailscale_reason
    return "Tailnet URL: unavailable"


def tooltip(state):
    """Short hover text for the icon."""
    if state.presence is Presence.DOWN:
        return "Chintu: gateway not running"
    if state.presence is Presence.CONNECTED:
        return "Chintu: %s connected" % (state.phone_name or "phone")
    return "Chintu: phone away"


class Poller(threading.Thread):
    """Polls `/local/status` every `interval` seconds and hands each state to `on_state`."""

    def __init__(self, port=DEFAULT_PORT, interval=POLL_INTERVAL_SECONDS, on_state=None, fetch=fetch_status):
        super().__init__(name="chintu-desktop-poller", daemon=True)
        self.port = port
        self.interval = interval
        self.on_state = on_state or (lambda state: None)
        self.fetch = fetch
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self.state = derive_state(None, "not polled yet", port=port)

    def poll_once(self):
        payload, error = self.fetch(self.port)
        self.state = derive_state(payload, error, port=self.port)
        self.on_state(self.state)
        return self.state

    def poll_soon(self):
        """Ask the loop to poll immediately (after starting/stopping the daemon)."""
        self._wake.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as error:  # noqa: BLE001 - the tray must keep running
                self.state = derive_state(None, "poll failed: %s" % error, port=self.port)
                self.on_state(self.state)
            self._wake.wait(self.interval)
            self._wake.clear()

    def stop(self):
        self._stop_event.set()
        self._wake.set()
