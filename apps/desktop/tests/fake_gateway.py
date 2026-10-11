"""A stand-in for the gateway's `/local/*` surface on an ephemeral loopback port.

It enforces the same proof the real gateway does (`X-Chintu-Local` header equal
to the configured secret, `Host` exactly `127.0.0.1:<port>`, no `Origin`) and
records every request so tests can assert on what the tray sent.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from chintu_desktop.layout import Layout

STATUS = {
    "service": "chintu", "provider": "claude-agent", "phone_connected": True, "active_tasks": 1, "total_tasks": 4,
    "clients": [{"client_id": "c1", "name": "Pixel 8", "paired_at": 1.0, "last_seen": 100.0, "connected": True}],
    "tailscale": {"status": "available", "reason": None, "gateway_url": "https://mac.tail.ts.net", "name": "mac"},
    "pair_url": "http://evil.example/pair",  # deliberately wrong: the tray must never use it
}

CLIENTS = [
    {"client_id": "c1", "name": "Pixel 8", "paired_at": 1.0, "last_seen": 100.0, "expires_at": 10 * 86400.0,
     "connected": True},
    {"client_id": "c2", "name": "Old phone", "paired_at": 2.0, "last_seen": 5.0, "expires_at": 2 * 86400.0,
     "connected": False},
]


class FakeGateway:
    """`with FakeGateway(secret="s") as gw:` serves until the block ends."""

    def __init__(self, secret="correct-secret", service="chintu", status=None, clients=None, pairing_status=200,
                 pairing_body=None):
        self.secret = secret
        self.service = service
        self.status_payload = dict(STATUS if status is None else status, service=service)
        self.clients = [dict(c) for c in (CLIENTS if clients is None else clients)]
        self.pairing_status = pairing_status
        self.pairing_body = pairing_body
        self.requests = []   # (method, path, headers dict)
        self.revoked = []
        self._lock = threading.Lock()
        self.server = None
        self.port = None

    def __enter__(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence
                pass

            def _record(self):
                with gateway._lock:
                    gateway.requests.append((self.command, self.path, {k: v for k, v in self.headers.items()}))

            def _send(self, code, body):
                data = json.dumps(body).encode() if not isinstance(body, bytes) else body
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _proven(self):
                if self.headers.get("Origin") is not None:
                    return False
                if self.headers.get("Host") != "127.0.0.1:%d" % gateway.port:
                    return False
                return self.headers.get("X-Chintu-Local") == gateway.secret

            def do_GET(self):
                self._record()
                if not self.path.startswith("/local/") or not self._proven():
                    return self._send(403, {"error": {"code": "forbidden", "message": "local proof required"}})
                if self.path == "/local/status":
                    return self._send(200, gateway.status_payload)
                if self.path == "/local/clients":
                    with gateway._lock:
                        return self._send(200, list(gateway.clients))
                return self._send(404, {"error": {"code": "not_found", "message": self.path}})

            def do_POST(self):
                self._record()
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                if not self._proven():
                    return self._send(403, {"error": {"code": "forbidden", "message": "local proof required"}})
                if self.path == "/local/pairing-code":
                    body = gateway.pairing_body
                    if body is None:
                        body = {"payload": '{"chintu":1,"secret":"x"}', "url": "https://mac.tail.ts.net",
                                "name": "Mac", "expires_in": 600}
                    return self._send(gateway.pairing_status, body)
                parts = self.path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["local", "clients"] and parts[3] == "revoke":
                    with gateway._lock:
                        match = [c for c in gateway.clients if c["client_id"] == parts[2]]
                        if not match:
                            return self._send(404, {"error": {"code": "not_found", "message": "no such client"}})
                        gateway.clients = [c for c in gateway.clients if c["client_id"] != parts[2]]
                        gateway.revoked.append(parts[2])
                    return self._send(200, {"revoked": parts[2]})
                return self._send(404, {"error": {"code": "not_found", "message": self.path}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                                        name="fake-gateway", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)
        return False

    def paths(self, method=None):
        return [path for m, path, _ in self.requests if method is None or m == method]


def repo_layout(repo):
    """The repository layout rooted at a temporary directory: data in `<repo>/var/`."""
    return Layout.detect(repo=repo, env={})


def write_secret(target, secret):
    """Create `local.secret` the way the gateway does, for a `Layout` or a repository root."""
    path = target.secret if isinstance(target, Layout) else Path(target) / "var" / "local.secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret + "\n")
    path.chmod(0o600)
    return path
