"""Bounded JSONL subprocess integration. Real runtimes are disabled by default.

Evidence is an operator record of compatibility tests, not a sandbox switch.
The gateway must only supply evidence from a reviewed local configuration.
"""

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import signal
from typing import AsyncIterator
import uuid

from .types import AdapterCapabilities, AdapterEvent, TaskCommand, bounded_text, result


@dataclass(frozen=True)
class RuntimeEvidence:
    version: str
    execution_profiles: tuple[str, ...]
    auth_verified: bool = False
    protocol_verified: bool = False
    confinement_verified: bool = False
    cancellation_verified: bool = False
    continuation_verified: bool = False
    record: str = ""


class ProtocolError(Exception):
    pass


class LocalAdapter:
    provider = ""
    required_flags: tuple[str, ...] = ()

    def __init__(self, executable: str | None = None, *, evidence: RuntimeEvidence | None = None,
                 timeout_seconds: float = 600, stop_seconds: float = 2,
                 max_output_bytes: int = 2 * 1024 * 1024) -> None:
        self.executable = executable or self.provider
        self.evidence = evidence
        self.timeout_seconds = timeout_seconds
        self.stop_seconds = stop_seconds
        self.max_output_bytes = max_output_bytes
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()
        self._active: set[str] = set()

    def capabilities(self) -> AdapterCapabilities:
        evidence = self.evidence
        ready = bool(os.name == "posix" and shutil.which(self.executable) and evidence
                     and evidence.version and evidence.record
                     and evidence.execution_profiles
                     and set(evidence.execution_profiles) <= {"inspect", "develop"}
                     and evidence.auth_verified and evidence.protocol_verified
                     and evidence.confinement_verified and evidence.cancellation_verified)
        return AdapterCapabilities(
            self.provider, ready, evidence.execution_profiles if ready else (),
            bool(ready and evidence.continuation_verified), ready, False, False, False,
            None if ready else "Disabled: pinned runtime, authentication, protocol, confinement and cancellation evidence required.",
        )

    async def _capture_probe(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            self.executable, *args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
        )
        try:
            async with asyncio.timeout(5):
                output = await process.stdout.read(65537)
                if len(output) > 65536:
                    raise ProtocolError("Runtime probe output limit exceeded.")
                await process.wait()
                if process.returncode != 0:
                    raise ProtocolError("Runtime probe failed.")
                return output.decode("utf-8", errors="strict")
        finally:
            await self._stop(process)

    async def _validate_runtime(self) -> None:
        version = await self._capture_probe("--version")
        match = re.search(r"\b\d+\.\d+\.\d+\b", version)
        if match is None or match.group() != self.evidence.version:
            raise ProtocolError("Pinned runtime version mismatch.")
        help_text = await self._capture_probe(*self.help_args())
        if any(flag not in help_text for flag in self.required_flags):
            raise ProtocolError("Required runtime protocol flags unavailable.")
        if self.evidence.continuation_verified and self.provider == "codex":
            resume_help = await self._capture_probe("exec", "resume", "--help")
            if "SESSION_ID" not in resume_help or "--json" not in resume_help:
                raise ProtocolError("Required continuation protocol unavailable.")

    def help_args(self) -> tuple[str, ...]:
        return ("--help",)

    def argv(self, command: TaskCommand) -> list[str]:
        raise NotImplementedError

    def normalize(self, record: dict) -> tuple[list[AdapterEvent], str | None]:
        raise NotImplementedError

    async def _stop(self, process: asyncio.subprocess.Process) -> None:
        # A new POSIX process group prevents signalling unrelated gateway jobs.
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            await self._wait_for_exit(process, self.stop_seconds)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if not await self._wait_for_exit(process, max(1, self.stop_seconds)):
            raise RuntimeError("Provider process stop could not be confirmed.")

    @staticmethod
    async def _wait_for_exit(process: asyncio.subprocess.Process, timeout: float) -> bool:
        # process.wait() can remain blocked by full PIPE buffers when the event
        # consumer is paused. The child watcher reaps and sets returncode first.
        deadline = asyncio.get_running_loop().time() + timeout
        while process.returncode is None and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        return process.returncode is not None

    async def cancel(self, attempt_id: str) -> None:
        if attempt_id not in self._active:
            return
        self._cancelled.add(attempt_id)
        process = self._processes.get(attempt_id)
        if process is not None and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            # Reap and escalate here even when the stream consumer is paused.
            if not await self._wait_for_exit(process, self.stop_seconds):
                await self._stop(process)

    async def run(self, command: TaskCommand) -> AsyncIterator[AdapterEvent]:
        caps = self.capabilities()
        if not caps.available or command.execution_profile not in caps.execution_profiles:
            yield AdapterEvent("failed", result("Provider or execution profile unavailable.", error="unsupported_capability"))
            return
        if command.provider_session_id and not caps.continuation:
            yield AdapterEvent("failed", result("Continuation unavailable.", error="unsupported_capability"))
            return
        if command.provider_session_id:
            try:
                uuid.UUID(command.provider_session_id)
            except (ValueError, AttributeError):
                yield AdapterEvent("failed", result("Invalid provider session identifier.", error="invalid_request"))
                return
        if not command.instruction or len(command.instruction) > 8000 or "\x00" in command.instruction:
            yield AdapterEvent("failed", result("Invalid instruction.", error="invalid_request"))
            return
        if command.attempt_id in self._active:
            yield AdapterEvent("failed", result("Attempt already running.", error="invalid_state"))
            return
        self._active.add(command.attempt_id)
        process = None
        stderr_task = None
        session = command.provider_session_id
        terminal = None
        started = False
        summary = ""
        try:
            # A timeout context must never span a yield: it would cancel the
            # consumer while it persists an event or awaits another operation.
            deadline = asyncio.get_running_loop().time() + self.timeout_seconds

            async def within(awaitable):
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                return await asyncio.wait_for(awaitable, remaining)

            await within(self._validate_runtime())
            if command.attempt_id in self._cancelled:
                yield AdapterEvent("cancelled", result("Cancelled before provider launch."), session)
                return
            project = Path(command.project_path).resolve(strict=True)
            if not project.is_dir():
                raise ProtocolError("Registered project directory unavailable.")
            process = await within(asyncio.create_subprocess_exec(
                *self.argv(command), cwd=str(project),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=65536,
            ))
            self._processes[command.attempt_id] = process

            async def drain_stderr() -> int:
                count = 0
                while chunk := await process.stderr.read(8192):
                    count += len(chunk)
                    if count > self.max_output_bytes:
                        await self._stop(process)
                        return count
                return count

            stderr_task = asyncio.create_task(drain_stderr())
            # Prompt never appears in argv; leading '-' and shell syntax remain data.
            process.stdin.write(command.instruction.encode("utf-8"))
            await within(process.stdin.drain())
            process.stdin.close()
            total = 0
            while line := await within(process.stdout.readline()):
                total += len(line)
                if total > self.max_output_bytes:
                    raise ProtocolError("Provider output limit exceeded.")
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ProtocolError("Malformed provider record.")
                events, reported_session = self.normalize(record)
                if reported_session:
                    uuid.UUID(reported_session)
                    if session and session != reported_session:
                        raise ProtocolError("Provider session changed unexpectedly.")
                    session = reported_session
                for event in events:
                    if event.kind == "started":
                        if started or not session:
                            raise ProtocolError("Invalid provider start event.")
                        started = True
                    elif not started:
                        raise ProtocolError("Provider event before session start.")
                    if terminal is not None:
                        raise ProtocolError("Provider event after terminal result.")
                    if event.kind in ("succeeded", "failed"):
                        terminal = event
                    else:
                        if event.kind == "output":
                            summary = (summary + event.payload["text"])[-4000:]
                        yield AdapterEvent(event.kind, event.payload, session)
            await within(process.wait())
            stderr_count = await within(stderr_task)
            await self._stop(process)
            if command.attempt_id in self._cancelled:
                yield AdapterEvent("cancelled", result("Provider execution stopped after cancellation.", partial_effects=True), session)
            elif stderr_count > self.max_output_bytes:
                raise ProtocolError("Provider stderr output limit exceeded.")
            elif terminal is None or (process.returncode != 0 and terminal.kind == "succeeded"):
                yield AdapterEvent("interrupted", result("Provider process ended without a confirmed outcome.", error="process_lost", partial_effects=True), session)
            else:
                payload = terminal.payload or result(summary or "Provider finished.")
                yield AdapterEvent(terminal.kind, payload, session)
        except (ProtocolError, ValueError, KeyError, TypeError, AttributeError, RecursionError, UnicodeError, OSError,
                asyncio.TimeoutError, ConnectionError):
            if process is not None:
                await self._stop(process)
            if command.attempt_id in self._cancelled:
                yield AdapterEvent("cancelled", result("Provider execution stopped after cancellation.", partial_effects=process is not None), session)
            else:
                yield AdapterEvent("interrupted" if process else "failed",
                                   result("Provider compatibility, protocol or execution check failed.", error="runtime_error", partial_effects=process is not None), session)
        finally:
            if process is not None:
                await self._stop(process)
            if stderr_task is not None:
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
            self._processes.pop(command.attempt_id, None)
            self._cancelled.discard(command.attempt_id)
            self._active.discard(command.attempt_id)


class CodexAdapter(LocalAdapter):
    provider = "codex"
    required_flags = ("--json", "--sandbox", "--ignore-user-config", "--ignore-rules")

    def help_args(self) -> tuple[str, ...]:
        return ("exec", "--help")

    def argv(self, command: TaskCommand) -> list[str]:
        sandbox = "read-only" if command.execution_profile == "inspect" else "workspace-write"
        args = [self.executable, "exec", "-c", 'approval_policy="never"',
                "-c", 'sandbox_mode="' + sandbox + '"', "--ignore-user-config", "--ignore-rules", "--json"]
        if command.provider_session_id:
            args += ["resume", command.provider_session_id, "-"]
        else:
            args += ["--sandbox", sandbox, "-"]
        return args

    def normalize(self, record: dict) -> tuple[list[AdapterEvent], str | None]:
        kind = record.get("type")
        if kind == "thread.started":
            return [AdapterEvent("started", {})], record["thread_id"]
        if kind == "item.completed":
            item = record["item"]
            if item.get("type") == "agent_message":
                text = item["text"]
                if not isinstance(text, str):
                    raise ProtocolError("Invalid agent text.")
                return [AdapterEvent("output", {"text": bounded_text(text)})], None
            if item.get("type") != "reasoning":
                return [AdapterEvent("progress", {"text": "Provider completed an operation."})], None
        if kind == "turn.completed":
            return [AdapterEvent("succeeded", {})], None
        if kind == "turn.failed":
            return [AdapterEvent("failed", result("Provider reported task failure.", error="provider_failure", partial_effects=True))], None
        if kind == "error":
            raise ProtocolError("Provider protocol error.")
        return [], None


class ClaudeAdapter(LocalAdapter):
    provider = "claude"
    required_flags = ("--output-format", "--restricted", "--permission-prompts", "--safe-mode", "--resume", "--tools")

    def argv(self, command: TaskCommand) -> list[str]:
        tools = "Read,Glob,Grep" if command.execution_profile == "inspect" else "Read,Glob,Grep,Edit,Write,Bash"
        args = [self.executable, "--print", "--output-format", "stream-json", "--verbose",
                "--restricted", "--safe-mode", "--permission-prompts", "none",
                "--permission-mode", "dontAsk", "--tools", tools, "--disallowedTools", "mcp__*"]
        if command.provider_session_id:
            args += ["--resume", command.provider_session_id]
        return args

    def normalize(self, record: dict) -> tuple[list[AdapterEvent], str | None]:
        kind = record.get("type")
        if kind == "system" and record.get("subtype") == "init":
            return [AdapterEvent("started", {})], record["session_id"]
        if kind == "system" and record.get("subtype") == "permission_denied":
            raise ProtocolError("Provider requires unsupported approval.")
        if kind == "assistant" and not record.get("parent_tool_use_id"):
            events = []
            for block in record["message"]["content"]:
                if block.get("type") == "text":
                    if not isinstance(block.get("text"), str):
                        raise ProtocolError("Invalid assistant text.")
                    events.append(AdapterEvent("output", {"text": bounded_text(block["text"])}))
            return events, None
        if kind == "result":
            if record.get("permission_denials"):
                return [AdapterEvent("failed", result("Provider requires unsupported approval.", error="unsupported_capability", partial_effects=True))], record.get("session_id")
            if record.get("is_error") is True:
                return [AdapterEvent("failed", result("Provider reported task failure.", error="provider_failure", partial_effects=True))], record.get("session_id")
            if record.get("subtype") != "success" or record.get("is_error") is not False or not isinstance(record.get("result"), str):
                raise ProtocolError("Malformed provider result.")
            return [AdapterEvent("succeeded", result(record["result"]))], record.get("session_id")
        return [], None
