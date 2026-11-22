"""Where Ataria's files live, and reading and writing its OKF Markdown concepts (docs/12-okf-notes.md).

A concept file is YAML frontmatter between `---` lines, then a Markdown body. `index.md` and
`log.md` are reserved; dot-folders (.git, .obsidian) are ignored everywhere.

Files are named by *logical* paths such as `work/2026/11/22/items/x.md`, so the dashboard, run
reports and prompts keep one name for a file wherever it is stored:

- content (items, todos, extracts, summaries, the board, done ticks) lives in the owner's vault,
  in each section's `Ataria/` folder (`VAULT_HOMES`);
- operational files (run reports, logs, locks, the outbox, flow records) stay in the engine's
  state folder, never in the vault.

With `"engine": {"layout": "data"}` in the profile everything stays in the state folder, as the
prototype did before its vault move.
"""

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

LANES = ["work", "meetings", "finance", "ledger", "personal", "notes"]   # item lanes, in merge rank order
PRIORITIES = ["P0", "P1", "P2", "P3", "OK"]
RESERVED = {"index.md", "log.md"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,160}$")

VAULT_HOMES = {
    "work": "20-Work/Ataria/work",
    "meetings": "20-Work/Ataria/meetings",
    "finance": "40-Finance/Ataria/finance",
    "ledger": "40-Finance/Ataria/ledger",
    "personal": "70-Home/Ataria/personal",
    "notes": "80-Notes/Ataria/notes",
    "app": "10-Today/Ataria",
    "inbox/google/personal": "00-Inbox/Google/personal",
    "inbox/google/finance": "00-Inbox/Google/finance",
    "inbox/whatsapp": "00-Inbox/Messages/whatsapp",
    "inbox/notes": "00-Inbox/Messages/chintu",
}
OPERATIONAL = re.compile(r"(^|/)(\.git|\.gitignore|\.logs|\.tmp|\.run-cache|\.lock[^/]*|\.last-ok[^/]*|"
                         r"run-result\.json|run-[^/]*\.md)$")

# The OKF profile's types and the older names readers still accept.
TYPES = {
    "Item", "Todo", "Extract", "Note", "Board", "Done", "Monthly Summary", "State", "Reference",
    "Spend Event", "Run", "Run Notes", "Flow Run", "Message", "Daily Note", "Todo List", "Meeting",
    "Transcript", "Client", "Client Note", "Source Capture", "Interview", "Interview Problem",
    "Lesson", "Product", "Product Update", "Product Digest", "Project", "Person", "Decision",
    "Reference Note", "One on One", "Section", "Home",
}
TYPE_ALIASES = {
    "Ataria Item": "Item", "Ataria Todo": "Todo", "Ataria Extract": "Extract", "Chintu Note": "Note",
    "capture": "Note", "inbox": "Note", "Ataria Today": "Board", "Ataria Done": "Done",
    "Ataria Monthly Summary": "Monthly Summary", "Ataria State": "State",
    "Ataria Reference": "Reference", "Ataria Computation Input": "Reference", "Ataria Run": "Run",
    "Ataria Run Notes": "Run Notes", "Ataria Source Review": "Run Notes", "Ataria Review": "Run Notes",
    "Ataria Sources": "Run Notes", "Ataria Midday Check": "Run Notes", "Ataria Source Check": "Run Notes",
    "Ataria Midday Review": "Run Notes", "Ataria Flow Run": "Flow Run", "Ataria Message": "Message",
    "daily": "Daily Note", "todo": "Todo List", "meeting": "Meeting", "transcript": "Transcript",
    "client": "Client", "client-daily": "Client Note", "source-capture": "Source Capture",
    "interview": "Interview", "interview-problem": "Interview Problem", "lesson": "Lesson",
    "product": "Product", "product-update": "Product Update", "product-digest": "Product Digest",
    "project": "Project", "person": "Person", "decision": "Decision", "reference": "Reference Note",
    "1on1": "One on One", "section": "Section", "home": "Home",
}


def canonical_type(value):
    """The profile name for a type, accepting older names; None for a type not in the profile."""
    value = str(value or "").strip()
    return value if value in TYPES else TYPE_ALIASES.get(value)


class Vault:
    """Paths and time for one owner, from the profile."""

    def __init__(self, profile, profile_path=None):
        engine = profile.get("engine") or {}
        self.tz = ZoneInfo((profile.get("owner") or {}).get("timezone") or "UTC")
        self.home = Path(profile["vault"]).expanduser()
        default_state = (Path(profile_path).parent if profile_path else Path.home() / ".local/share/chintu") / "ataria"
        self.data = Path(engine.get("state_dir") or default_state).expanduser()
        self.layout = engine.get("layout", "vault")

    # --- time ---
    def now(self):
        return datetime.now(self.tz).replace(microsecond=0)

    def iso(self, value):
        return value.astimezone(self.tz).replace(microsecond=0).isoformat() if value else None

    def parse_dt(self, value):
        """A datetime from an ISO string, date or datetime; naive values are in the owner's timezone."""
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=self.tz)
        if hasattr(value, "year") and not isinstance(value, str):
            return datetime(value.year, value.month, value.day, tzinfo=self.tz)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=self.tz)

    # --- paths ---
    def _home(self, logical):
        if self.layout != "vault":
            return None
        logical = str(logical).strip("/")
        for prefix in sorted(VAULT_HOMES, key=len, reverse=True):
            if logical == prefix or logical.startswith(prefix + "/"):
                return prefix, self.home / VAULT_HOMES[prefix]
        return None

    def path(self, logical):
        """The real folder or file for a logical path. Run reports always stay in the state folder."""
        logical = str(logical).strip("/")
        found = self._home(logical)
        if found is None or OPERATIONAL.search(logical):
            return self.data / logical
        prefix, base = found
        return base / logical[len(prefix):].lstrip("/") if logical != prefix else base

    def logical(self, real):
        """The logical path of a real file: the inverse of path()."""
        real = Path(real).resolve()
        if self.layout == "vault":
            for prefix, rel in sorted(VAULT_HOMES.items(), key=lambda kv: len(kv[1]), reverse=True):
                base = (self.home / rel).resolve()
                if real == base or base in real.parents:
                    return (prefix + "/" + str(real.relative_to(base))).rstrip("/.") if real != base else prefix
        return str(real.relative_to(self.data.resolve()))

    def resolve(self, rel):
        """A logical path back to a real file: the vault copy when it exists, else the state folder."""
        found = self.path(rel)
        return found if found.exists() else self.data / str(rel).strip("/")


def read(path):
    """(frontmatter, body). Raises ValueError when the frontmatter is missing or not a mapping."""
    text = Path(path).read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError("no frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError("frontmatter not closed")
    front = yaml.safe_load(text[3:end]) or {}
    if not isinstance(front, dict):
        raise ValueError("frontmatter is not a mapping")
    return front, text[end + 4:].lstrip("\n")


def dump(front, body=""):
    head = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000)
    return f"---\n{head}---\n\n{body.strip()}\n" if body.strip() else f"---\n{head}---\n"


def write(path, front, body=""):
    """Atomic write, so readers (the dashboard, Obsidian) never see half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(dump(front, body))
    os.chmod(temporary, 0o640)
    os.replace(temporary, path)


def month_dirs(lane_dir):
    """[(YYYY, MM, Path)], oldest first."""
    out = []
    for year in sorted(Path(lane_dir).glob("[0-9][0-9][0-9][0-9]")):
        for month in sorted(year.glob("[0-1][0-9]")):
            out.append((year.name, month.name, month))
    return out


def day_dir(base, when):
    return Path(base) / when.strftime("%Y/%m/%d")
