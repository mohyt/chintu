"""OKF v0.2 files, the on-disk format Chintu shares with Ataria. Standard library only.

A concept file is YAML frontmatter between `---` lines, then a Markdown body; a bundle root holds
`index.md` (with `okf_version`) and `log.md`. Values are written as JSON scalars, which are valid
YAML, so readers with a full YAML parser (Ataria uses PyYAML) load them unchanged.
"""

import json
import os
import tempfile
from pathlib import Path

OKF_VERSION = "0.2"
DATA_HOME_FILE = Path("~/.config/ataria/data.env")
NOTES_SECTION = Path("00-Inbox") / "Notes"


def data_home(environ=None, config=None):
    """The machine's shared OKF data folder (the Ataria vault copy), or None when it is not set.

    $DATA_HOME wins; otherwise a `DATA_HOME=<path>` line in ~/.config/ataria/data.env, which
    `ataria-sync join` writes, so daemons started without a login shell see it too.
    """
    environ = os.environ if environ is None else environ
    value = environ.get("DATA_HOME", "").strip()
    if not value:
        try:
            for line in Path(config or DATA_HOME_FILE).expanduser().read_text().splitlines():
                key, _, rest = line.strip().partition("=")
                if key == "DATA_HOME":
                    value = rest.strip().strip("'\"")
        except OSError:
            pass
    return Path(value).expanduser().resolve() if value else None


def notes_folder(configured, database, environ=None, config=None):
    """Where voice notes go: the configured notes_dir, else DATA_HOME's notes inbox, else beside the database."""
    if configured:
        return Path(configured)
    home = data_home(environ, config)
    return home / NOTES_SECTION if home else Path(database).resolve().parent / "notes"


def _scalar(value):
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def dump(frontmatter, body=""):
    """Frontmatter of scalars, flat mappings and lists of scalars or flat mappings."""
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            lines += [f"  {k}: {_scalar(v)}" for k, v in value.items()]
        elif isinstance(value, (list, tuple)):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            for entry in value:
                if isinstance(entry, dict):
                    pairs = [f"{k}: {_scalar(v)}" for k, v in entry.items()]
                    lines.append("  - " + pairs[0])
                    lines += ["    " + p for p in pairs[1:]]
                else:
                    lines.append("  - " + _scalar(entry))
        else:
            lines.append(f"{key}: {_scalar(value)}")
    lines.append("---")
    head = "\n".join(lines) + "\n"
    return head + "\n" + body.strip() + "\n" if body.strip() else head


def write(path, frontmatter, body="", mode=0o600):
    """Atomic write so readers (Ataria, Obsidian, the agent) never see a half file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(dump(frontmatter, body))
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def write_in(folder, name, frontmatter, body="", mode=0o600):
    """write() a file directly inside folder; refuses a name that would leave it (`..`, `/`)."""
    base = os.path.realpath(folder)
    path = os.path.realpath(os.path.join(base, name))
    if not path.startswith(base + os.sep) or os.path.dirname(path) != base:
        raise ValueError("note file name must stay inside the notes folder")
    write(path, frontmatter, body, mode)


def ensure_bundle(folder, title, description):
    """Create the bundle's `index.md` and `log.md` once; never rewrite them."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    index = folder / "index.md"
    if not index.exists():
        index.write_text(f'---\nokf_version: "{OKF_VERSION}"\n---\n\n# {title}\n\n{description}\n', encoding="utf-8")
    log = folder / "log.md"
    if not log.exists():
        log.write_text(f"# {title} log\n", encoding="utf-8")
