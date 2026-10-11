"""Workspace confinement for the Claude Agent SDK profiles, enforced by a PreToolUse hook.

The SDK evaluates hooks before deny rules, permission modes, ``allowed_tools`` and
``can_use_tool``; a hook ``deny`` is final in every mode and a hook ``allow`` skips the
mode and allow-rule steps. Because bare ``allowed_tools`` entries auto-approve a whole
tool before any callback runs, the ``inspect`` and ``develop`` profiles register no
bare allows at all: every tool call is decided here.

What the hook guarantees:

- File tools (Read, Glob, Grep, Edit, Write, MultiEdit, NotebookEdit) only touch paths
  that resolve, after following symlinks and ``..``, inside the registered workspace.
- Bash (``develop`` only) is refused when the command text contains an absolute path
  outside the workspace, ``~``, ``..``, ``$HOME``, ``sudo``/``su``/``doas``, a
  ``curl``/``wget`` piped into an interpreter, or a ``cd``/``pushd`` that leaves the
  workspace. Any tool the profile does not list is refused.

What it does not guarantee: a Bash command that passes the text checks can still reach
outside the workspace through symlinks inside it, environment variables other than
``HOME``, programs that resolve their own paths (``git -C``, package managers reading
``~/.config``), subshells that compute paths, or network access. The hook is a text
filter on the command, not a sandbox. Use ``inspect`` where that matters.
"""

import re
import shlex
from pathlib import Path

FILE_TOOLS = {
    "Read": ("file_path",), "Edit": ("file_path",), "Write": ("file_path",),
    "MultiEdit": ("file_path",), "NotebookEdit": ("notebook_path",),
    "Glob": ("path", "pattern"), "Grep": ("path",),
}
READ_TOOLS = ("Read", "Glob", "Grep")
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
PROFILE_TOOLS = {
    "inspect": READ_TOOLS,
    "develop": READ_TOOLS + WRITE_TOOLS + ("Bash",),
}

_HTTP_URL = re.compile(r"https?://[^\s'\"]*")
_ABSOLUTE = re.compile(r"(?:^|[\s=:'\"(<>|;&,])(/[^\s'\"<>|;&)]*)")
_PIPED_FETCH = re.compile(r"\b(?:curl|wget)\b.*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|dash|ksh|fish|python[0-9.]*|perl|ruby|node)\b", re.S)
_ESCALATION = {"sudo", "su", "doas"}
_HOME_VARIABLE = re.compile(r"\$\{?HOME\b")


def _inside(workspace: Path, candidate: Path) -> bool:
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved == workspace or resolved.is_relative_to(workspace)


def path_allowed(workspace: Path, value: str) -> bool:
    """True when ``value`` (absolute or workspace-relative) resolves inside the workspace."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return False
    if value.startswith("~"):
        return False
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    return _inside(workspace, candidate)


def file_call_allowed(workspace: Path, tool_name: str, tool_input: dict) -> bool:
    """Every path-like argument of a file tool must stay inside the workspace."""
    if not isinstance(tool_input, dict):
        return False
    for key in FILE_TOOLS[tool_name]:
        value = tool_input.get(key)
        if value is None and key in ("path", "pattern"):
            continue  # Glob/Grep default to the session cwd, which is the workspace.
        if key == "pattern" and isinstance(value, str) and not value.startswith(("/", "~")):
            if ".." in Path(value).parts:
                return False
            continue  # A relative glob pattern is anchored at the cwd.
        if not path_allowed(workspace, value):
            return False
    return True


def _segments(command: str) -> list[list[str]]:
    parts = []
    for segment in re.split(r"[;&|]+|\n", command):
        if segment.strip():
            parts.append(shlex.split(segment, posix=True))
    return parts


def bash_allowed(workspace: Path, command: str) -> bool:
    """Textual denylist for ``develop``; see the module docstring for its limits."""
    if not isinstance(command, str) or not command.strip() or "\x00" in command or len(command) > 20000:
        return False
    if "~" in command or ".." in command or _HOME_VARIABLE.search(command):
        return False
    if _PIPED_FETCH.search(command):
        return False
    stripped = _HTTP_URL.sub(" ", command)
    for match in _ABSOLUTE.finditer(stripped):
        if not _inside(workspace, Path(match.group(1))):
            return False
    try:
        segments = _segments(command)
    except ValueError:
        return False
    for tokens in segments:
        if any(token in _ESCALATION for token in tokens):
            return False
        for index, token in enumerate(tokens):
            if token in ("cd", "pushd"):
                arguments = [t for t in tokens[index + 1:index + 2] if not t.startswith("-")]
                if not arguments or not path_allowed(workspace, arguments[0]):
                    return False
    return True


def decide(workspace: Path, profile: str, tool_name: str, tool_input: dict) -> tuple[bool, str]:
    """(allowed, reason) for one tool call under ``profile``."""
    allowed_tools = PROFILE_TOOLS.get(profile, ())
    if tool_name not in allowed_tools:
        return False, f"Chintu's {profile} profile does not include this tool."
    if tool_name in FILE_TOOLS:
        if file_call_allowed(workspace, tool_name, tool_input):
            return True, "Inside the registered workspace."
        return False, f"Chintu's {profile} profile only allows files inside the registered workspace."
    if tool_name == "Bash":
        if isinstance(tool_input, dict) and bash_allowed(workspace, tool_input.get("command")):
            return True, "Command text stays inside the registered workspace."
        return False, (f"Chintu's {profile} profile refuses commands that reference paths outside the "
                       "workspace, the home directory, '..', sudo, or piped downloads.")
    return False, f"Chintu's {profile} profile does not include this tool."


def workspace_hook(workspace: Path, profile: str, notes: list[str] | None = None, note_limit: int = 20):
    """Build the PreToolUse callback; ``notes`` collects bounded denial messages for progress."""
    workspace = Path(workspace).resolve()

    async def pre_tool_use(input_data, tool_use_id, context):
        tool_name = str(input_data.get("tool_name", "")) if isinstance(input_data, dict) else ""
        tool_input = input_data.get("tool_input", {}) if isinstance(input_data, dict) else {}
        allowed, reason = decide(workspace, profile, tool_name, tool_input)
        if not allowed and notes is not None and len(notes) < note_limit:
            notes.append(f"Refused by the {profile} profile: {tool_name[:60] or 'tool'}")
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "allow" if allowed else "deny",
                                       "permissionDecisionReason": reason}}

    return pre_tool_use
