"""`chintu-desktop ataria init` and `doctor`: create or edit ataria.json by asking, then check it.

init asks in four parts: A. about you, B. personal life, C. work, D. the jobs (one block per job:
whether it runs, which agent, which model) and how Ataria runs. Each question says what the answer
is used for. Re-running it edits: each question shows the current answer and Enter keeps it.
Nothing is written until the owner confirms the summary. No question asks for a password or token;
those stay in the agents' own logins or the system's credential store (`secrets` holds references).
"""

import os
import shutil
import sys
import time
from pathlib import Path

from . import profile, templates

AGENTS = {
    "claude": "Claude Code (`claude`): Anthropic's agent; uses your Claude Code login and its MCP connectors",
    "codex": "Codex (`codex`): OpenAI's agent; uses your Codex login and its apps, such as the Google connector",
}

# Every job Ataria runs: the area it belongs to, what it does, and the opinionated default agent.
JOBS = (
    ("work", "work", "Reads your work mail, chat, tickets and code reviews and writes work items and todos",
     "claude", "claude-opus-5-5", False),
    ("meetings", "work", "Reads your calendar and meeting transcripts and writes agendas, prep and follow-ups",
     "claude", "claude-opus-5-5", False),
    ("interviews", "work", "Turns interview transcripts into scorecards (only if you interview people)",
     "claude", "claude-opus-5-5", False),
    ("google", "personal", "Reads personal Gmail and Google Calendar and saves extracts the other personal jobs use",
     "codex", "gpt-6.1-sol", False),
    ("personal", "personal", "Family and school, your interests and the news digest; looks things up on the web",
     "codex", "gpt-6.1-sol", True),
    ("notes", "personal", "Turns voice notes you dictate to Chintu into todos and notes",
     "claude", "claude-opus-5-5", False),
    ("finance", "finance", "Reads bank and card statements and bills; tracks spending and budget",
     "codex", "gpt-6.1-sol", False),
    ("ledger", "finance", "Keeps the monthly ledger built from your statements",
     "codex", "gpt-6.1-sol", False),
    ("review", "always", "Checks each board for mistakes, duplicates and missed items before it is shown",
     "codex", "gpt-6.1-sol", False),
)
PERSONAL_SOURCES = (
    ("gmail", "Personal Gmail (through the google job)", True),
    ("google_calendar", "Personal Google Calendar", True),
    ("feeds", "News and blog feeds for the digest (no login needed)", True),
    ("chintu_notes", "Voice notes you dictate to Chintu", True),
)
WORK_SOURCES = (
    ("microsoft365", "Work mail and calendar in Microsoft 365 (Outlook)", True),
    ("teams", "Microsoft Teams chats and channels", True),
    ("jira", "Jira tickets", False),
    ("github", "GitHub pull requests, reviews and CI", False),
    ("meetings", "Meeting invites and transcripts", True),
    ("whatsapp_groups", "WhatsApp work groups (needs a WhatsApp bridge on this computer)", False),
)
FEED_PACKS = ("world", "regional", "tech", "ai", "business", "cloud", "security", "regulators")
DIGESTS = ("morning", "midday", "evening", "month")
AREAS = ("work", "personal", "family", "finance")


class Prompter:
    """Questions on a terminal. `answers` replaces input for tests and scripted installs."""

    def __init__(self, stdin=None, stdout=None, answers=None):
        self.stdin, self.stdout = stdin or sys.stdin, stdout or sys.stdout
        self.answers = list(answers) if answers is not None else None

    def say(self, text=""):
        print(text, file=self.stdout)

    def part(self, title, intro):
        self.say(f"\n{title}\n{'=' * len(title)}\n{intro}")

    def heading(self, title, note=None):
        self.say(f"\n{title}" + (f"\n  {note}" if note else ""))

    def _read(self, prompt):
        if self.answers is not None:
            self.stdout.write(prompt)
            if not self.answers:
                raise EOFError("the scripted answers ran out")
            value = self.answers.pop(0)
            self.stdout.write(value + "\n")
            return value
        self.stdout.write(prompt)
        self.stdout.flush()
        line = self.stdin.readline()
        if not line:
            raise EOFError("no more input")
        return line.rstrip("\n")

    def ask(self, question, current=None, required=False, check=None, why=None):
        if why:
            self.say(f"  ({why})")
        hint = f" [{current}]" if current not in (None, "") else ""
        while True:
            value = self._read(f"  {question}{hint}: ").strip()
            if not value:
                value = "" if current is None else str(current)
            if value == "-":
                value = ""
            if required and not value:
                self.say("    This one is needed.")
                continue
            if value and check:
                problem = check(value)
                if problem:
                    self.say(f"    {problem}")
                    continue
            return value

    def yes(self, question, current=True, why=None):
        if why:
            self.say(f"  ({why})")
        value = self._read(f"  {question} [{'Y/n' if current else 'y/N'}]: ").strip().lower()
        return current if not value else value.startswith("y")

    def items(self, question, current=(), why=None):
        value = self.ask(question + " (comma separated, - for none)", ", ".join(current), why=why)
        return [part.strip() for part in value.split(",") if part.strip()]

    def choice(self, question, options, current=None, why=None):
        return self.ask(f"{question} ({'/'.join(options)})", current or options[0], why=why,
                        check=lambda v: None if v in options else f"Choose one of: {', '.join(options)}")


def detected_timezone():
    if os.environ.get("TZ") and "/" in os.environ["TZ"]:
        return os.environ["TZ"]
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    try:
        return Path("/etc/timezone").read_text().strip() or "UTC"
    except OSError:
        return "UTC"


def valid_timezone(value):
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(value)
        return None
    except Exception:
        return "Not a timezone name; use one like Asia/Singapore or Europe/London."


def valid_clock(value):
    parts = value.split(":")
    ok = (len(parts) == 2 and all(p.isdigit() for p in parts) and len(parts[0]) == 2
          and int(parts[0]) < 24 and int(parts[1]) < 60)
    return None if ok else "Use 24-hour HH:MM, for example 07:00."


def detected_vault():
    if os.environ.get("DATA_HOME"):
        return os.environ["DATA_HOME"]
    try:
        for line in Path("~/.config/ataria/data.env").expanduser().read_text().splitlines():
            key, _, value = line.strip().partition("=")
            if key == "DATA_HOME" and value.strip():
                return value.strip().strip("'\"")
    except OSError:
        pass
    return "~/vaults/main"


def _sources(p, catalogue, current, packs=False):
    out = dict(current or {})
    for name, label, default in catalogue:
        entry = dict(out.get(name) or {})
        entry["enabled"] = p.yes(label + "?", entry.get("enabled", default))
        if packs and name == "feeds" and entry["enabled"]:
            entry["packs"] = p.items(f"Feed packs ({', '.join(FEED_PACKS)})", entry.get("packs", ("world", "tech", "ai")),
                                     why="topics for the outside-world part of your digest")
        out[name] = entry
    return out


def _context(p, current, questions):
    out = dict(current or {})
    for key, question, why in questions:
        value = p.ask(question, out.get(key), why=why)
        if value:
            out[key] = value
        else:
            out.pop(key, None)
    return out


def interview(current=None, ask=None):
    """Ask every part; returns the new profile (not yet saved). `current` supplies the defaults."""
    p = ask or Prompter()
    cur = current or {}
    first = current is None
    out = {key: value for key, value in cur.items()}   # unknown keys are kept
    out["schema"] = 1

    # --- A. About you --------------------------------------------------------------------------
    p.part("A. About you", "Shared by every job.")
    owner = dict(cur.get("owner") or {})
    owner["name"] = p.ask("What should Ataria call you", owner.get("name"), required=True,
                          why="used in every prompt and on the board")
    owner["full_name"] = p.ask("Full name", owner.get("full_name"), why="recognises mail and documents addressed to you")
    owner["city"] = p.ask("City", owner.get("city"), why="local news, weather and travel")
    owner["timezone"] = p.ask("Timezone", owner.get("timezone") or detected_timezone(), required=True,
                              check=valid_timezone, why="what 'today' and due times mean")
    locale = (os.environ.get("LANG") or "en_GB").split(".")[0].replace("_", "-")
    owner["locale"] = p.ask("Language and region", owner.get("locale") or locale, why="dates and number formats")
    out["owner"] = {k: v for k, v in owner.items() if v}
    out["vault"] = p.ask("Vault folder", cur.get("vault") or detected_vault(), required=True,
                         why="the folder of Markdown notes Ataria writes the board into")
    vault = Path(out["vault"]).expanduser()
    if not vault.exists() and p.yes(f"{vault} does not exist. Create it now?", True):
        vault.mkdir(parents=True, mode=0o700, exist_ok=True)

    # --- B. Personal life ----------------------------------------------------------------------
    p.part("B. Personal life", "Read by the google, personal, notes, finance and ledger jobs. Kept apart from work.")
    personal = dict(cur.get("personal") or {})
    personal["email"] = p.ask("Personal email", personal.get("email"),
                              why="tells your own mail apart from mail you are copied on")
    p.heading("Family", "school notices, activities and home todos are matched to these people")
    family = list(personal.get("family") or [])
    if family:
        p.say("  Current: " + ", ".join(f"{m.get('name')} ({m.get('relation')})" for m in family))
        if not p.yes("Keep this list?", True):
            family = []
    while p.yes("Add a family member?", first and not family):
        member = {"name": p.ask("Name", required=True), "relation": p.ask("Relation (partner, child, parent...)", required=True)}
        school = p.ask("School (for a child; - for none)")
        if school:
            member["school"] = school
        notes = p.ask("Anything Ataria should know (- for nothing)")
        if notes:
            member["notes"] = notes
        family.append(member)
    personal["family"] = family
    personal["interests"] = p.items("Interests to follow", personal.get("interests", ()),
                                    why="topics for the growth and reading part of the digest")
    p.heading("Personal sources")
    personal["sources"] = _sources(p, PERSONAL_SOURCES, personal.get("sources"), packs=True)
    p.heading("Money")
    finance = dict(personal.get("finance") or {})
    finance["enabled"] = p.yes("Should Ataria track your bank statements, bills and budget?", finance.get("enabled", False),
                               why="turns on the finance and ledger jobs")
    if finance["enabled"]:
        finance["currency"] = p.ask("Currency (3 letters)", finance.get("currency", "INR" if owner["timezone"] == "Asia/Kolkata" else "USD"),
                                    check=lambda v: None if len(v) == 3 and v.isalpha() and v.isupper() else "Use a code like INR or SGD.")
        finance["banks"] = p.items("Banks and cards", finance.get("banks", ()), why="to recognise statements and alerts")
        finance["statements_folder"] = p.ask("Folder where statements are saved", finance.get("statements_folder"),
                                             why="the finance job reads PDFs from here")
    personal["finance"] = {k: v for k, v in finance.items() if v not in ("", None, [])} | {"enabled": finance["enabled"]}
    personal["context"] = _context(p, personal.get("context"), (
        ("family", "Family routines worth knowing", "for example which school mails are todos and which are only information"),
        ("personal", "How should personal items be judged", "what is urgent for you outside work"),
        ("style", "How should summaries be written", "applies to every job"),
    ))
    out["personal"] = {k: v for k, v in personal.items() if v not in ("", None, [], {})}

    # --- C. Work -------------------------------------------------------------------------------
    p.part("C. Work", "Read by the work, meetings and interviews jobs. Skip it if Ataria should not follow work.")
    work = dict(cur.get("work") or {})
    work_on = p.yes("Should Ataria follow your work?", work.get("enabled", True) if work else first)
    if work_on:
        work["enabled"] = True
        work["company"] = p.ask("Company", work.get("company"))
        work["role"] = p.ask("Your role at work", work.get("role"), why="decides what counts as yours to act on")
        work["email"] = p.ask("Work email", work.get("email"), why="tells your own mail apart from mail you are copied on")
        work["clients"] = p.items("Clients", work.get("clients", ()), why="client matters rank first")
        work["products"] = p.items("Products", work.get("products", ()), why="for the product digest")
        work["team"] = p.items("People on your team", work.get("team", ()), why="who is blocked on you, and who owns what")
        p.heading("Work sources")
        work["sources"] = _sources(p, WORK_SOURCES, work.get("sources"))
        if work["sources"].get("github", {}).get("enabled"):
            work["github_orgs"] = p.items("GitHub organisations", work.get("github_orgs", ()))
        work["context"] = _context(p, work.get("context"), (
            ("work", "How should work items be judged", "what is urgent, what is only information"),
            ("alert_feeds", "Automated alert feeds to treat as information", "bot channels, monitoring, CI result mails"),
        ))
        out["work"] = {k: v for k, v in work.items() if v not in ("", None, [], {})}
    else:
        out["work"] = {"enabled": False}

    # --- D. Jobs and how Ataria runs ------------------------------------------------------------
    p.part("D. Jobs and how Ataria runs",
           "Each job is one agent run. Choose which agent runs it and which model; Enter keeps the suggestion.\n"
           + "\n".join(f"  {name}: {text}" for name, text in AGENTS.items()))
    areas = {"always": True, "work": work_on, "personal": True, "finance": out["personal"]["finance"]["enabled"]}
    jobs = dict(cur.get("jobs") or {})
    for name, area, what, agent, model, search in JOBS:
        entry = dict(jobs.get(name) or {})
        if not areas[area]:
            jobs[name] = {**entry, "enabled": False, "agent": entry.get("agent", agent)}
            continue
        p.heading(f"Job: {name}", what)
        default_on = entry.get("enabled", name != "interviews")
        entry["enabled"] = p.yes(f"Run the {name} job?", default_on)
        entry["agent"] = p.choice("Agent", ("claude", "codex"), entry.get("agent", agent))
        if "model" in entry and entry["agent"] == (jobs.get(name) or {}).get("agent"):
            suggested = entry["model"] or ""           # an earlier answer, even "the agent's default", is kept
        else:
            suggested = model if entry["agent"] == agent else ""
        entry["model"] = p.ask("Model (- for the agent's own default)", suggested) or None
        if entry["agent"] == "codex":
            entry["web_search"] = p.yes("Let it search the web?", entry.get("web_search", search))
        else:
            entry.pop("web_search", None)
        jobs[name] = entry
    out["jobs"] = jobs

    p.heading("Schedule")
    schedule = dict(cur.get("schedule") or {})
    schedule["morning"] = p.ask("Morning run (HH:MM)", schedule.get("morning", "07:00"), required=True, check=valid_clock,
                                why="the full refresh")
    schedule["midday"] = p.ask("Midday update (HH:MM)", schedule.get("midday", "13:30"), required=True, check=valid_clock,
                               why="what changed since the morning")
    schedule["catch_up"] = p.yes("Run a missed step when the computer wakes up?", schedule.get("catch_up", True))
    out["schedule"] = schedule

    p.heading("Who gets the digests")
    delivery = dict(cur.get("delivery") or {})
    delivery["phone"] = p.yes("Show the board and digests in the Chintu phone app?", delivery.get("phone", True))
    recipients = list(delivery.get("recipients") or [])
    if recipients:
        p.say("  Current: " + ", ".join(f"{r['name']} ({r['channel']}: {', '.join(r.get('areas', []))})" for r in recipients))
        if not p.yes("Keep them?", True):
            recipients = []
    while p.yes("Add someone who gets a digest (yourself on WhatsApp, a partner for home items...)?", False):
        person = {"name": p.ask("Name", required=True),
                  "channel": p.choice("Channel", ("phone", "whatsapp", "email"))}
        address = p.ask("Number or email (- for none)")
        if address:
            person["address"] = address
        person["areas"] = [a for a in p.items(f"Which areas ({', '.join(AREAS)})", ("work", "personal")) if a in AREAS]
        person["digests"] = [d for d in p.items(f"Which digests ({', '.join(DIGESTS)})", ("morning",)) if d in DIGESTS]
        recipients.append(person)
    delivery["recipients"] = recipients
    out["delivery"] = delivery

    p.heading("Secrets", "where they are kept, never the secret itself")
    secrets = dict(cur.get("secrets") or {})
    if secrets:
        p.say("  Current: " + ", ".join(f"{k} in {v['store']}" for k, v in secrets.items()))
    while p.yes("Add a secret reference (for example statement passwords)?", False):
        name = p.ask("Name (letters, digits, - or _)", required=True)
        store = p.choice("Kept in", ("keychain", "secret-service", "file"))
        secrets[name] = {"store": store, "ref": p.ask("Reference (keychain item or file path)", required=True)}
    if secrets:
        out["secrets"] = secrets

    p.heading("Privacy")
    privacy = dict(cur.get("privacy") or {})
    if "deny_patterns_file" in privacy:          # an earlier answer, even "none", is kept
        default_deny = privacy["deny_patterns_file"] or ""
    else:
        suggested = Path("~/.config/chintu/ataria-deny.txt").expanduser()
        default_deny = "~/.config/chintu/ataria-deny.txt" if suggested.exists() else ""
    deny = p.ask("Local list of names and hosts that must never be shared (- for none)", default_deny,
                 why="checked before anything leaves this computer")
    privacy["deny_patterns_file"] = deny or None
    out["privacy"] = privacy
    for old in ("agents", "sources", "family", "context"):   # the pre-0.7.0 layout
        out.pop(old, None)
    return profile.validate(out)


def summary(value):
    owner, personal, work = value.get("owner", {}), value.get("personal", {}), value.get("work", {})
    lines = [f"  You: {owner.get('name')} · {owner.get('city', '-')} · {owner.get('timezone')}",
             f"  Vault: {value.get('vault')}"]
    on = [n for n, e in personal.get("sources", {}).items() if e.get("enabled")]
    lines.append(f"  Personal: {len(personal.get('family', []))} family · sources: {', '.join(on) or 'none'}"
                 + (" · finance on" if personal.get("finance", {}).get("enabled") else ""))
    if work.get("enabled"):
        on = [n for n, e in work.get("sources", {}).items() if e.get("enabled")]
        lines.append(f"  Work: {work.get('company', '-')} · {len(work.get('clients', []))} clients · sources: {', '.join(on) or 'none'}")
    else:
        lines.append("  Work: not followed")
    lines.append("  Jobs:")
    for name, *_ in JOBS:
        job = value.get("jobs", {}).get(name)
        if job and job.get("enabled"):
            extra = (f" · {job['model']}" if job.get("model") else "") + (" · web" if job.get("web_search") else "")
            lines.append(f"    {name:<11}{job['agent']}{extra}")
    s = value["schedule"]
    lines.append(f"  Runs: {s['morning']} and {s['midday']}" + (" · catch up after sleep" if s.get("catch_up") else ""))
    lines.append(f"  Digests to: {len(value['delivery'].get('recipients', []))} people"
                 + (" and the phone app" if value["delivery"].get("phone") else ""))
    return "\n".join(lines)


def init(path=None, ask=None, assume_yes=False):
    p = ask or Prompter()
    path = Path(path) if path else profile.default_path()
    current = None
    if path.exists():
        current = profile.load(path)
        p.say(f"Editing {path}. Enter keeps the answer in brackets; - clears it.")
    else:
        p.say(f"Setting up Ataria. Your answers are saved only in {path}, readable by you alone.\n"
              "Enter accepts the answer in brackets; - leaves it empty. Ctrl+C stops without saving.")
    value = interview(current, p)
    p.say("\nSummary\n" + summary(value))
    if not assume_yes and not p.yes(f"Save to {path}?", True):
        p.say("Nothing saved.")
        return None
    if path.exists():
        backup = path.with_name(path.name + time.strftime(".%Y%m%d-%H%M%S.bak"))
        backup.write_bytes(path.read_bytes())
        os.chmod(backup, 0o600)
    profile.save(value, path)
    p.say(f"Saved {path}. Check it any time with: chintu-desktop ataria doctor")
    return value


# --- doctor -------------------------------------------------------------------------------------

def enabled_jobs(value):
    return {name: job for name, job in (value.get("jobs") or {}).items() if job.get("enabled")}


def doctor(path=None, out=None, which=shutil.which, home=None):
    """Check the profile and what it depends on. Returns (exit code, [(level, check, detail)])."""
    out = out or sys.stdout
    home = Path(home or Path.home())
    path = Path(path) if path else profile.default_path()
    results = []

    def note(level, check, detail):
        results.append((level, check, detail))

    try:
        value = profile.load(path)
        note("ok", "profile", f"{path} is valid and private")
    except profile.ProfileError as error:
        note("fail", "profile", str(error))
        value = None
    if value is not None:
        missing = set()
        base = templates.defaults_dir()
        for template in sorted(base.rglob("*.md")):
            try:
                templates.render_file(template, value, base=base)
            except templates.TemplateError as error:
                missing.update(error.missing)
        if missing:
            note("fail", "templates", "the bundled prompts need: " + ", ".join(sorted(missing))
                 + " (run: chintu-desktop ataria init)")
        else:
            note("ok", "templates", "every bundled prompt can be filled from your profile")
        problem = valid_timezone(value["owner"].get("timezone", "UTC"))
        note("fail" if problem else "ok", "timezone", problem or value["owner"].get("timezone", "UTC"))
        vault = Path(value["vault"]).expanduser()
        if not vault.is_dir():
            note("fail", "vault", f"{vault} does not exist")
        elif not os.access(vault, os.W_OK):
            note("fail", "vault", f"{vault} is not writable by you")
        else:
            note("ok", "vault", str(vault))
        jobs = enabled_jobs(value)
        if not jobs:
            note("warn", "jobs", "no job is switched on; the board will be empty")
        for agent in sorted({job["agent"] for job in jobs.values()}):
            users = ", ".join(sorted(name for name, job in jobs.items() if job["agent"] == agent))
            if not which(agent):
                note("fail", agent, f"{agent} is not installed or not on PATH (used by: {users})")
                continue
            signed_in = (home / ".codex" / "auth.json").exists() if agent == "codex" else (home / ".claude").exists()
            note("ok" if signed_in else "warn", agent,
                 (f"installed and looks signed in (used by: {users})" if signed_in
                  else f"installed; sign in by running `{agent}` once (used by: {users})"))
        finance = (value.get("personal") or {}).get("finance") or {}
        if finance.get("enabled") and finance.get("statements_folder"):
            folder = Path(finance["statements_folder"]).expanduser()
            note("ok" if folder.is_dir() else "warn", "statements", str(folder) if folder.is_dir() else f"{folder} does not exist")
        deny = (value.get("privacy") or {}).get("deny_patterns_file")
        if deny:
            deny_path = Path(deny).expanduser()
            if not deny_path.is_file():
                note("warn", "deny list", f"{deny_path} does not exist")
            elif deny_path.stat().st_mode & 0o077:
                note("warn", "deny list", f"{deny_path} is readable by others; run: chmod 600 '{deny_path}'")
            else:
                note("ok", "deny list", str(deny_path))
        for name, secret in (value.get("secrets") or {}).items():
            if secret["store"] == "file":
                secret_path = Path(secret["ref"]).expanduser()
                parent = secret_path.parent
                if parent.is_dir() and not os.access(parent, os.X_OK):
                    # A folder you cannot enter: the file may belong to a separate service account on purpose.
                    note("ok", f"secret {name}", f"{secret_path} is in a folder only another account can open "
                                                 "(not checked)")
                    continue
                if not secret_path.is_file():
                    note("fail", f"secret {name}", f"{secret_path} does not exist")
                elif secret_path.stat().st_mode & 0o077:
                    note("fail", f"secret {name}", f"{secret_path} is readable by others; run: chmod 600 '{secret_path}'")
                else:
                    note("ok", f"secret {name}", str(secret_path))
            else:
                note("ok", f"secret {name}", f"kept in {secret['store']} (not read by this check)")
    width = max(len(check) for _, check, _ in results)
    for level, check, detail in results:
        mark = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}[level]
        print(f"{mark}  {check.ljust(width)}  {detail}", file=out)
    return (1 if any(level == "fail" for level, _, _ in results) else 0), results
