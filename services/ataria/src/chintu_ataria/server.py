"""The engine's HTTP server on loopback: the API the gateway calls, and the dashboard it passes through.

  GET    {base}/                     the dashboard (defaults/web)
  GET    {base}/api/config           what the dashboard shows for this owner (from the profile)
  GET    {base}/api/today[?date=]    the newest (or that day's) board, ticks applied live
  GET    {base}/api/days             days that have a board, newest first, with counts
  GET    {base}/api/md?path=         one lane note
  GET    {base}/api/asset?path=      an image beside a lane note (svg, png, jpg, webp)
  GET    {base}/api/months           monthly summaries across lanes
  POST   {base}/api/done             {uid}, or {done: [...], undone: [...]}
  DELETE {base}/api/done/<uid>       untick
  GET    {base}/api/v1/status        the gateway's contract (docs/13-ataria.md): {"latest_date"}
  GET    {base}/api/v1/today[?date=] the board in the contract's AtariaBoard shape
  POST   {base}/api/v1/done          {done: [...], undone: [...]} -> {done, undone, done_at}

Every request must come from the gateway on this machine, proven by a bearer token whose SHA-256
is in `engine.token_sha256_file` (default: `engine-token.sha256` in the state folder); this process never holds the token itself. When the profile
lists `engine.allowed_logins`, a request with one of those `Tailscale-User-Login` headers (added
by `tailscale serve`) is also let in. Writes need `X-Ataria: 1`.
"""

import hashlib
import hmac
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import vault as okf

UID_RE = re.compile(r"^(work|meetings|personal|finance|ledger|notes|focus)\.[A-Za-z0-9][A-Za-z0-9._=+-]{0,160}$")
TYPES = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".html": "text/html; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
         ".png": "image/png", ".webmanifest": "application/manifest+json", ".json": "application/json"}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
AUTO = "auto:todos"


def web_root():
    return Path(str(resources.files("chintu_ataria").joinpath("defaults/web")))


def dashboard_config(profile):
    """What the dashboard needs to know about the owner; nothing secret, nothing it does not show."""
    owner = profile.get("owner") or {}
    personal = profile.get("personal") or {}
    schedule = profile.get("schedule") or {}
    family = personal.get("family") or []
    growth = personal.get("growth") or {}
    return {
        "owner": owner.get("name") or "",
        "locale": owner.get("locale") or "en-GB",
        "times": [t for t in (schedule.get("morning"), schedule.get("midday")) if t],
        "kids": any(str(m.get("relation", "")).lower() in ("child", "son", "daughter", "kid") for m in family if isinstance(m, dict)),
        "work": bool((profile.get("work") or {}).get("enabled", True)),
        "region": {"name": growth.get("region_name") or "", "tag": growth.get("region_tag") or ""},
        "course": growth.get("course") if isinstance(growth.get("course"), dict) else None,
    }


class Engine:
    """The board files and ticks behind the server."""

    def __init__(self, profile, v):
        self.profile, self.v = profile, v
        self.app = v.path("app")
        self.done_dir = self.app / "done"
        self._counts = {}

    def today_files(self):
        return sorted(self.app.glob("[0-9][0-9][0-9][0-9]/[0-1][0-9]/[0-3][0-9]/today.md"))

    def day_counts(self, path):
        try:
            mtime = path.stat().st_mtime_ns
            hit = self._counts.get(path)
            if hit and hit[0] == mtime:
                return hit[1]
            counts = okf.read(path)[0].get("counts") or {}
            self._counts[path] = (mtime, counts)
            return counts
        except Exception:
            return {}

    def latest(self, date=None):
        if date:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                return None
            path = self.app / date.replace("-", "/") / "today.md"
            return path if path.exists() else None
        files = self.today_files()
        return files[-1] if files else None

    def done_map(self):
        out = {}
        for path in self.done_dir.glob("*.md"):
            try:
                out[path.stem] = okf.read(path)[0].get("done_at")
            except Exception:
                pass
        return out

    def apply_done(self, items, done):
        """Done flags from the current ticks, so a tick shows before the next merge."""
        by_uid = {}
        for e in items:
            d = self.v.parse_dt(done.get(e["uid"]))
            updated = self.v.parse_dt(e.get("updated_at"))
            e["reopened"] = bool(d and updated and updated > d)
            e["done"] = bool(d) and not e["reopened"]
            e["done_at"] = self.v.iso(d) if e["done"] else None
            e["linked_done"] = False
            by_uid[e["uid"]] = e
        for e in items:
            if e["kind"] == "todo" and not e["done"] and e.get("item"):
                parent = by_uid.get(f'{e["lane"]}.{e["item"]}')
                if parent and parent["done"]:
                    e["done"], e["done_at"], e["linked_done"] = True, parent["done_at"], True
        for e in items:
            e["group"] = "info" if e.get("info") else "done" if (e["done"] or e.get("state") == "closed") else "open"
        return items

    def sync_parents(self, now, touched):
        """Close an item once every todo under it is done; reopen it when a todo is unticked, but only if
        that close was automatic. Only items whose todos changed in this request are checked."""
        path = self.latest()
        if not path:
            return []
        items = self.apply_done(okf.read(path)[0].get("items") or [], self.done_map())
        kids = {}
        for e in items:
            if e["kind"] == "todo" and e.get("item"):
                kids.setdefault(f'{e["lane"]}.{e["item"]}', []).append(e)
        changed = []
        for e in items:
            todos = kids.get(e["uid"])
            if e["kind"] != "item" or not todos or e["uid"] in touched or not any(k["uid"] in touched for k in todos):
                continue
            all_done = all((k["done"] and not k["linked_done"]) or k.get("state") == "closed" for k in todos)
            tick = self.done_dir / f'{e["uid"]}.md'
            auto = tick.exists() and (okf.read(tick)[0].get("generated") or {}).get("by") == AUTO
            if all_done and not e["done"] and e.get("state") != "closed":
                okf.write(tick, {"type": "Ataria Done", "title": f'Done: {e["id"]}', "id": e["id"], "lane": e["lane"],
                                 "done_at": self.v.iso(now), "generated": {"by": AUTO, "at": self.v.iso(now)}})
                changed.append(e["uid"])
            elif not all_done and auto:
                tick.unlink(missing_ok=True)
                changed.append(e["uid"])
        return changed

    def tick(self, done, undone, who):
        now = self.v.now()
        for uid in undone:
            (self.done_dir / f"{uid}.md").unlink(missing_ok=True)
        for uid in done:
            lane, cid = uid.split(".", 1)
            okf.write(self.done_dir / f"{uid}.md", {
                "type": "Ataria Done", "title": f"Done: {cid}", "id": cid, "lane": lane,
                "done_at": self.v.iso(now), "generated": {"by": f"human:{who}", "at": self.v.iso(now)}})
        self.sync_parents(now, set(done) | set(undone))
        return self.v.iso(now)

    def safe_file(self, rel, exts=(".md",)):
        """A lane file named by a logical path, or None."""
        rel = unquote(rel or "").lstrip("/")
        if not rel.endswith(exts) or any(part.startswith(".") for part in rel.split("/")):
            return None
        path = self.v.resolve(rel).resolve()
        roots = [self.v.data / lane for lane in okf.LANES] + [self.v.data / "inbox" / "whatsapp" / "self"]
        roots += [self.v.path(lane) for lane in okf.LANES] + [self.v.path("inbox/whatsapp/self")]
        for root in roots:
            if path.is_relative_to(root.resolve()) and path.is_file():
                return path
        return None

    def months(self):
        out = []
        for lane in okf.LANES:
            for path in sorted(self.v.path(lane).glob("[0-9][0-9][0-9][0-9]/[0-1][0-9]/summary.md"), reverse=True):
                try:
                    front, _ = okf.read(path)
                except Exception:
                    continue
                out.append({"lane": lane, "month": str(front.get("month", "")), "title": front.get("title", ""),
                            "counts": front.get("counts") or {}, "path": self.v.logical(path)})
        return sorted(out, key=lambda m: (m["month"], m["lane"]), reverse=True)

    def contract_board(self, front, items):
        """A board in the shape of Chintu's AtariaBoard contract; the gateway trims and validates it again."""
        out = []
        for e in items:
            p = str(e.get("p") or "P3")
            out.append({
                "uid": e["uid"], "lane": e.get("lane", ""), "kind": "todo" if e.get("kind") == "todo" else "item",
                "priority": p if p in okf.PRIORITIES else "P3", "group": e.get("group", "open"),
                "title": str(e.get("title") or e.get("tt") or e["uid"]), "short_title": _text(e.get("tt")),
                "done": bool(e.get("done")), "done_at": e.get("done_at"), "due": _date(e.get("due")),
                "owner": _text(e.get("owner")), "next": _text(e.get("next")), "why": _text(e.get("why")),
                "area": _text(e.get("area")),
                "parent": f'{e["lane"]}.{e["item"]}' if e.get("kind") == "todo" and e.get("item") else None,
                "links": [{"title": str(link.get("t") or "Link"), "url": str(link["u"])} for link in (e.get("links") or [])
                          if isinstance(link, dict) and str(link.get("u", "")).startswith(("http://", "https://"))],
                "stale": bool(e.get("stale"))})
        generated = front.get("generated") or {}
        counts = {k: n for k, n in (front.get("counts") or {}).items() if isinstance(n, int) and n >= 0}
        at = generated.get("at") if isinstance(generated, dict) else None
        return {"date": str(front.get("date"))[:10], "title": str(front.get("title") or ""), "slot": _text(front.get("slot")),
                "generated_at": self.v.iso(self.v.parse_dt(at)) if at else None,
                "summary": str(front.get("summary") or ""), "counts": counts, "items": out}


def _text(value):
    return str(value) if value not in (None, "") else None


def _date(value):
    value = _text(value)
    return value[:10] if value and re.match(r"^\d{4}-\d{2}-\d{2}", value) else None


def make_handler(engine, base, token_hash_file, allowed_logins=(), static=None):
    base = base.rstrip("/")
    static = Path(static or web_root())
    allowed_logins = set(allowed_logins or ())
    config = dashboard_config(engine.profile)

    class Handler(BaseHTTPRequestHandler):
        server_version = "ataria"

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

        def send(self, code, body=b"", kind="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, default=str).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("content-type", kind)
            self.send_header("content-length", str(len(body)))
            self.send_header("cache-control", "no-store")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("referrer-policy", "no-referrer")
            extra = dict(extra or {})
            self.send_header("content-security-policy", extra.pop("content-security-policy", CSP))
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def gateway(self):
            """The Chintu gateway on this machine, proven by the bearer token whose hash is on file."""
            auth = self.headers.get("Authorization", "")
            if not token_hash_file or not auth.startswith("Bearer ") or self.client_address[0] not in ("127.0.0.1", "::1"):
                return False
            try:
                want = Path(token_hash_file).read_text().strip().lower()
            except OSError:
                return False
            got = hashlib.sha256(auth[7:].strip().encode()).hexdigest()
            return len(want) == 64 and hmac.compare_digest(got, want)

        def allowed(self):
            return self.gateway() or (bool(allowed_logins) and self.headers.get("Tailscale-User-Login", "") in allowed_logins)

        def route(self):
            url = urlparse(self.path)
            if url.path != base and not url.path.startswith(base + "/"):
                return None, None
            return url.path[len(base):] or "/", parse_qs(url.query)

        def do_GET(self):
            if not self.allowed():
                return self.send(403, {"error": "not allowed"})
            path, query = self.route()
            if path is None:
                return self.send(404, {"error": "not found"})
            if path == "/" and not urlparse(self.path).path.endswith("/"):
                return self.send(301, b"", extra={"location": base + "/"})
            arg = lambda name: (query.get(name) or [""])[0]
            if path == "/api/config":
                return self.send(200, config)
            if path == "/api/days":
                return self.send(200, [{"date": "-".join(f.parts[-4:-1]), "counts": engine.day_counts(f)}
                                       for f in reversed(engine.today_files()[-60:])])
            if path == "/api/today":
                found = engine.latest(arg("date"))
                if not found:
                    return self.send(404, {"error": "no board yet"})
                front, body = okf.read(found)
                done = engine.done_map()
                front["items"] = engine.apply_done(front.get("items") or [], done)
                front["focus_done"] = {k: d for k, d in done.items() if k.startswith("focus.")}
                return self.send(200, {"path": engine.v.logical(found), "frontmatter": front, "body": body})
            if path == "/api/md":
                found = engine.safe_file(arg("path"))
                if not found:
                    return self.send(404, {"error": "not found"})
                front, body = okf.read(found)
                return self.send(200, {"path": engine.v.logical(found), "frontmatter": front, "body": body})
            if path == "/api/asset":
                found = engine.safe_file(arg("path"), exts=(".svg", ".png", ".jpg", ".jpeg", ".webp"))
                if not found:
                    return self.send(404, {"error": "not found"})
                return self.send(200, found.read_bytes(), TYPES.get(found.suffix, "image/jpeg"),
                                 extra={"content-security-policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'"})
            if path == "/api/months":
                return self.send(200, engine.months())
            if path.startswith("/api/v1/"):
                if not self.gateway():
                    return self.send(403, {"error": "not allowed"})
                if path == "/api/v1/status":
                    found = engine.latest()
                    return self.send(200, {"latest_date": "-".join(found.parts[-4:-1]) if found else None})
                if path == "/api/v1/today":
                    found = engine.latest(arg("date"))
                    if not found:
                        return self.send(404, {"error": "no board for that day"})
                    front, _ = okf.read(found)
                    return self.send(200, engine.contract_board(front, engine.apply_done(front.get("items") or [], engine.done_map())))
                return self.send(404, {"error": "not found"})
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            found = (static / name).resolve()
            if not found.is_relative_to(static.resolve()) or not found.is_file():
                return self.send(404, {"error": "not found"})
            return self.send(200, found.read_bytes(), TYPES.get(found.suffix, "application/octet-stream"))

        do_HEAD = do_GET

        def write_ok(self):
            return self.allowed() and self.headers.get("X-Ataria") == "1"

        def do_POST(self):
            if not self.write_ok():
                return self.send(403, {"error": "not allowed"})
            path, _ = self.route()
            if path == "/api/v1/done" and not self.gateway():
                return self.send(403, {"error": "not allowed"})
            if path not in ("/api/done", "/api/v1/done"):
                return self.send(404, {"error": "not found"})
            try:
                length = min(int(self.headers.get("content-length", "0")), 65536)
                body = json.loads(self.rfile.read(length) or b"{}")
                done = [body["uid"]] if "uid" in body else list(body.get("done") or [])
                undone = list(body.get("undone") or [])
            except (ValueError, TypeError, KeyError, AttributeError):
                return self.send(400, {"error": "bad json"})
            uids = done + undone
            bad = [u for u in uids if not (isinstance(u, str) and UID_RE.match(u))]
            if not uids or len(uids) > 500 or bad:
                return self.send(400, {"error": "bad uid", "uids": [str(u)[:200] for u in bad][:20]})
            who = "chintu" if self.gateway() else self.headers.get("Tailscale-User-Login", "local")
            at = engine.tick(done, undone, who)
            if "uid" in body:
                return self.send(200, {"uid": body["uid"], "done_at": at})
            return self.send(200, {"done": done, "undone": undone, "done_at": at})

        def do_DELETE(self):
            if not self.write_ok():
                return self.send(403, {"error": "not allowed"})
            path, _ = self.route()
            match = re.match(r"^/api/done/(.+)$", path or "")
            if not match or not UID_RE.match(match.group(1)):
                return self.send(404, {"error": "not found"})
            engine.tick([], [match.group(1)], "local")
            return self.send(200, {"uid": match.group(1), "done": False})

    return Handler


def serve(profile, v, port=None, announce=print):
    settings = profile.get("engine") or {}
    port = int(port or settings.get("port") or 8787)
    base = settings.get("base") or "/ataria"
    # The tray writes this file (chintu_desktop.engine.prepare); without it nothing can get in.
    token_hash_file = settings.get("token_sha256_file") or str(v.data / "engine-token.sha256")
    engine = Engine(profile, v)
    engine.done_dir.mkdir(parents=True, exist_ok=True)
    handler = make_handler(engine, base, token_hash_file, settings.get("allowed_logins") or ())
    announce(f"ataria engine on http://127.0.0.1:{port}{base}/")
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
