"""Working directories inside registered workspaces.

A conversation may narrow its agent to one directory of a registered workspace. The
phone only ever names a path relative to that workspace; everything here resolves
symlinks and ``..`` and refuses anything that lands outside the workspace root.
"""

import os
from pathlib import Path, PurePosixPath

from .errors import ApiError

MAX_ENTRIES = 500


def normalise(relative):
    """Canonical relative spelling: '' for the root, no '.', no trailing slash; refuses escapes."""
    if not isinstance(relative, str) or len(relative) > 1024 or "\x00" in relative or "\\" in relative:
        raise ApiError(422, "invalid_request", "Invalid working directory.")
    path = PurePosixPath(relative.strip())
    if path.is_absolute() or ".." in path.parts or relative.strip().startswith("~"):
        raise ApiError(422, "invalid_request", "The working directory must be a folder inside the workspace.")
    parts = [part for part in path.parts if part not in ("", ".")]
    return "/".join(parts)


def inside(root, candidate):
    root, candidate = Path(root), Path(candidate)
    return candidate == root or candidate.is_relative_to(root)


def overlaps(first, second):
    """Two workspace paths overlap when either contains the other (quarantine applies to both)."""
    return first == second or inside(first, second) or inside(second, first)


def resolve(root, relative):
    """(canonical relative path, absolute directory) for ``relative`` under the registered ``root``."""
    relative = normalise(relative)
    if root is None or str(root).startswith("project:"):
        if relative:
            raise ApiError(404, "not_found", "This workspace has no folders to choose from.")
        return "", root
    base = Path(root).resolve()
    try:
        target = (base / relative).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ApiError(404, "not_found", "Working directory not found.") from None
    if not inside(base, target):
        raise ApiError(422, "invalid_request", "The working directory must be a folder inside the workspace.")
    if not target.is_dir():
        raise ApiError(404, "not_found", "Working directory not found.")
    canonical = target.relative_to(base).as_posix()
    return ("" if canonical == "." else canonical), str(target)


def listing(root, relative, limit=MAX_ENTRIES):
    """Visible subdirectories of one folder, sorted by name; hidden folders and escaping links are left out."""
    relative, absolute = resolve(root, relative)
    if absolute is None or str(absolute).startswith("project:"):
        return {"path": "", "parent": None, "entries": [], "truncated": False}
    base = Path(root).resolve()
    entries = []
    try:
        with os.scandir(absolute) as found:
            for entry in found:
                if entry.name.startswith("."):
                    continue
                try:
                    if not entry.is_dir():
                        continue
                    target = Path(entry.path).resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                if inside(base, target) and target != base:
                    entries.append({"name": entry.name[:255], "path": target.relative_to(base).as_posix()})
    except OSError:
        raise ApiError(404, "not_found", "Working directory cannot be read.") from None
    entries.sort(key=lambda item: item["name"].casefold())
    parent = None if not relative else relative.rpartition("/")[0]
    return {"path": relative, "parent": parent, "entries": entries[:limit], "truncated": len(entries) > limit}
