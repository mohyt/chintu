"""Find personal information that should be a profile value, not repository text.

Built-in patterns catch email addresses, phone numbers, home folder paths and tailnet host names.
An optional deny list adds the owner's own names, hosts and account numbers. That list is a
local file (one entry per line, `#` comments), never committed. Entries match whole words,
ignoring case.
"""

import re
from dataclasses import dataclass
from pathlib import Path

ALLOWED_DOMAINS = ("example.com", "example.org", "example.net")
PATTERNS = {
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)"),
    "phone number": re.compile(r"\+\d[\d ()-]{7,}\d|(?<![\d-])\d{10}(?![\d-])|(?<!\d)\d{5} \d{5}(?!\d)"),
    "home folder path": re.compile(r"/home/[a-z_][a-z0-9_-]*|/Users/[A-Za-z][A-Za-z0-9_.-]*|[A-Za-z]:\\Users\\[A-Za-z]"),
    "tailnet host name": re.compile(r"\b[a-z0-9-]+\.[a-z0-9-]+\.ts\.net\b"),
}
ALLOWED_HOSTS = {"machine.tailnet.ts.net"}
TEXT_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".py", ".txt", ".toml", ".html", ".js", ".css", ".sh", ".env", ""}


@dataclass(frozen=True)
class Leak:
    path: str
    line: int
    kind: str
    text: str


def deny_list(path):
    if not path:
        return []
    entries = []
    for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


def scan_text(text, path="<text>", deny=()):
    found = []
    deny_res = [(entry, re.compile(r"(?<![\w])" + re.escape(entry) + r"(?![\w])", re.I)) for entry in deny]
    for number, line in enumerate(text.splitlines(), 1):
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                if kind == "email address" and match.group(1).lower().endswith(ALLOWED_DOMAINS):
                    continue
                if kind == "tailnet host name" and match.group(0) in ALLOWED_HOSTS:
                    continue
                found.append(Leak(path, number, kind, match.group(0)))
        for entry, pattern in deny_res:
            if pattern.search(line):
                # The finding names the deny-list line number only, so CI logs never echo the entry.
                found.append(Leak(path, number, "deny-list entry", f"entry #{deny.index(entry) + 1}"))
    return found


def scan_tree(root, deny=(), skip=()):
    root = Path(root)
    found = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        if path.suffix not in TEXT_SUFFIXES or any(str(rel).startswith(s) for s in skip):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found += scan_text(text, str(rel), deny)
    return found
