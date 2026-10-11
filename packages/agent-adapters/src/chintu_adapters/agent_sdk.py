"""Claude Agent SDK provider: the Claude Code tool loop under explicit Chintu profiles.

The SDK is imported lazily so the package works without it. No API key is ever
passed; the SDK launches the owner's ``claude`` executable and uses its stored
login. The default executable name ``claude`` is looked up on PATH and, failing
that, the Claude Code CLI the ``claude-agent-sdk`` package ships
(``claude_agent_sdk/_bundled/claude``), so a packaged Chintu works without a
separately installed CLI. Profiles map to SDK permission modes and tool lists;
there is no interactive approval path, so anything that would prompt is denied instead.

``inspect`` and ``develop`` register no bare ``allowed_tools`` entries: the SDK
auto-approves such entries before any callback, so confinement is enforced by the
PreToolUse hook in :mod:`chintu_adapters.confinement`, which decides every call.
``desktop`` is the owner's explicit opt-in to ``bypassPermissions``.
"""

import re
import asyncio
from contextlib import suppress
from dataclasses import dataclass
import importlib
import logging
from pathlib import Path
import shutil
import sys
from typing import AsyncIterator
import uuid

from .confinement import PROFILE_TOOLS, workspace_hook
from .types import AdapterCapabilities, AdapterEvent, TaskCommand, bounded_text, result


PROVIDER = "claude-agent"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EXECUTABLE = "claude"
READ_ONLY_TOOLS = PROFILE_TOOLS["inspect"]
DEVELOP_TOOLS = PROFILE_TOOLS["develop"]
CLAUDE_CODE_PRESET = {"type": "preset", "preset": "claude_code"}
log = logging.getLogger("chintu.agent")


@dataclass(frozen=True)
class ProfilePolicy:
    """SDK settings for one execution profile. ``tools=None`` keeps the full built-in set.

    ``confined`` profiles get the workspace PreToolUse hook and no bare allows;
    ``project_settings`` controls whether a workspace's own ``.claude`` settings load.
    """

    permission_mode: str
    tools: tuple[str, ...] | None
    allowed_tools: tuple[str, ...]
    confined: bool
    project_settings: bool


PROFILE_POLICIES = {
    # A cloned repository's .claude/settings.json is untrusted input for read-only inspection.
    "inspect": ProfilePolicy("dontAsk", READ_ONLY_TOOLS, (), confined=True, project_settings=False),
    "develop": ProfilePolicy("dontAsk", DEVELOP_TOOLS, (), confined=True, project_settings=True),
    "desktop": ProfilePolicy("bypassPermissions", None, (), confined=False, project_settings=True),
}
DEFAULT_PROFILES = ("inspect", "develop")


def _single_line(text: str) -> str:
    return " ".join(str(text).split())


def describe_tool_use(name: str, tool_input: dict, project: Path | None = None) -> str:
    """Short, bounded progress text for one tool call; host paths are shown relative to the project."""
    verbs = {"Bash": "Running", "Read": "Reading", "Edit": "Editing", "MultiEdit": "Editing",
             "Write": "Writing", "NotebookEdit": "Editing", "Glob": "Finding", "Grep": "Searching",
             "WebSearch": "Searching the web", "WebFetch": "Fetching", "Agent": "Delegating",
             "Task": "Delegating"}
    keys = ("command", "file_path", "notebook_path", "pattern", "url", "query", "description")
    detail = ""
    if isinstance(tool_input, dict):
        for key in keys:
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                detail = value
                break
    if detail and project is not None and detail.startswith(str(project)):
        detail = detail[len(str(project)):].lstrip("/") or "."
    label = verbs.get(name, "Using")
    text = f"{label}: {detail}" if detail and name in verbs else f"{label}: {name}"
    return bounded_text(_single_line(text), 400)


_URL = re.compile(r"https?://[^\s)\]>'\"]+")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_CODE_BLOCK = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.M)


def speakable(text: str) -> str:
    """Strip Markdown and URLs so text-to-speech reads prose, not punctuation."""
    text = _CODE_BLOCK.sub(" code omitted. ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("a link", text)
    text = _INLINE_CODE.sub(r"\1", text)
    for _ in range(2):
        text = _EMPHASIS.sub(r"\2", text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    return text.replace("|", " ").replace("~", "")


def speech_summary(text: str, limit: int = 600) -> str:
    """Keep spoken text short: cut at a sentence boundary when possible."""
    text = _single_line(speakable(text))
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if boundary > limit // 3:
        return cut[:boundary + 1]
    return cut.rstrip() + "…"


def final_result(summary: str, *, error: str | None = None, partial_effects: bool = False) -> dict:
    payload = result(summary, error=error, partial_effects=partial_effects)
    payload["speech_text"] = speech_summary(payload["summary"]) or payload["summary"]
    return payload


DEFAULT_PERSONA = (
    "You are Chintu, the owner's spoken personal assistant, reached by voice from their phone. "
    "Act on their behalf like a capable chief of staff: use the owner's profile, memory, skills and every connected "
    "tool or MCP connector (mail, calendar, chat, documents, task trackers, files) to answer and to get things done. "
    "Reply the way a person would say it out loud: lead with the answer in one to three short sentences, plain words, "
    "no Markdown, no lists, no URLs read aloud (say 'I've put the link in the reply'). Ask at most one short question "
    "when something is genuinely ambiguous; otherwise make the sensible choice and say what you did. "
    "Offer to go deeper only when there is clearly more."
)


class ClaudeAgentAdapter:
    provider = PROVIDER

    def __init__(self, executable: str = DEFAULT_EXECUTABLE, *, model: str = DEFAULT_MODEL,
                 profiles: tuple[str, ...] = DEFAULT_PROFILES, load_user_settings: bool = True,
                 max_turns: int | None = None, timeout_seconds: float = 1800, stop_seconds: float = 10,
                 max_progress_events: int = 150, max_output_events: int = 60, sdk=None,
                 persona: str | None = DEFAULT_PERSONA) -> None:
        self.executable = executable
        self.persona = persona
        self.model = model
        self.profiles = tuple(profiles)
        self.load_user_settings = load_user_settings
        self.max_turns = max_turns
        self.timeout_seconds = timeout_seconds
        self.stop_seconds = stop_seconds
        self.max_progress_events = max_progress_events
        self.max_output_events = max_output_events
        self._injected_sdk = sdk
        self._clients: dict[str, object] = {}
        self._active: set[str] = set()
        self._cancelled: set[str] = set()
        if "desktop" in self.profiles:
            log.warning("claude-agent desktop profile enabled: bypassPermissions, no workspace confinement")

    # -- availability -----------------------------------------------------

    def _sdk(self):
        if self._injected_sdk is not None:
            return self._injected_sdk
        return importlib.import_module("claude_agent_sdk")

    def bundled_executable(self) -> str | None:
        """The Claude Code CLI shipped inside the installed SDK package, if there is one."""
        try:
            sdk = self._sdk()
        except ImportError:
            return None
        location = getattr(sdk, "__file__", None)
        if not location:
            return None
        path = Path(location).parent / "_bundled" / ("claude.exe" if sys.platform == "win32" else "claude")
        return str(path) if path.is_file() else None

    def resolved_executable(self) -> str | None:
        """An explicit path must exist; a bare name is looked up on PATH. The default name
        ``claude`` additionally falls back to the SDK's bundled CLI."""
        path = Path(self.executable).expanduser()
        if path.is_absolute() or "/" in self.executable:
            return str(path) if path.is_file() else None
        found = shutil.which(self.executable)
        if found is None and self.executable == DEFAULT_EXECUTABLE:
            found = self.bundled_executable()
        return found

    def _unavailable_reason(self) -> str | None:
        unknown = [p for p in self.profiles if p not in PROFILE_POLICIES]
        if not self.profiles or unknown:
            return "Disabled: configured execution profiles must be a nonempty subset of inspect/develop/desktop."
        try:
            self._sdk()
        except ImportError:
            return "Disabled: the claude-agent-sdk Python package is not installed (pip install claude-agent-sdk)."
        if self.resolved_executable() is None:
            return (f"Disabled: the Claude Code executable '{self.executable}' was not found on PATH"
                    + (" and the claude-agent-sdk package ships no bundled CLI" if self.executable == DEFAULT_EXECUTABLE else "")
                    + "; install and log in with the claude CLI.")
        return None

    def capabilities(self) -> AdapterCapabilities:
        reason = self._unavailable_reason()
        ready = reason is None
        return AdapterCapabilities(PROVIDER, ready, self.profiles if ready else (), ready, ready,
                                   False, False, False, reason)

    # -- options ----------------------------------------------------------

    def build_options(self, command: TaskCommand, project: Path, sdk=None, notes: list[str] | None = None):
        """SDK options for one attempt. Raises ValueError for an unconfigured profile."""
        if command.execution_profile not in self.profiles or command.execution_profile not in PROFILE_POLICIES:
            raise ValueError("Execution profile is not enabled for the claude-agent runtime")
        policy = PROFILE_POLICIES[command.execution_profile]
        sdk = sdk or self._sdk()
        profile = command.execution_profile

        async def deny(tool_name, tool_input, context):
            # No interactive approvals exist on this path; a prompt becomes an explicit denial.
            if notes is not None and len(notes) < 20:
                notes.append(bounded_text(f"Denied without approval: {_single_line(str(tool_name))}", 400))
            return sdk.PermissionResultDeny(
                message=f"Chintu's {profile} profile does not authorize this action without an interactive approval.")

        sources = ["user"] if self.load_user_settings else []
        if policy.project_settings and self.load_user_settings:
            sources.append("project")
        options = dict(
            cwd=str(project), model=self.model, permission_mode=policy.permission_mode,
            tools=list(policy.tools) if policy.tools is not None else dict(CLAUDE_CODE_PRESET),
            allowed_tools=list(policy.allowed_tools),
            setting_sources=sources,
        )
        if policy.confined:
            # The hook is the single authority for every tool call; the callback only backs it up.
            options["hooks"] = {"PreToolUse": [sdk.HookMatcher(hooks=[workspace_hook(project, profile, notes)])]}
            options["can_use_tool"] = deny
        if self.persona:
            # Keep Claude Code's own preset (tools, safety, CLAUDE.md loading) and append the voice-assistant persona.
            options["system_prompt"] = {"type": "preset", "preset": "claude_code", "append": self.persona}
        if command.provider_session_id:
            options["resume"] = command.provider_session_id
        if self.max_turns is not None:
            options["max_turns"] = self.max_turns
        executable = self.resolved_executable()
        if executable:
            options["cli_path"] = executable
        return sdk.ClaudeAgentOptions(**options)

    # -- execution --------------------------------------------------------

    async def cancel(self, attempt_id: str) -> None:
        if attempt_id not in self._active:
            return
        self._cancelled.add(attempt_id)
        client = self._clients.get(attempt_id)
        if client is not None:
            with suppress(Exception):
                await asyncio.wait_for(client.interrupt(), self.stop_seconds)

    async def _disconnect(self, client) -> None:
        with suppress(Exception):
            await asyncio.wait_for(client.disconnect(), max(1, self.stop_seconds))

    async def run(self, command: TaskCommand) -> AsyncIterator[AdapterEvent]:
        caps = self.capabilities()
        if not caps.available or command.execution_profile not in caps.execution_profiles:
            yield AdapterEvent("failed", final_result("Provider or execution profile unavailable.", error="unsupported_capability"))
            return
        if command.provider_session_id:
            try:
                uuid.UUID(command.provider_session_id)
            except (ValueError, AttributeError, TypeError):
                yield AdapterEvent("failed", final_result("Invalid provider session identifier.", error="invalid_request"))
                return
        if not command.instruction or len(command.instruction) > 8000 or "\x00" in command.instruction:
            yield AdapterEvent("failed", final_result("Invalid instruction.", error="invalid_request"))
            return
        if command.attempt_id in self._active:
            yield AdapterEvent("failed", final_result("Attempt already running.", error="invalid_state"))
            return
        self._active.add(command.attempt_id)
        sdk = self._sdk()
        session = command.provider_session_id
        client = None
        started = False
        terminal = None
        final_text = ""
        progress_count = output_count = 0
        notes: list[str] = []
        effects_possible = command.execution_profile != "inspect"
        try:
            deadline = asyncio.get_running_loop().time() + self.timeout_seconds

            async def within(awaitable):
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                return await asyncio.wait_for(awaitable, remaining)

            project = Path(command.project_path).resolve(strict=True)
            if not project.is_dir():
                raise ValueError("Registered project directory unavailable.")
            options = self.build_options(command, project, sdk, notes)
            if command.attempt_id in self._cancelled:
                yield AdapterEvent("cancelled", final_result("Cancelled before provider launch."), session)
                return
            if command.execution_profile == "desktop":
                log.warning("task %s runs the desktop profile: bypassPermissions, no workspace confinement",
                            command.task_id[:8])
            client = sdk.ClaudeSDKClient(options=options)
            self._clients[command.attempt_id] = client
            await within(client.connect())
            if command.attempt_id in self._cancelled:
                await self._disconnect(client)
                yield AdapterEvent("cancelled", final_result("Cancelled before the instruction was sent."), session)
                return
            # The instruction travels over the SDK's stdin JSON stream, never as argv or shell text.
            await within(client.query(command.instruction))
            stream = client.receive_response()
            while True:
                try:
                    message = await within(anext(stream))
                except StopAsyncIteration:
                    break
                reported = getattr(message, "session_id", None)
                if isinstance(message, sdk.SystemMessage):
                    reported = message.data.get("session_id") if isinstance(message.data, dict) else None
                    if message.subtype != "init":
                        reported = None
                if isinstance(reported, str) and reported and not started:
                    session = reported
                    started = True
                    yield AdapterEvent("started", {}, session)
                if isinstance(message, sdk.ResultMessage):
                    terminal = message
                    break
                if not started:
                    continue
                pending = []
                if isinstance(message, sdk.AssistantMessage) and not message.parent_tool_use_id:
                    texts = []
                    for block in message.content:
                        if isinstance(block, sdk.TextBlock) and isinstance(block.text, str) and block.text.strip():
                            texts.append(block.text)
                        elif isinstance(block, sdk.ToolUseBlock):
                            pending.append(("progress", describe_tool_use(block.name, block.input, project)))
                    if texts:
                        final_text = "\n".join(texts)
                        pending.insert(0, ("output", bounded_text(final_text, 2000)))
                for note in notes:
                    pending.append(("progress", note))
                notes.clear()
                for kind, text in pending:
                    if kind == "output":
                        if output_count >= self.max_output_events:
                            continue
                        output_count += 1
                    else:
                        if progress_count >= self.max_progress_events:
                            continue
                        progress_count += 1
                    yield AdapterEvent(kind, {"text": text}, session)
            await self._disconnect(client)
            if command.attempt_id in self._cancelled:
                yield AdapterEvent("cancelled", final_result(
                    "Claude Agent execution stopped after cancellation.", partial_effects=effects_possible), session)
            elif terminal is None or not started:
                yield AdapterEvent("interrupted", final_result(
                    "Claude Agent session ended without a confirmed outcome.", error="process_lost",
                    partial_effects=effects_possible), session)
            elif terminal.is_error or terminal.subtype != "success":
                text = terminal.result if isinstance(terminal.result, str) and terminal.result.strip() else final_text
                summary = text or f"Claude Agent reported a failure ({terminal.subtype})."
                yield AdapterEvent("failed", final_result(summary, error="provider_failure",
                                                          partial_effects=effects_possible), session)
            else:
                text = terminal.result if isinstance(terminal.result, str) and terminal.result.strip() else final_text
                yield AdapterEvent("succeeded", final_result(text or "Claude Agent finished without a reply."), session)
        except (ValueError, KeyError, TypeError, AttributeError, OSError, RuntimeError,
                asyncio.TimeoutError, ConnectionError, sdk.ClaudeSDKError) as error:
            if client is not None:
                if isinstance(error, asyncio.TimeoutError):
                    with suppress(Exception):
                        await asyncio.wait_for(client.interrupt(), self.stop_seconds)
                await self._disconnect(client)
            if command.attempt_id in self._cancelled:
                yield AdapterEvent("cancelled", final_result(
                    "Claude Agent execution stopped after cancellation.", partial_effects=effects_possible and started), session)
            elif started:
                yield AdapterEvent("interrupted", final_result(
                    f"Claude Agent session was lost ({type(error).__name__}).", error="runtime_error",
                    partial_effects=effects_possible), session)
            else:
                yield AdapterEvent("failed", final_result(
                    f"Claude Agent could not start the session ({type(error).__name__}).", error="runtime_error"), session)
        finally:
            if client is not None:
                await self._disconnect(client)
            self._clients.pop(command.attempt_id, None)
            self._cancelled.discard(command.attempt_id)
            self._active.discard(command.attempt_id)
