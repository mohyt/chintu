"""The merge: the lanes' items and todos into one daily board. Deterministic, no model call.

1. Read items and todos from every lane: the current month in full, plus still-open concepts
   from earlier months.
2. Apply done ticks (`app/done/<lane>.<id>.md`, one per tick). A done item comes back as reopened
   when its `updated_at` is after `done_at`. Ticking an item also ticks its todos.
3. Rank: P0 to P3, then consequence, due date and lane order. At most three P0s; the rest show as
   P1 with `capped: true`. Items marked `info: true` (nobody is waiting on the owner) go to the
   digest for the day they were seen or changed: never ranked, counted, ticked or sent.
4. Write `app/YYYY/MM/DD/today.md`. Lines outside work use the safe short title, so amounts and
   private details never reach the board's titles.
5. Write messages to the outbox: one summary to the owner, and a home update to each recipient
   who gets the family area, only when something is new. A delivery plugin sends them.
"""

import json
from datetime import timedelta

from . import vault as okf

VERSION = "merge/1"
LANE_RANK = {lane: index for index, lane in enumerate(okf.LANES)}
P_RANK = {p: index for index, p in enumerate(okf.PRIORITIES)}
# Consequence first: client or revenue, people blocked, money, a child, hygiene.
C_RANK = {c: index for index, c in enumerate(["client", "blocked", "money", "child", "hygiene"])}
DAILY_LANES = [lane for lane in okf.LANES if lane != "ledger"] + ["google"]
WORK_LANES = ("work", "notes", "meetings")


class Merge:
    def __init__(self, profile, v):
        self.profile, self.v = profile, v
        self.owner = (profile.get("owner") or {}).get("name") or "Owner"
        self.app = v.path("app")
        self.done_dir = self.app / "done"
        self.state = v.data / "app"
        self.outbox = v.data / "outbox"

    # --- reading ---
    def load_lane(self, lane, today):
        lane_dir = self.v.path(lane)
        current = (f"{today:%Y}", f"{today:%m}")
        out, errors = [], []
        for year, month, folder in okf.month_dirs(lane_dir):
            if (year, month) > current:
                continue
            for kind in ("items", "todos"):
                for path in sorted(folder.glob(f"[0-3][0-9]/{kind}/*.md")):
                    try:
                        front, body = okf.read(path)
                    except Exception as error:
                        errors.append(f"{self.v.logical(path)}: {error}")
                        continue
                    if okf.canonical_type(front.get("type")) not in ("Item", "Todo") or front.get("duplicate_of"):
                        continue
                    if (year, month) != current and front.get("state") != "open":
                        continue
                    out.append((path, front, body))
        return out, errors

    def last_run(self, lane, today):
        """The lane's newest run report (today, else the latest day folder)."""
        base = self.v.data / {"google": "inbox/google/runs", "interviews": "meetings"}.get(lane, lane)
        name = "run-interviews-*.md" if lane == "interviews" else "run-[0-9]*.md"
        runs = sorted(base.glob(f"[0-9][0-9][0-9][0-9]/[0-1][0-9]/[0-3][0-9]/{name}"), key=lambda p: (p.parent, p.name))
        if not runs:
            return {"result": "missing", "at": None, "sources_health": []}
        try:
            front, _ = okf.read(runs[-1])
        except Exception:
            return {"result": "unreadable", "at": None, "sources_health": []}
        at = self.v.parse_dt(front.get("started_at"))
        return {
            "result": front.get("result", "failed") if at and at.date() == today.date() else "not run today",
            "at": self.v.iso(at),
            "run_type": front.get("run_type"),
            "sources_health": front.get("sources_health") or [],
            "error": str(front.get("error") or ""),
            "path": str(runs[-1].relative_to(self.v.data)),
        }

    def load_done(self):
        done = {}
        for path in self.done_dir.glob("*.md"):
            try:
                done[path.stem] = self.v.parse_dt(okf.read(path)[0].get("done_at"))
            except Exception:
                continue
        return done

    def self_notes(self, entries, done, now):
        """Notes the owner sends themselves on WhatsApp, one raw item each, shown at once. When a notes
        item cites the message (`whatsapp-self://<id>`) the raw one is hidden, and a tick on the raw one
        carries over to the items that replaced it."""
        folder = self.v.path("inbox/whatsapp/self")
        by_ref = {}
        for entry in entries:
            if entry["lane"] == "notes":
                for link in entry["links"]:
                    if link["u"].startswith("whatsapp-self://"):
                        by_ref.setdefault(link["u"], []).append(entry)
        out = []
        for path in sorted(folder.glob("[0-9][0-9][0-9][0-9]/[0-1][0-9]/[0-3][0-9]/wa-*.md")):
            try:
                front, body = okf.read(path)
            except Exception:
                continue
            lid = str(front.get("id") or path.stem)
            uid = f"notes.{lid}"
            ref = next((s.get("resource") for s in front.get("sources") or [] if isinstance(s, dict)), "")
            got = self.v.parse_dt(front.get("received_at"))
            done_at = done.get(uid)
            if ref in by_ref:
                if done_at:
                    for entry in by_ref[ref]:
                        if entry["kind"] == "item" and not entry["done"] and entry["state"] == "open":
                            okf.write(self.done_dir / f'{entry["uid"]}.md', {
                                "type": "Ataria Done", "title": f'Done: {entry["id"]}', "id": entry["id"],
                                "lane": entry["lane"], "done_at": self.v.iso(done_at),
                                "generated": {"by": "auto:whatsapp-note", "at": self.v.iso(now)}})
                            entry["done"], entry["done_at"] = True, self.v.iso(done_at)
                continue
            text = body.strip()
            out.append({
                "uid": uid, "id": lid, "lane": "notes", "kind": "item", "info": False, "p": "P2",
                "title": text.splitlines()[0][:160] if text else lid, "tt": "WhatsApp note",
                "area": "", "pillar": "personal", "start": None, "end": None, "stats": None,
                "consequence": "", "tags": ["whatsapp"], "due": None, "owner": self.owner, "state": "open",
                "next": "Sorted by the next notes run", "item": "",
                "done": bool(done_at), "done_at": self.v.iso(done_at) if done_at else None, "reopened": False,
                "updated_at": self.v.iso(got), "stale": False, "verified": True, "by": "whatsapp:self",
                "path": self.v.logical(path), "first_seen": f"{got:%Y-%m-%d}" if got else "",
                "why": text[:600], "links": [],
            })
        return out

    # --- the board ---
    def entry(self, lane, path, front, body, done, now):
        why = next((part.strip() for part in body.split("\n\n") if part.strip() and not part.lstrip().startswith("#")), "")
        lid = front.get("id") or path.stem
        uid = f"{lane}.{lid}"
        updated = self.v.parse_dt(front.get("updated_at"))
        done_at = done.get(uid)
        reopened = bool(done_at and updated and updated > done_at)
        stale_after = self.v.parse_dt(front.get("stale_after"))
        safe = lane not in WORK_LANES
        is_item = okf.canonical_type(front["type"]) == "Item"
        return {
            "uid": uid, "id": lid, "lane": lane, "kind": "item" if is_item else "todo",
            "info": is_item and bool(front.get("info")),
            "p": front.get("p") if front.get("p") in P_RANK else "P3",
            "title": str(front.get("tt") if safe else (front.get("title") or front.get("tt") or lid)),
            "tt": str(front.get("tt") or lid),
            "area": front.get("area") or "",
            "pillar": front.get("pillar") or lane,
            "start": self.v.iso(self.v.parse_dt(front.get("start"))),
            "end": self.v.iso(self.v.parse_dt(front.get("end"))),
            "stats": front.get("stats") if isinstance(front.get("stats"), dict) else None,
            "consequence": front.get("consequence") or "",
            "tags": front.get("tags") or [],
            "due": str(front["due"]) if front.get("due") else None,
            "owner": front.get("owner") or "",
            "state": front.get("state") or "open",
            "outcome": str(front.get("outcome") or ""),
            "answered_via": str(front.get("answered_via") or ""),
            "next": "" if lane == "finance" else str(front.get("next") or ""),
            "item": front.get("item") or "",
            "done": bool(done_at) and not reopened,
            "done_at": self.v.iso(done_at) if done_at and not reopened else None,
            "reopened": reopened,
            "updated_at": self.v.iso(updated),
            "stale": bool(stale_after and now >= stale_after),
            "verified": human_verified(front),
            "by": (front.get("generated") or {}).get("by", ""),
            "path": self.v.logical(path),
            "first_seen": str(front.get("first_seen") or ""),
            "why": why[:600],
            "links": [{"t": str(s.get("title") or "source"), "u": str(s.get("resource"))}
                      for s in (front.get("sources") or []) if isinstance(s, dict) and s.get("resource")][:6],
        }

    def build(self, today):
        now = self.v.now()
        done = self.load_done()
        entries, errors = [], []
        for lane in okf.LANES:
            concepts, lane_errors = self.load_lane(lane, today)
            errors += lane_errors
            entries += [self.entry(lane, path, front, body, done, now) for path, front, body in concepts]
        entries += self.self_notes(entries, done, now)

        done_items = {e["uid"]: e["done_at"] for e in entries if e["kind"] == "item" and e["done"]}
        for e in entries:
            if e["kind"] == "todo" and not e["done"] and e["item"]:
                parent = f'{e["lane"]}.{e["item"]}'
                if parent in done_items:
                    e["done"], e["done_at"], e["linked_done"] = True, done_items[parent], True

        def done_today(e):
            if e["done"]:
                return self.v.parse_dt(e["done_at"]).date() == today.date()
            if e["state"] == "closed":
                updated = self.v.parse_dt(e["updated_at"])
                return bool(updated and updated.date() == today.date())
            return False

        def info_today(e):
            if not e["info"] or e["state"] != "open":
                return False
            return any(d and d.date() == today.date()
                       for d in (self.v.parse_dt(e["first_seen"]), self.v.parse_dt(e["updated_at"])))

        entries = [e for e in entries if (e["info"] and info_today(e)) or
                   (not e["info"] and ((e["state"] == "open" and not e["done"]) or done_today(e)))]
        open_items = {(e["lane"], e["id"]) for e in entries
                      if e["kind"] == "item" and e["state"] == "open" and not e["done"] and not e["info"]}
        for e in entries:
            e["nested"] = e["kind"] == "todo" and bool(e["item"]) and (e["lane"], e["item"]) in open_items
            e["group"] = "info" if e["info"] else "done" if (e["done"] or e["state"] == "closed") else "open"
            first = self.v.parse_dt(e["first_seen"])
            e["age_days"] = max(0, (today.date() - first.date()).days) if first else 0
            e["backlog"] = e["group"] == "open" and e["age_days"] > 0

        def key(e):
            return (P_RANK[e["p"]], C_RANK.get(e["consequence"], len(C_RANK)), e["due"] or "9999-12-31",
                    LANE_RANK[e["lane"]], e["kind"] != "item", e["title"].lower())

        entries.sort(key=key)
        p0 = 0
        for e in entries:
            if e["group"] == "open" and e["p"] == "P0" and not e["nested"]:
                p0 += 1
                if p0 > 3:
                    e["p"], e["capped"] = "P1", True
        entries.sort(key=key)
        return entries, errors

    def run(self, today=None, slot=None, outbox=True):
        now = self.v.now()
        today = today or now
        slot = slot or ("morning" if now.hour < 12 else "midday")
        entries, errors = self.build(today)
        lanes = {lane: self.last_run(lane, today) for lane in okf.LANES + ["google", "interviews", "review"]}
        c = counts_of(entries)
        front = {
            "type": "Ataria Today", "title": f"Ataria today {today:%Y-%m-%d}", "date": f"{today:%Y-%m-%d}",
            "slot": slot, "generated": {"by": VERSION, "at": self.v.iso(now)},
            "summary": summary_line(c, lanes), "counts": c, "lanes": lanes, "read_errors": errors, "items": entries,
        }
        out = okf.day_dir(self.app, today) / "today.md"
        okf.write(out, front, today_body(entries, lanes, today))
        self.prune_done(entries)
        if outbox:
            self.messages(entries, c, lanes, slot, today, now)
        return out, front

    # --- messages ---
    def recipients(self):
        """(owner's recipient or None, [other recipients who get the family area])."""
        everyone = [r for r in ((self.profile.get("delivery") or {}).get("recipients") or []) if isinstance(r, dict)]
        mine = next((r for r in everyone if r.get("name", "").lower() == self.owner.lower()), None)
        family = [r for r in everyone if r is not mine and "family" in (r.get("areas") or [])]
        return mine, family

    def messages(self, entries, c, lanes, slot, today, now):
        state_file = self.state / ".outbox-state.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        mine, family = self.recipients()
        wants = lambda r: slot not in ("morning", "midday", "evening") or slot in (r.get("digests") or [slot])
        family = [r for r in family if wants(r)]
        home, home_lines, state["home"] = home_text(entries, today, state, bool(family))
        names = ", ".join(r["name"] for r in family)
        if mine is None or wants(mine):
            self.message("me", slot, today, me_text(entries, c, lanes, slot, home_lines, names, self.dashboard_url()), now)
        if home:
            for r in family:
                self.message(r["name"].lower(), slot, today, home, now)
        self.state.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))

    def dashboard_url(self):
        return (self.profile.get("engine") or {}).get("dashboard_url") or ""

    def message(self, to, slot, today, text, now):
        front = {"type": "Ataria Message", "title": f"{slot} message to {to}", "id": f"{slot}-{to}",
                 "to": to, "slot": slot, "generated": {"by": VERSION, "at": self.v.iso(now)}}
        okf.write(okf.day_dir(self.outbox, today) / f"{slot}-{to}.md", front, text)

    def prune_done(self, entries):
        """Drop ticks older than 40 days whose concept no longer exists (cleared at month close)."""
        live = {e["uid"] for e in entries}
        cutoff = self.v.now() - timedelta(days=40)
        for path in self.done_dir.glob("*.md"):
            try:
                at = self.v.parse_dt(okf.read(path)[0].get("done_at"))
            except Exception:
                continue
            if path.stem not in live and not path.stem.startswith("focus.") and at and at < cutoff:
                path.unlink()

    def month_review(self):
        """The month-in-review message from the previous month's lane summaries (their `whatsapp` field)."""
        now = self.v.now()
        previous = now.replace(day=1) - timedelta(days=1)
        month = f"{previous:%Y-%m}"
        state_file = self.state / ".month-sent.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        sent = set(state.get(month, []))
        parts = []
        for lane in okf.LANES:
            path = self.v.path(lane) / f"{previous:%Y}" / f"{previous:%m}" / "summary.md"
            if lane in sent or not path.exists():
                continue
            try:
                front, _ = okf.read(path)
            except Exception:
                continue
            text = str(front.get("whatsapp") or "").strip()
            if text:
                parts.append((lane, text))
        if not parts:
            return []
        text = f"Ataria month in review · {previous:%B %Y}\n\n" + "\n\n".join(f"{lane.title()}\n{t}" for lane, t in parts)
        self.message("me", f"monthly-{now:%d}", now, text, now)
        state[month] = sorted(sent | {lane for lane, _ in parts})
        self.state.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))
        return [lane for lane, _ in parts]


def human_verified(front):
    value = front.get("verified")
    values = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    return any(str(v.get("by", "")).startswith("human:") for v in values if isinstance(v, dict))


def counts_of(entries):
    c = {p: 0 for p in okf.PRIORITIES}
    # Meetings and interviews with a start time are calendar events, not todos.
    top = [e for e in entries if not e.get("nested") and not (e["lane"] == "meetings" and e["kind"] == "item" and e.get("start"))]
    for e in top:
        if e["group"] == "open":
            c[e["p"]] += 1
    c["open"] = sum(1 for e in top if e["group"] == "open")
    c["done_today"] = sum(1 for e in entries if e["group"] == "done")
    return c


def today_body(entries, lanes, today):
    lines = [f"# Ataria today, {today:%a %d %b %Y}", ""]
    for p in okf.PRIORITIES:
        group = [e for e in entries if e["group"] == "open" and e["p"] == p]
        if group:
            lines += [f"## {p}", ""]
            for e in group:
                due = f" (due {e['due']})" if e["due"] else ""
                lines.append(f"- [ ] [{e['title']}]({e['path']}){due} · {e['lane']}")
            lines.append("")
    for head, group in (("Summary", [e for e in entries if e["group"] == "info" and e["area"] != "alert"]),
                        ("Alerts", [e for e in entries if e["group"] == "info" and e["area"] == "alert"])):
        if group:
            lines += [f"## {head}", ""] + [f"- [{e['title']}]({e['path']}) · {e['lane']}" for e in group] + [""]
    done = [e for e in entries if e["group"] == "done"]
    if done:
        lines += ["## Done today", ""] + [f"- [x] {e['title']} · {e['lane']}" for e in done] + [""]
    lines += ["## Lanes", ""]
    for lane, r in lanes.items():
        bad = [s["name"] for s in r.get("sources_health", []) if s.get("status") not in ("ok", "skipped")]
        lines.append(f"- {lane}: {r['result']}" + (f" (sources: {', '.join(bad)})" if bad else ""))
    return "\n".join(lines)


def needs_look(lanes):
    """Lanes that failed or did not run (daily lanes only), and failed sources of daily lanes."""
    bad = []
    for lane, r in lanes.items():
        if r["result"] in ("failed", "unreadable") or (lane in DAILY_LANES and r["result"] in ("missing", "not run today")):
            bad.append(f"{lane} ({r['result']})")
        elif lane in DAILY_LANES:
            bad += [f"{lane}/{s['name']}" for s in r.get("sources_health", []) if s.get("status") == "failed"]
    return bad


def summary_line(c, lanes):
    bad = needs_look(lanes)
    line = f"{c['open']} open: {c['P0']} P0, {c['P1']} P1, {c['P2']} P2, {c['P3']} P3; {c['done_today']} done today."
    return line + (f" Needs a look: {', '.join(bad)}." if bad else "")


def me_text(entries, c, lanes, slot, home_lines, family_names, url):
    open_ = [e for e in entries if e["group"] == "open" and not e.get("nested")]
    p0 = [e for e in open_ if e["p"] == "P0"]
    p1 = [e for e in open_ if e["p"] == "P1"]
    if p0:
        out = [f"Ataria {slot}: {len(p0)} P0 · {p0[0]['tt']}"]
        out += [f"• {e['tt']}" + (f": {e['next']}" if e["next"] else "") for e in p0]
    else:
        out = [f"Ataria {slot}: No P0" + (f" · top P1: {p1[0]['tt']}" if p1 else "")]
    out.append(f"P1 {c['P1']} · P2 {c['P2']} · done today {c['done_today']}")
    bad = needs_look(lanes)
    if bad:
        out.append("Unavailable: " + ", ".join(bad))
    if url:
        out.append(f"Dashboard: {url}")
    if home_lines:
        out += ["", f"Home (for {family_names}):" if family_names else "Home:"] + home_lines
    return "\n".join(out)


def home_text(entries, today, state, anyone):
    """The home update: new or changed open items tagged home or kids outside work, safe titles only.
    None when nothing is new or nobody gets it."""
    sent = state.get("home", {})
    if not anyone:
        return None, [], sent
    fresh = []
    for e in entries:
        if e["lane"] in ("work", "meetings") or e.get("pillar") == "work" or e["group"] != "open":
            continue
        if not ({"home", "kids"} & {str(t).lower() for t in e["tags"]}):
            continue
        if sent.get(e["uid"]) != e["updated_at"]:
            fresh.append(e)
    if not fresh:
        return None, [], sent
    for e in fresh:
        sent[e["uid"]] = e["updated_at"]
    lines = [f"• {e['tt']}" + (f" (by {e['due']})" if e["due"] else "") for e in fresh[:8]]
    return f"Ataria home update, {today:%a} {today.day} {today:%b}\n\n" + "\n".join(lines), lines, sent
