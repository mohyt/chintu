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


def bounded_text(text: str, limit: int = 4000) -> str:
    """Keep UTF-8 payload bytes bounded as well as character counts."""
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


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
