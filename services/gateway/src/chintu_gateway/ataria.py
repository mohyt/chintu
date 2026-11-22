"""Pass phone requests to the Ataria engine on this machine (docs/13-ataria.md, D25).

The engine is a separate process. The gateway calls it over loopback HTTP with a bearer token
read from a local file, keeps only the fields the contract defines, and validates the result,
so whatever the engine returns, the phone only ever sees contract-shaped data.

Engine API, relative to the configured base URL:

  GET  {url}/status          {"latest_date": "YYYY-MM-DD" | null}
  GET  {url}/today[?date=]   a board (AtariaBoard fields; extra fields are dropped)
  POST {url}/done            {"done": [uid...], "undone": [uid...]} -> {"done", "undone", "done_at"}

Every engine request carries `Authorization: Bearer <token>` and `X-Ataria: 1`.
"""

import asyncio
import ipaddress
import json
import os
import re
import stat
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from jsonschema import ValidationError

from .errors import ApiError

MAX_RESPONSE = 4 * 1024 * 1024
TIMEOUT_SECONDS = 15
STATUS_TIMEOUT_SECONDS = 3
DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
ITEM_FIELDS = ("uid", "lane", "kind", "priority", "group", "title", "short_title", "done", "done_at", "due",
               "owner", "next", "why", "area", "parent", "links", "stale")
LIMITS = {"title": 1000, "short_title": 200, "owner": 200, "next": 2000, "why": 4000, "area": 64}


@dataclass(frozen=True)
class AtariaConfig:
    url: str
    token_file: Path


def parse_config(value, base_dir):
    """The optional `ataria` block of gateway.json: {"url": loopback http URL, "token_file": path}."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"url", "token_file"}:
        raise ValueError("ataria needs exactly url and token_file")
    url, token_file = value["url"], value["token_file"]
    if not isinstance(url, str) or not isinstance(token_file, str) or len(url) > 512 or len(token_file) > 1024:
        raise ValueError("ataria url and token_file must be short strings")
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("ataria url must be a plain http URL")
    if not loopback(parts.hostname):
        raise ValueError("ataria url must point at this machine (127.0.0.1, ::1 or localhost)")
    path = Path(token_file).expanduser()
    if not path.is_absolute():
        path = Path(base_dir) / path
    return AtariaConfig(url.rstrip("/"), path.resolve())


def loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def read_token(path):
    """The engine token. Refused when other users can read it."""
    try:
        info = os.stat(path)
        if info.st_mode & stat.S_IROTH or info.st_size > 4096:
            raise ApiError(503, "ataria_unavailable", "The Ataria token file is readable by other users; restrict it.")
        token = Path(path).read_text().strip()
    except OSError:
        raise ApiError(503, "ataria_unavailable", "The Ataria token file is missing.", retryable=False) from None
    if not 32 <= len(token) <= 512 or any(c.isspace() for c in token):
        raise ApiError(503, "ataria_unavailable", "The Ataria token file does not hold a usable token.")
    return token


class Engine:
    def __init__(self, config, contract):
        self.config, self.contract = config, contract

    def _call(self, method, path, body=None, timeout=TIMEOUT_SECONDS):
        token = read_token(self.config.token_file)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.config.url + path, data=data, method=method, headers={
            "Authorization": "Bearer " + token, "X-Ataria": "1", "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {})})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise ApiError(404, "not_found", "Ataria has no board for that day yet.") from None
            raise ApiError(502, "ataria_error", f"The Ataria engine returned HTTP {error.code}.", retryable=True) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise ApiError(503, "ataria_unavailable", "The Ataria engine is not answering on this computer.",
                           retryable=True) from None
        if len(raw) > MAX_RESPONSE:
            raise ApiError(502, "ataria_error", "The Ataria engine's answer is too large.")
        try:
            return json.loads(raw)
        except ValueError:
            raise ApiError(502, "ataria_error", "The Ataria engine did not answer with JSON.") from None

    async def call(self, *args, **kwargs):
        return await asyncio.to_thread(self._call, *args, **kwargs)

    def _checked(self, schema, value):
        try:
            return self.contract.validate(schema, value)
        except ValidationError:
            raise ApiError(502, "ataria_error", "The Ataria engine's answer does not match the contract.") from None

    async def status(self):
        try:
            value = await self.call("GET", "/status", timeout=STATUS_TIMEOUT_SECONDS)
        except ApiError as error:
            return {"enabled": True, "reachable": False, "latest_date": None,
                    "reason": error.body["error"]["message"][:300]}
        latest = value.get("latest_date") if isinstance(value, dict) else None
        latest = latest if isinstance(latest, str) and DATE.match(latest) else None
        return self.contract.validate("AtariaStatus", {"enabled": True, "reachable": True, "latest_date": latest,
                                                       "reason": None})

    async def board(self, date=None):
        if date is not None and not DATE.match(date):
            raise ApiError(400, "invalid_request", "date must be YYYY-MM-DD.")
        value = await self.call("GET", "/today" + ("?" + urlencode({"date": date}) if date else ""))
        return self._checked("AtariaBoard", board(value))

    async def done(self, request):
        if not request["done"] and not request["undone"]:
            raise ApiError(422, "invalid_request", "Name at least one item to tick or untick.")
        if set(request["done"]) & set(request["undone"]):
            raise ApiError(422, "invalid_request", "An item cannot be ticked and unticked at once.")
        value = await self.call("POST", "/done", {"done": request["done"], "undone": request["undone"]})
        if not isinstance(value, dict):
            raise ApiError(502, "ataria_error", "The Ataria engine's answer does not match the contract.")
        return self._checked("AtariaDoneResult", {key: value.get(key) for key in ("done", "undone", "done_at")})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def clip(value, limit):
    return value[:limit] if isinstance(value, str) else value


def item(value):
    """Keep only the contract's fields; trim long text rather than refusing the whole board."""
    if not isinstance(value, dict):
        return value
    out = {key: value[key] for key in ITEM_FIELDS if key in value}
    for key, limit in LIMITS.items():
        if key in out:
            out[key] = clip(out[key], limit)
    if isinstance(out.get("links"), list):
        out["links"] = [{"title": clip(link.get("title", ""), 200), "url": link.get("url")}
                        for link in out["links"][:20] if isinstance(link, dict)]
    return out


def board(value):
    if not isinstance(value, dict):
        return value
    out = {key: value[key] for key in ("date", "title", "slot", "generated_at", "summary", "counts", "items") if key in value}
    out["title"] = clip(out.get("title", ""), 300)
    out["summary"] = clip(out.get("summary", ""), 8000)
    if isinstance(out.get("items"), list):
        out["items"] = [item(entry) for entry in out["items"][:1000]]
    return out
