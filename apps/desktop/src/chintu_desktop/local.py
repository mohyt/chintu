"""The gateway's local operator API and the proof it demands.

The gateway writes a per-start secret to `local.secret` beside its database (mode
0600); the `Layout` says where that is (`<repo>/var/` or the per-user Chintu folder).
Every `/local/*` request must carry it as `X-Chintu-Local`, send a `Host` header
of exactly `127.0.0.1:<port>` and no `Origin`; anything else is a 403. The secret
changes whenever the daemon restarts, so it is read from disk for every request
and never cached.

Requests go straight through `http.client` to the loopback address: no proxy
environment, no redirects, exact header casing. `transport` is injectable so the
tests run against a fake server or no server at all.

The browser pairing page takes the secret as `?key=` instead of a header. That
URL is always built here from the port and the file, never taken from a server
response: a process squatting on the port could otherwise send the browser
anywhere it liked.
"""

import http.client
import json
import urllib.parse
from dataclasses import dataclass

DEFAULT_PORT = 8765
HEADER = "X-Chintu-Local"
DEFAULT_TIMEOUT = 2.0


class LocalError(Exception):
    """Base class; `kind` is a short machine-readable reason for the state model."""

    kind = "error"


class SecretMissing(LocalError):
    """`local.secret` is absent or empty: the daemon is not running, or not from this Chintu folder."""

    kind = "no_secret"


class Unreachable(LocalError):
    """Connection refused, reset or timed out."""

    kind = "unreachable"


class Refused(LocalError):
    """HTTP 403: the listener did not accept our proof, so it is not this Chintu folder's gateway."""

    kind = "refused"


class HttpError(LocalError):
    """Any other non-2xx answer; the body is kept for callers that know the endpoint."""

    kind = "http"

    def __init__(self, code, body=b""):
        super().__init__("gateway answered HTTP %d" % code)
        self.code = code
        self.body = body


class NotChintu(LocalError):
    """The listener answered, but not like a Chintu gateway."""

    kind = "not_chintu"


def secret_path(layout):
    return layout.secret


def read_secret(layout):
    """The current secret, read fresh from disk. Raises `SecretMissing`."""
    path = secret_path(layout)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        value = ""
    if not value or any(ch.isspace() for ch in value):
        raise SecretMissing("no local secret at %s: the Chintu daemon is not running, "
                            "or was not started from this Chintu folder (%s)" % (path, path.parent))
    return value


def proof_headers(port, secret):
    """Exactly the headers the gateway checks; never an `Origin`."""
    return {"Host": "127.0.0.1:%d" % port, HEADER: secret, "Accept": "application/json", "Connection": "close"}


def pair_url(port, secret):
    """The browser pairing page, built locally from the port and the secret."""
    return "http://127.0.0.1:%d/pair?key=%s" % (port, urllib.parse.quote(secret, safe=""))


def http_transport(port, method, path, headers, body, timeout):
    """`(status, body)` from one direct loopback request. Raises `OSError` family on failure."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class LocalClient:
    """Talks to `/local/*` on behalf of the tray; one instance per app, stateless apart from its settings."""

    def __init__(self, layout, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT, transport=http_transport):
        self.layout = layout
        self.port = port
        self.timeout = timeout
        self.transport = transport

    def secret(self):
        return read_secret(self.layout)

    def pair_url(self):
        return pair_url(self.port, self.secret())

    def request(self, method, path, timeout=None):
        """`(status, body)` for one proven request; 403 raises `Refused`, no server raises `Unreachable`."""
        secret = self.secret()
        body = b"" if method in ("POST", "PUT", "PATCH") else None
        try:
            code, payload = self.transport(self.port, method, path, proof_headers(self.port, secret), body,
                                           self.timeout if timeout is None else timeout)
        except (OSError, http.client.HTTPException, TimeoutError) as error:
            raise Unreachable("gateway unreachable: %s" % (getattr(error, "strerror", None) or error)) from None
        if code == 403:
            raise Refused("port %d refused the local proof (HTTP 403): it is not the gateway of %s"
                          % (self.port, self.layout.home))
        if not 200 <= code < 300:
            raise HttpError(code, payload)
        return code, payload

    def get_json(self, path, timeout=None):
        _, payload = self.request("GET", path, timeout)
        try:
            return json.loads(payload)
        except ValueError:
            raise NotChintu("port %d returned invalid JSON for %s" % (self.port, path)) from None

    def post_json(self, path, timeout=None):
        _, payload = self.request("POST", path, timeout)
        if not payload.strip():
            return None
        try:
            return json.loads(payload)
        except ValueError:
            raise NotChintu("port %d returned invalid JSON for %s" % (self.port, path)) from None

    # --- endpoints ---------------------------------------------------------------

    def status(self):
        """`GET /local/status`; the listener counts as the gateway only with `service == "chintu"`."""
        payload = self.get_json("/local/status")
        if not isinstance(payload, dict) or payload.get("service") != "chintu":
            raise NotChintu("port %d is not a Chintu gateway" % self.port)
        return payload

    def clients(self):
        """`GET /local/clients` as a list of `Client`."""
        return parse_clients(self.get_json("/local/clients"))

    def revoke(self, client_id):
        """`POST /local/clients/{client_id}/revoke`."""
        if not client_id or "/" in client_id:
            raise ValueError("invalid client id")
        self.post_json("/local/clients/%s/revoke" % urllib.parse.quote(client_id, safe=""))


@dataclass(frozen=True)
class Client:
    client_id: str
    name: str
    paired_at: float | None
    last_seen: float | None
    expires_at: float | None
    connected: bool


def _timestamp(value):
    """Epoch seconds from a number or an ISO-8601 string; `None` when absent or unparseable."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime, timezone
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def parse_clients(payload):
    """Accept either a bare list or `{"clients": [...]}`; skip malformed entries."""
    if isinstance(payload, dict):
        payload = payload.get("clients")
    if not isinstance(payload, list):
        raise NotChintu("unexpected clients listing")
    clients = []
    for item in payload:
        if not isinstance(item, dict) or not item.get("client_id"):
            continue
        clients.append(Client(client_id=str(item["client_id"]), name=str(item.get("name") or "Unnamed phone"),
                              paired_at=_timestamp(item.get("paired_at")), last_seen=_timestamp(item.get("last_seen")),
                              expires_at=_timestamp(item.get("expires_at")), connected=bool(item.get("connected"))))
    return clients


def expiry_text(expires_at, now):
    """"expires in N days" style text for the menu; empty when the gateway gave no expiry."""
    if expires_at is None:
        return ""
    remaining = expires_at - now
    if remaining <= 0:
        return "expired"
    days = int(remaining // 86400)
    if days == 0:
        hours = max(1, int(remaining // 3600))
        return "expires in %d hour%s" % (hours, "" if hours == 1 else "s")
    if remaining % 86400:
        days += 1
    return "expires in %d day%s" % (days, "" if days == 1 else "s")
