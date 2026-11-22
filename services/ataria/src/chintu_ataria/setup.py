"""`chintu-desktop ataria init` and `doctor`: create or edit ataria.json by asking, then check it.

init asks for every part of the profile, section by section. Re-running it edits: each question
shows the current answer and Enter keeps it. Nothing is written until the owner confirms the
summary. Answers go only to the local profile (mode 0600); no question asks for a password or token,
which stay in the agents' own logins or the system's credential store (`secrets` holds references).
"""

import os
import shutil
import sys
import time
from pathlib import Path

from . import profile, templates

SOURCES = (
    ("feeds", "News and blog feeds (world, tech, AI and more), no login needed"),
    ("google", "Gmail and Google Calendar, through the agent's own Google connector"),
    ("github", "GitHub pull requests, reviews and CI for your organisations"),
    ("notes", "Voice notes you dictate to Chintu"),
    ("meetings", "Meeting invites and transcripts"),
)
FEED_PACKS = ("world", "regional", "tech", "ai", "business", "cloud", "security", "regulators")
LANES = ("work", "meetings", "personal", "finance", "notes")
CONTEXT = (
    ("work", "How should work items be judged? (what is urgent, what is only information)"),
    ("family", "Family routines worth knowing (school notices, activities)"),
    ("style", "How should summaries be written?"),
    ("alert_feeds", "Automated alert feeds to treat as information (bot channels, monitoring)"),
)
DIGESTS = ("morning", "midday", "evening", "month")


class Prompter:
    """Questions on a terminal. `answers` replaces input for tests and scripted installs."""

    def __init__(self, stdin=None, stdout=None, answers=None):
        self.stdin, self.stdout = stdin or sys.stdin, stdout or sys.stdout
        self.answers = list(answers) if answers is not None else None

    def say(self, text=""):
        print(text, file=self.stdout)

    def _read(self, prompt):
        if self.answers is not None:
            self.stdout.write(prompt)
            value = self.answers.pop(0) if self.answers else ""
            self.stdout.write(value + "\n")
            return value
        self.stdout.write(prompt)
        self.stdout.flush()
        line = self.stdin.readline()
        if not line:
            raise EOFError("no more input")
        return line.rstrip("\n")

    def ask(self, question, current=None, required=False, check=None):
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

    def yes(self, question, current=True):
        value = self._read(f"  {question} [{'Y/n' if current else 'y/N'}]: ").strip().lower()
        return current if not value else value.startswith("y")

    def items(self, question, current=()):
        value = self.ask(question + " (comma separated, - for none)", ", ".join(current))
        return [part.strip() for part in value.split(",") if part.strip()]

    def choice(self, question, options, current=None):
        return self.ask(f"{question} ({'/'.join(options)})", current or options[0],
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
    ok = len(parts) == 2 and all(p.isdigit() for p in parts) and int(parts[0]) < 24 and int(parts[1]) < 60 and len(parts[0]) == 2
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


def detected_agent():
    for agent in ("codex", "claude"):
        if shutil.which(agent):
            return agent
    return "claude"


def interview(current=None, ask=None):
    """Ask every section; returns the new profile (not yet saved). `current` supplies the defaults."""
    p = ask or Prompter()
    cur = current or {}
    out = {key: value for key, value in cur.items()}   # unknown keys are kept
    out["schema"] = 1

    owner = dict(cur.get("owner") or {})
    p.say("\n1. You")
    owner["name"] = p.ask("What should Ataria call you", owner.get("name"), required=True)
    for key, question in (("full_name", "Full name"), ("role", "Your role"), ("email", "Email (for recognising mail to you)"),
                          ("city", "City")):
        owner[key] = p.ask(question, owner.get(key))
    owner["timezone"] = p.ask("Timezone", owner.get("timezone") or detected_timezone(), required=True, check=valid_timezone)
    locale = (os.environ.get("LANG") or "en_GB").split(".")[0].replace("_", "-")
    owner["locale"] = p.ask("Language and region", owner.get("locale") or locale)
    out["owner"] = {k: v for k, v in owner.items() if v}

    p.say("\n2. Vault (the folder of Markdown notes Ataria writes into)")
    out["vault"] = p.ask("Vault folder", cur.get("vault") or detected_vault(), required=True)
    vault = Path(out["vault"]).expanduser()
    if not vault.exists() and p.yes(f"{vault} does not exist. Create it now?", True):
        vault.mkdir(parents=True, mode=0o700, exist_ok=True)

    p.say("\n3. Schedule")
    schedule = dict(cur.get("schedule") or {})
    schedule["morning"] = p.ask("Morning run (HH:MM)", schedule.get("morning", "07:00"), required=True, check=valid_clock)
    schedule["midday"] = p.ask("Midday update (HH:MM)", schedule.get("midday", "13:30"), required=True, check=valid_clock)
    schedule["catch_up"] = p.yes("Run a missed step when the computer wakes up?", schedule.get("catch_up", True))
    out["schedule"] = schedule

    p.say("\n4. Agents")
    agents = dict(cur.get("agents") or {})
    agents["default"] = p.choice("Agent for most jobs", ("codex", "claude"), agents.get("default") or detected_agent())
    lanes = {}
    for lane in LANES:
        chosen = p.choice(f"Agent for the {lane} job", ("codex", "claude"),
                          (agents.get("lanes") or {}).get(lane, agents["default"]))
        if chosen != agents["default"]:
            lanes[lane] = chosen
    agents["lanes"] = lanes
    out["agents"] = agents

    p.say("\n5. Work")
    work = dict(cur.get("work") or {})
    if p.yes("Should Ataria follow your work?", bool(work) or current is None):
        work["company"] = p.ask("Company", work.get("company"))
        work["role"] = p.ask("Role at work", work.get("role") or owner.get("role"))
        work["clients"] = p.items("Clients", work.get("clients", ()))
        work["products"] = p.items("Products", work.get("products", ()))
        work["team"] = p.items("People on your team", work.get("team", ()))
        work["github_orgs"] = p.items("GitHub organisations", work.get("github_orgs", ()))
        out["work"] = {k: v for k, v in work.items() if v}
    else:
        out.pop("work", None)

    p.say("\n6. Family")
    family = list(cur.get("family") or [])
    if family:
        p.say("  Current: " + ", ".join(f"{m.get('name')} ({m.get('relation')})" for m in family))
        if not p.yes("Keep this list?", True):
            family = []
    while p.yes("Add a family member?", not family and current is None):
        member = {"name": p.ask("Name", required=True), "relation": p.ask("Relation (partner, child, parent...)", required=True)}
        school = p.ask("School (for children; - for none)")
        if school:
            member["school"] = school
        notes = p.ask("Anything Ataria should know (- for nothing)")
        if notes:
            member["notes"] = notes
        family.append(member)
    if family:
        out["family"] = family
    else:
        out.pop("family", None)

    p.say("\n7. Sources")
    sources = dict(cur.get("sources") or {})
    for name, description in SOURCES:
        entry = dict(sources.get(name) or {})
        entry["enabled"] = p.yes(f"{description}?", entry.get("enabled", name in ("feeds", "notes")))
        if name == "feeds" and entry["enabled"]:
            entry["packs"] = [pack for pack in p.items(f"Feed packs ({', '.join(FEED_PACKS)})",
                                                       entry.get("packs", ("world", "tech", "ai")))]
        if name == "google" and entry["enabled"]:
            entry.setdefault("via", "codex-connector")
        sources[name] = entry
    out["sources"] = sources

    p.say("\n8. Delivery")
    delivery = dict(cur.get("delivery") or {})
    delivery["phone"] = p.yes("Show the board and digests in the Chintu phone app?", delivery.get("phone", True))
    recipients = list(delivery.get("recipients") or [])
    if recipients:
        p.say("  Current recipients: " + ", ".join(f"{r['name']} ({r['channel']})" for r in recipients))
        if not p.yes("Keep them?", True):
            recipients = []
    while p.yes("Add someone who gets a digest?", False):
        person = {"name": p.ask("Name", required=True),
                  "channel": p.choice("Channel", ("phone", "whatsapp", "email"))}
        address = p.ask("Address (number or email; - for none)")
        if address:
            person["address"] = address
        person["digests"] = [d for d in p.items(f"Which digests ({', '.join(DIGESTS)})", ("morning",)) if d in DIGESTS]
        recipients.append(person)
    delivery["recipients"] = recipients
    out["delivery"] = delivery

    p.say("\n9. Notes for the agents (one line each; - to clear)")
    context = dict(cur.get("context") or {})
    for key, question in CONTEXT:
        value = p.ask(question, context.get(key))
        if value:
            context[key] = value
        else:
            context.pop(key, None)
    out["context"] = context

    p.say("\n10. Secrets (where they are kept, never the secret itself)")
    secrets = dict(cur.get("secrets") or {})
    if secrets:
        p.say("  Current: " + ", ".join(f"{k} in {v['store']}" for k, v in secrets.items()))
    while p.yes("Add a secret reference (for example statement passwords)?", False):
        name = p.ask("Name (letters, digits, - or _)", required=True)
        store = p.choice("Kept in", ("keychain", "secret-service", "file"))
        secrets[name] = {"store": store, "ref": p.ask("Reference (keychain item or file path)", required=True)}
    if secrets:
        out["secrets"] = secrets

    p.say("\n11. Privacy")
    privacy = dict(cur.get("privacy") or {})
    if "deny_patterns_file" in privacy:          # an earlier answer, even "none", is kept
        default_deny = privacy["deny_patterns_file"] or ""
    else:
        suggested = Path("~/.config/chintu/ataria-deny.txt").expanduser()
        default_deny = "~/.config/chintu/ataria-deny.txt" if suggested.exists() else ""
    deny = p.ask("Local list of names and hosts that must never be shared (- for none)", default_deny)
    privacy["deny_patterns_file"] = deny or None
    out["privacy"] = privacy
    return profile.validate(out)


def summary(value):
    owner = value.get("owner", {})
    lines = [f"  You: {owner.get('name')} · {owner.get('timezone')}",
             f"  Vault: {value.get('vault')}",
             f"  Runs: {value['schedule']['morning']} and {value['schedule']['midday']}"
             + (" · catch up after sleep" if value['schedule'].get('catch_up') else ""),
             f"  Agents: {value['agents']['default']}" + "".join(f", {k}: {v}" for k, v in value['agents'].get('lanes', {}).items())]
    if value.get("work"):
        w = value["work"]
        lines.append(f"  Work: {w.get('company', '-')} · {len(w.get('clients', []))} clients · {len(w.get('products', []))} products")
    if value.get("family"):
        lines.append(f"  Family: {len(value['family'])} people")
    on = [name for name, entry in value.get("sources", {}).items() if entry.get("enabled")]
    lines.append("  Sources: " + (", ".join(on) or "none"))
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
              "Enter accepts the answer in brackets; - leaves it empty.")
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
        agents = {value.get("agents", {}).get("default", "claude")} | set(value.get("agents", {}).get("lanes", {}).values())
        for agent in sorted(agents):
            if not which(agent):
                note("fail", agent, f"{agent} is not installed or not on PATH")
                continue
            signed_in = (home / ".codex" / "auth.json").exists() if agent == "codex" else (home / ".claude").exists()
            note("ok" if signed_in else "warn", agent,
                 "installed and looks signed in" if signed_in else f"installed; sign in by running `{agent}` once")
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
                if not secret_path.is_file():
                    note("fail", f"secret {name}", f"{secret_path} does not exist")
                elif secret_path.stat().st_mode & 0o077:
                    note("fail", f"secret {name}", f"{secret_path} is readable by others; run: chmod 600 '{secret_path}'")
                else:
                    note("ok", f"secret {name}", str(secret_path))
            else:
                note("ok", f"secret {name}", f"kept in {secret['store']} (not read by this check)")
        if not any(entry.get("enabled") for entry in (value.get("sources") or {}).values()):
            note("warn", "sources", "no source is switched on; the board will be empty")
    width = max(len(check) for _, check, _ in results)
    for level, check, detail in results:
        mark = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}[level]
        print(f"{mark}  {check.ljust(width)}  {detail}", file=out)
    return (1 if any(level == "fail" for level, _, _ in results) else 0), results
