"""The Ataria dashboard: where it is, and the pairing that reaches another computer's (docs/13-ataria.md).

The dashboard is served by the gateway on the computer that runs the Ataria runtime. When that is
this computer, the tray asks its own gateway for a one-time link (`/local/ataria/link`). Otherwise
this computer is paired with that one, the way a phone is, and asks it over Tailscale HTTPS:

  chintu-desktop pairing-code                     on the computer that runs Ataria: prints a code
  chintu-desktop ataria connect CODE|-            here: pairs with it, keeps the token in
                                                  ataria-remote.json (mode 0600)
  chintu-desktop ataria dashboard                 opens the dashboard (local first, else remote)
  chintu-desktop ataria disconnect                revokes this computer there and forgets it

The code is the same JSON the phone scans as a QR code; it works once and expires in 10 minutes.
"""

import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

FILE_NAME = "ataria-remote.json"
TIMEOUT = 15


class RemoteError(Exception):
    pass


def remote_file(layout):
    return Path(layout.home) / FILE_NAME


def check_gateway_url(url):
    parts = urlsplit(url if isinstance(url, str) else "")
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query \
            or parts.fragment or parts.path not in ("", "/"):
        raise RemoteError("The pairing code must name an https gateway address, such as https://box.tailnet.ts.net.")
    return "https://" + parts.netloc


def parse_code(text):
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise RemoteError("That is not a pairing code. Run `chintu-desktop pairing-code` on the other computer.") from None
    if not isinstance(value, dict) or value.get("chintu") != 1 or not isinstance(value.get("secret"), str):
        raise RemoteError("That is not a Chintu pairing code.")
    return check_gateway_url(value.get("url")), value["secret"], str(value.get("name") or "")[:80]


def _request(method, url, headers=None, body=None, opener=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "X-Chintu-Protocol": "1", "Accept": "application/json",
        **({"Content-Type": "application/json"} if data is not None else {}), **(headers or {})})
    open_url = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open
    try:
        with open_url(request, timeout=TIMEOUT) as response:
            raw = response.read(1024 * 1024)
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read(65536))["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = "HTTP %d" % error.code
        raise RemoteError("%s answered: %s" % (urlsplit(url).hostname, message)) from None
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
        raise RemoteError("Could not reach %s: %s" % (urlsplit(url).hostname, getattr(error, "reason", error))) from None


def save(layout, value):
    path = remote_file(layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, indent=2)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def load(layout):
    """The saved pairing, or None. A file other users can read is refused."""
    path = remote_file(layout)
    try:
        if os.stat(path).st_mode & 0o077:
            raise RemoteError("%s is readable by other users; run `chmod 600` on it." % path)
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise RemoteError("%s is unreadable; run `chintu-desktop ataria disconnect` and connect again." % path) from None
    if not isinstance(value, dict) or not isinstance(value.get("token"), str):
        raise RemoteError("%s is incomplete; connect again." % path)
    value["gateway_url"] = check_gateway_url(value.get("gateway_url"))
    return value


def connect(layout, code_text, opener=None, hostname=None):
    url, secret, name = parse_code(code_text.strip())
    client_name = "%s (desktop)" % (hostname or socket.gethostname().split(".")[0] or "Computer")
    paired = _request("POST", url + "/api/v1/pair", body={"enrollment_secret": secret, "client_name": client_name[:80]},
                      opener=opener)
    if not isinstance(paired.get("token"), str) or not isinstance(paired.get("client_id"), str):
        raise RemoteError("%s did not return a usable pairing." % urlsplit(url).hostname)
    save(layout, {"gateway_url": url, "name": name, "client_id": paired["client_id"], "token": paired["token"]})
    return url, name


def remote_link(layout, opener=None):
    value = load(layout)
    if value is None:
        return None
    link = _request("POST", value["gateway_url"] + "/api/v1/ataria/link",
                    headers={"Authorization": "Bearer " + value["token"]}, opener=opener)
    path = link.get("path") if isinstance(link, dict) else None
    if not isinstance(path, str) or not path.startswith("/ataria/open?code="):
        raise RemoteError("%s did not return a dashboard link." % urlsplit(value["gateway_url"]).hostname)
    return value["gateway_url"] + path


def disconnect(layout, opener=None):
    """Revoke this computer on the other gateway (best effort), then forget it. False when not connected."""
    path = remote_file(layout)
    if not path.exists():
        return False
    try:
        value = load(layout)
        _request("DELETE", value["gateway_url"] + "/api/v1/clients/me",
                 headers={"Authorization": "Bearer " + value["token"]}, opener=opener)
    except RemoteError:
        pass
    path.unlink()
    return True


def dashboard_url(layout, local_client, local_ataria, opener=None):
    """The one-time dashboard URL: this computer's runtime first, else the paired one; None when neither."""
    if local_ataria:
        value = local_client.post_json("/local/ataria/link")
        path = value.get("path") if isinstance(value, dict) else None
        if isinstance(path, str) and path.startswith("/ataria/open?code="):
            return "http://127.0.0.1:%d%s" % (local_client.port, path)
        error = value.get("error") if isinstance(value, dict) else None
        raise RemoteError(error.get("message") if isinstance(error, dict) and error.get("message")
                          else "The local gateway did not return a dashboard link.")
    return remote_link(layout, opener)


def connected_name(layout):
    """A short label for the paired computer, or None (never raises; used in the tray menu)."""
    try:
        value = load(layout)
    except RemoteError:
        return None
    return (value.get("name") or urlsplit(value["gateway_url"]).hostname.split(".")[0]) if value else None
