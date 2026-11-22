"""The Ataria dashboard in a browser, served through the gateway (docs/13-ataria.md, "The dashboard").

The dashboard is the engine's own web page. A browser cannot send the phone's bearer token, so a
paired device (or the tray, with the local proof) first asks for a one-time link:

  POST /api/v1/ataria/link    -> {"path": "/ataria/open?code=...", "expires_in": 60}
  GET  /ataria/open?code=...  -> sets an HttpOnly, SameSite=Strict session cookie, redirects to /ataria/
  ANY  /ataria/<path>         -> with that cookie, passed to the engine's web root on loopback

The code works once, for 60 seconds. A session lasts 12 hours, lives only in this process (a
restart signs everyone out), and ends at once when the device that opened it is revoked. Writes
must carry `X-Ataria: 1`, which a cross-site form cannot send.
"""

import asyncio
import hashlib
import re
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote, urlencode

from .ataria import NoRedirect, read_token

CODE_SECONDS = 60
SESSION_SECONDS = 12 * 3600
MAX_SESSIONS = 64
MAX_PAGE = 16 * 1024 * 1024
COOKIE = "chintu_ataria"
LOCAL = "local"
SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=+-]{0,200}$")
TYPES = ("text/html", "text/css", "text/javascript", "application/javascript", "application/json",
         "application/manifest+json", "text/markdown", "text/plain", "image/svg+xml", "image/png",
         "image/jpeg", "image/webp")
HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # The page, its script and its data come from here; only Google Fonts is allowed from outside.
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; connect-src 'self'; "
                               "img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                               "font-src 'self' https://fonts.gstatic.com; frame-ancestors 'none'; "
                               "base-uri 'self'; form-action 'self'",
}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class Grant:
    owner: str        # client_id, or LOCAL for the tray on this machine
    expires: float


class Sessions:
    """One-time codes and browser sessions, kept only as SHA-256 hashes in memory."""

    def __init__(self, clock=time.time):
        self.clock, self.codes, self.sessions = clock, {}, {}

    def _sweep(self):
        now = self.clock()
        for table in (self.codes, self.sessions):
            for key in [key for key, grant in table.items() if grant.expires <= now]:
                del table[key]

    def link(self, owner):
        self._sweep()
        code = secrets.token_urlsafe(32)
        self.codes[digest(code)] = Grant(owner, self.clock() + CODE_SECONDS)
        return {"path": "/ataria/open?" + urlencode({"code": code}), "expires_in": CODE_SECONDS}

    def open(self, code):
        """A fresh session token for a valid code, which is used up; None otherwise."""
        self._sweep()
        grant = self.codes.pop(digest(code), None) if isinstance(code, str) and len(code) < 200 else None
        if grant is None:
            return None
        if len(self.sessions) >= MAX_SESSIONS:
            oldest = min(self.sessions, key=lambda key: self.sessions[key].expires)
            del self.sessions[oldest]
        token = secrets.token_urlsafe(32)
        self.sessions[digest(token)] = Grant(grant.owner, self.clock() + SESSION_SECONDS)
        return token

    def owner(self, token):
        self._sweep()
        grant = self.sessions.get(digest(token)) if isinstance(token, str) and len(token) < 200 else None
        return grant.owner if grant else None

    def end(self, owner=None):
        """Sign out one device's sessions (all of them when owner is None)."""
        for table in (self.codes, self.sessions):
            for key in [key for key, grant in table.items() if owner is None or grant.owner == owner]:
                del table[key]


def web_path(path):
    """The part after /ataria/, checked segment by segment; None when it could escape the web root."""
    if path == "":
        return ""
    parts = path.split("/")
    if any(not SEGMENT.match(part) for part in parts[:-1]) or (parts[-1] and not SEGMENT.match(parts[-1])):
        return None
    return "/".join(quote(part, safe="=+") for part in parts)


def secure_cookie(host):
    """Secure everywhere except plain http on this machine (the tray's loopback link)."""
    name = (host or "").rsplit(":", 1)[0].strip("[]").lower()
    return name not in ("127.0.0.1", "::1", "localhost")


class Pages:
    """Passes dashboard requests to the engine's web root."""

    def __init__(self, config):
        self.config = config

    def _fetch(self, method, path, query, body, content_type):
        token = read_token(self.config.token_file)
        url = self.config.web_url + "/" + path + ("?" + query if query else "")
        headers = {"Authorization": "Bearer " + token, "X-Ataria": "1", "Accept": "*/*"}
        if body is not None:
            headers["Content-Type"] = content_type or "application/json"
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
        try:
            with opener.open(request, timeout=30) as response:
                status, kind, raw = response.status, response.headers.get("Content-Type", ""), response.read(MAX_PAGE + 1)
        except urllib.error.HTTPError as error:
            status, kind, raw = error.code, error.headers.get("Content-Type", ""), error.read(MAX_PAGE + 1)
        except (urllib.error.URLError, OSError, TimeoutError):
            return 503, "text/plain; charset=utf-8", b"The Ataria engine is not answering on this computer."
        if len(raw) > MAX_PAGE:
            return 502, "text/plain; charset=utf-8", b"The Ataria engine's answer is too large."
        if 300 <= status < 400:
            status, kind, raw = 502, "text/plain; charset=utf-8", b"The Ataria engine tried to redirect."
        if kind.split(";")[0].strip().lower() not in TYPES:
            kind = "application/octet-stream"
        return status, kind, raw

    async def fetch(self, *args):
        return await asyncio.to_thread(self._fetch, *args)
