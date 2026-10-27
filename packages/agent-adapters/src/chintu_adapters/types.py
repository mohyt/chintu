"""Version 1.0.0 of the local adapter boundary."""

from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass(frozen=True)
class AdapterCapabilities:
    provider: str
    available: bool
    execution_profiles: tuple[str, ...]
    continuation: bool
    cancellation: bool
    approvals: bool
    active_steering: bool
    session_recovery: bool
    reason: str | None = None


@dataclass(frozen=True)
class TaskCommand:
    task_id: str
    attempt_id: str
    project_path: str
    instruction: str
    execution_profile: str = "inspect"
    provider_session_id: str | None = None


@dataclass(frozen=True)
class AdapterEvent:
    kind: str
    payload: dict
    provider_session_id: str | None = None


class Adapter(Protocol):
    def capabilities(self) -> AdapterCapabilities: ...

    def run(self, command: TaskCommand) -> AsyncIterator[AdapterEvent]: ...

    async def cancel(self, attempt_id: str) -> None: ...

    # Optional: adapters without it report no tool catalog.
    async def tools(self, project_path: str, execution_profile: str) -> dict: ...


def bounded_text(text: str, limit: int = 4000) -> str:
    """Keep UTF-8 payload bytes bounded as well as character counts."""
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


# Claude Code's built-in tools, described for the owner rather than for the model.
BUILTIN_TOOLS = {
    "Read": "Read a file", "Glob": "Find files by name", "Grep": "Search inside files",
    "Edit": "Change part of a file", "MultiEdit": "Make several changes to one file",
    "Write": "Create or replace a file", "NotebookEdit": "Edit a Jupyter notebook",
    "Bash": "Run a shell command", "WebSearch": "Search the web", "WebFetch": "Fetch a web page",
    "Agent": "Hand part of the work to a subagent", "Task": "Hand part of the work to a subagent",
    "TodoWrite": "Keep a to-do list for the task", "Skill": "Run one of your skills",
}
MCP_STATUSES = ("connected", "pending", "failed", "needs-auth", "disabled")


def _entry_text(value, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return bounded_text(" ".join(value.split()), limit * 2)[:limit]


def builtin_tools(names) -> list[dict]:
    return [{"name": name, "kind": "builtin", "server": None, "description": BUILTIN_TOOLS.get(name)}
            for name in names if _entry_text(name, 200)]


def tool_catalog(source: str, tools=(), mcp_servers=(), skills=(), reason: str | None = None) -> dict:
    """The adapter half of the contract's ToolCatalog: bounded, deduplicated, and free of host paths."""
    seen, listed = set(), []
    for tool in tools:
        name = _entry_text(tool.get("name"), 200)
        if name is None or name in seen or len(listed) >= 1000:
            continue
        seen.add(name)
        listed.append({"name": name, "kind": "mcp" if tool.get("kind") == "mcp" else "builtin",
                       "server": _entry_text(tool.get("server"), 200),
                       "description": _entry_text(tool.get("description"), 500)})
    servers = []
    for server in list(mcp_servers)[:100]:
        name = _entry_text(server.get("name"), 200)
        if name is not None:
            status = server.get("status") if server.get("status") in MCP_STATUSES else "unknown"
            tools_count = server.get("tools")
            servers.append({"name": name, "status": status,
                            "tools": tools_count if type(tools_count) is int and tools_count >= 0 else 0})
    named = [{"name": _entry_text(skill.get("name"), 200), "description": _entry_text(skill.get("description"), 500)}
             for skill in skills]
    return {"source": source, "tools": listed, "mcp_servers": servers,
            "skills": [skill for skill in named if skill["name"]][:500],
            "reason": _entry_text(reason, 2000)}


def result(summary: str, *, error: str | None = None, partial_effects: bool = False) -> dict:
    summary = bounded_text(summary, 4000)[:2000]
    return {
        "summary": summary[:2000],
        "speech_text": summary[:2000],
        "artifact_ids": [],
        "partial_effects": ["Effects may have occurred before execution stopped; review the workspace."] if partial_effects else [],
        "error": None if error is None else {
            "code": error, "message": summary[:2000], "retryable": False, "details": {}
        },
    }
