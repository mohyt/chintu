"""In-process stand-in for ``claude_agent_sdk``: no inference, subprocess, or network.

Behavior is scripted by the instruction text, mirroring the other fixtures:
normal text succeeds; ``[wait]`` blocks until interrupt; ``[fail]`` returns an
error result; ``[die]`` ends the stream without a result; ``[raise]`` raises a
SDK error mid-stream; ``[prompt]`` exercises the permission callback.
"""

import asyncio
from dataclasses import dataclass
import os
from typing import Any

SESSION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
clients: list = []


class ClaudeSDKError(Exception):
    pass


class CLIConnectionError(ClaudeSDKError):
    pass


class CLINotFoundError(CLIConnectionError):
    pass


class ClaudeAgentOptions:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.__dict__.update(kwargs)


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclass
class SystemMessage:
    subtype: str
    data: dict


@dataclass
class AssistantMessage:
    content: list
    model: str = "fake-model"
    parent_tool_use_id: str | None = None
    session_id: str | None = None


@dataclass
class UserMessage:
    content: Any
    parent_tool_use_id: str | None = None


@dataclass
class ResultMessage:
    subtype: str
    is_error: bool
    session_id: str
    result: str | None = None
    num_turns: int = 1
    duration_ms: int = 1
    duration_api_ms: int = 1
    terminal_reason: str | None = None


@dataclass
class PermissionResultDeny:
    message: str = ""
    behavior: str = "deny"
    interrupt: bool = False


class ClaudeSDKClient:
    def __init__(self, options: ClaudeAgentOptions | None = None, transport: Any = None) -> None:
        self.options = options or ClaudeAgentOptions()
        self.prompt: str | None = None
        self.connected = False
        self.disconnected = False
        self.interrupted = asyncio.Event()
        clients.append(self)

    async def connect(self, prompt: Any = None) -> None:
        cwd = self.options.kwargs.get("cwd")
        if cwd and not os.path.isdir(cwd):
            raise CLIConnectionError("Working directory does not exist")
        if self.options.kwargs.get("cli_path") and not os.path.isfile(self.options.kwargs["cli_path"]):
            raise CLINotFoundError("Claude Code not found")
        self.connected = True

    async def query(self, prompt: Any, session_id: str = "default") -> None:
        if not self.connected:
            raise CLIConnectionError("Not connected. Call connect() first.")
        self.prompt = prompt

    async def interrupt(self) -> None:
        if not self.connected:
            raise CLIConnectionError("Not connected. Call connect() first.")
        self.interrupted.set()

    async def disconnect(self) -> None:
        self.disconnected = True
        self.connected = False

    async def receive_response(self):
        session = self.options.kwargs.get("resume") or SESSION
        text = self.prompt or ""
        cwd = self.options.kwargs.get("cwd", "")
        if text == "[no-init]":
            yield ResultMessage("success", False, session, "Reply without an init message.")
            return
        yield SystemMessage("init", {"session_id": session, "model": "fake-model", "tools": ["Read"]})
        if text == "[die]":
            return
        if text == "[raise]":
            raise ClaudeSDKError("fake SDK failure; SENSITIVE DETAIL")
        yield AssistantMessage([
            ToolUseBlock("t1", "Read", {"file_path": os.path.join(cwd, "docs", "01-requirements.md")}),
            ToolUseBlock("t2", "Bash", {"command": "ls -la\n  --color"}),
            ToolUseBlock("t3", "mcp__github__get_issue", {"number": 1}),
        ], session_id=session)
        yield UserMessage([{"type": "tool_result", "tool_use_id": "t1", "content": "SECRET TOOL OUTPUT"}])
        yield AssistantMessage([TextBlock("Subagent text must not leak.")], parent_tool_use_id="t9")
        if text == "[prompt]":
            decision = await self.options.kwargs["can_use_tool"]("Edit", {"file_path": "x"}, None)
            assert isinstance(decision, PermissionResultDeny)
        if text == "[wait]":
            await self.interrupted.wait()
            yield ResultMessage("success", False, session, "Stopped.", terminal_reason="aborted_streaming")
            return
        if text == "[fail]":
            yield ResultMessage("error_during_execution", True, session, None)
            return
        reply = "Fake reply: " + text
        yield AssistantMessage([TextBlock("Looking at the workspace first.")], session_id=session)
        yield AssistantMessage([TextBlock(reply)], session_id=session)
        yield ResultMessage("success", False, session, reply)
