"""Gateway status polling and the pure state model behind the tray icon.

`derive_state` is a pure function from one `/local/status` payload (or a fetch
error) to a `DesktopState`; the tray only renders that value. `fetch_status` and
`fetch_clients` are the only network calls, made through `local.LocalClient`,
which carries the per-start secret from `var/local.secret` on every request.
"""

import enum
import threading
import time
from dataclasses import dataclass

from . import local
from .local import DEFAULT_PORT, LocalError  # noqa: F401 - DEFAULT_PORT is re-exported for the other modules

POLL_INTERVAL_SECONDS = 5.0


class Presence(enum.Enum):
    """The three icon states."""

    CONNECTED = "connected"  # daemon up and the paired phone was seen recently
    AWAY = "away"            # daemon up, phone paired but not currently reachable (or none paired)
    DOWN = "down"            # no verified gateway answering on the loopback port


@dataclass(frozen=True)
class DesktopState:
    presence: Presence
    daemon_running: bool          # True only after a proven `/local/status` answered `service == "chintu"`
    phone_connected: bool
    phone_name: str | None
    paired_count: int
    clients: tuple               # of `local.Client`
    active_tasks: int
    total_tasks: int
    gateway_url: str | None
    tailscale_status: str | None
    tailscale_reason: str | None
    provider: str | None
    fault_kind: str | None        # `local.LocalError.kind` when the poll failed, else None
    error: str | None
    checked_at: float


def local_url(port, path):
    return "http://127.0.0.1:%d%s" % (port, path)


def fetch_status(port=DEFAULT_PORT, repo=None, client=None):
    """Return `(payload, error)`; exactly one of them is `None`.

    `error` is a `local.LocalError`: `SecretMissing` when `var/local.secret` is
    absent, `Unreachable` when nothing answers, `Refused` on a 403, `NotChintu`
    when the listener does not answer like a gateway.
    """
    client = client or local.LocalClient(repo, port)
    try:
        return client.status(), None
    except LocalError as error:
        return None, error


def fetch_clients(port=DEFAULT_PORT, repo=None, client=None):
    """Return `(clients, error)` from `GET /local/clients`."""
    client = client or local.LocalClient(repo, port)
    try:
        return client.clients(), None
    except LocalError as error:
        return None, error


def _phone_name(clients):
    """Prefer a connected client, then the most recently seen one."""
    if not clients:
        return None
    connected = [c for c in clients if c.connected]
    if connected:
        return connected[0].name
    seen = sorted(clients, key=lambda c: c.last_seen or 0, reverse=True)
    return seen[0].name


def derive_state(payload, error=None, port=DEFAULT_PORT, now=None, clients=None):
    """Map one poll result to the state the tray renders.

    `clients` from `/local/clients` (which carries `expires_at`) wins over the
    `clients` list embedded in the status payload.
    """
    checked_at = time.time() if now is None else now
    if payload is None:
        kind = getattr(error, "kind", None) or ("error" if error else None)
        return DesktopState(
            presence=Presence.DOWN, daemon_running=False, phone_connected=False, phone_name=None,
            paired_count=0, clients=(), active_tasks=0, total_tasks=0, gateway_url=None, tailscale_status=None,
            tailscale_reason=None, provider=None, fault_kind=kind or "unreachable",
            error=str(error) if error else "gateway unreachable", checked_at=checked_at)
    if clients is None:
        try:
            clients = local.parse_clients(payload.get("clients") or [])
        except LocalError:
            clients = []
    tailscale = payload.get("tailscale") or {}
    phone_connected = bool(payload.get("phone_connected"))
    return DesktopState(
        presence=Presence.CONNECTED if phone_connected else Presence.AWAY,
        daemon_running=True,
        phone_connected=phone_connected,
        phone_name=_phone_name(clients),
        paired_count=len(clients),
        clients=tuple(clients),
        active_tasks=int(payload.get("active_tasks") or 0),
        total_tasks=int(payload.get("total_tasks") or 0),
        gateway_url=tailscale.get("gateway_url"),
        tailscale_status=tailscale.get("status"),
        tailscale_reason=tailscale.get("reason"),
        provider=payload.get("provider"),
        fault_kind=None,
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
    """Polls `/local/status` (and `/local/clients`) every `interval` seconds and hands each state to `on_state`."""

    def __init__(self, port=DEFAULT_PORT, repo=None, interval=POLL_INTERVAL_SECONDS, on_state=None,
                 fetch=fetch_status, fetch_clients=fetch_clients):
        super().__init__(name="chintu-desktop-poller", daemon=True)
        self.port = port
        self.repo = repo
        self.interval = interval
        self.on_state = on_state or (lambda state: None)
        self.fetch = fetch
        self.fetch_clients = fetch_clients
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self.state = derive_state(None, "not polled yet", port=port)

    def poll_once(self):
        payload, error = self.fetch(self.port, self.repo)
        clients = None
        if payload is not None:
            clients, _ = self.fetch_clients(self.port, self.repo)  # the status payload's list is the fallback
        self.state = derive_state(payload, error, port=self.port, clients=clients)
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
