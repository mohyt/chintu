"""The owner's Ataria profile, `ataria.json`: everything personal, read at run time, never committed.

Where it lives, first match wins:
  1. $ATARIA_PROFILE
  2. Chintu's data directory: ~/Library/Application Support/Chintu/ataria.json on macOS,
     $XDG_DATA_HOME/chintu/ataria.json (default ~/.local/share/chintu) elsewhere.

The file must not be readable by other users. Unknown keys are allowed and kept, so newer
profiles work with older code; the keys the schema knows are type-checked.
"""

import json
import os
import stat
import sys
from importlib import resources
from pathlib import Path

from jsonschema import Draft202012Validator

MAX_BYTES = 256 * 1024


class ProfileError(ValueError):
    pass


def default_path(env=None, platform=None, home=None):
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path(home or Path.home())
    if env.get("ATARIA_PROFILE"):
        return Path(env["ATARIA_PROFILE"]).expanduser()
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Chintu" / "ataria.json"
    data = Path(env["XDG_DATA_HOME"]).expanduser() if env.get("XDG_DATA_HOME") else home / ".local" / "share"
    return data / "chintu" / "ataria.json"


def schema():
    return json.loads(resources.files("chintu_ataria").joinpath("schema/profile.schema.json").read_text())


_VALIDATOR = None


def validate(value):
    """Type-check a profile; raises ProfileError naming every problem."""
    global _VALIDATOR
    if _VALIDATOR is None:
        _VALIDATOR = Draft202012Validator(schema())
    errors = sorted(_VALIDATOR.iter_errors(value), key=lambda e: list(e.absolute_path))
    if errors:
        lines = []
        for error in errors[:20]:
            where = ".".join(str(p) for p in error.absolute_path) or "(top level)"
            lines.append(f"{where}: {error.message}")
        raise ProfileError("ataria.json is not valid:\n  " + "\n  ".join(lines))
    return value


def load(path=None, check_mode=True):
    path = Path(path) if path else default_path()
    try:
        info = path.stat()
    except FileNotFoundError:
        raise ProfileError(f"No Ataria profile at {path}. Create one with `chintu ataria init`.") from None
    if check_mode and os.name == "posix" and info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ProfileError(f"{path} is readable by other users; run: chmod 600 '{path}'")
    if info.st_size > MAX_BYTES:
        raise ProfileError(f"{path} is larger than {MAX_BYTES // 1024} KiB")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ProfileError(f"{path} is not valid JSON: {error}") from None
    return validate(value)


def save(value, path=None):
    """Write a validated profile atomically with mode 0600."""
    validate(value)
    path = Path(path) if path else default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)
    return path
