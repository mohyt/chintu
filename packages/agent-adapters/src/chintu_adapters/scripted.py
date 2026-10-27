"""Deterministic no-I/O fixture; instructions are never executed."""

import asyncio
import uuid
from typing import AsyncIterator

from .types import AdapterCapabilities, AdapterEvent, TaskCommand, bounded_text, result, tool_catalog


class ScriptedAdapter:
    def __init__(self) -> None:
        self._running: dict[str, asyncio.Event] = {}

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities("scripted", True, ("inspect", "develop"), True, True,
                                   False, False, False)

    async def run(self, command: TaskCommand) -> AsyncIterator[AdapterEvent]:
        if command.execution_profile not in self.capabilities().execution_profiles:
            yield AdapterEvent("failed", result("Unsupported execution profile.", error="unsupported_capability"))
            return
        if command.attempt_id in self._running:
            yield AdapterEvent("failed", result("Attempt already running.", error="invalid_state"))
            return
        signal = asyncio.Event()
        self._running[command.attempt_id] = signal
        session = command.provider_session_id or str(uuid.uuid5(uuid.NAMESPACE_URL, "chintu-scripted:" + command.task_id))
        try:
            yield AdapterEvent("started", {}, session)
            yield AdapterEvent("progress", {"text": "Running scripted fixture; no tools or filesystem access."}, session)
            if command.instruction.strip() == "[wait]":
                await signal.wait()
            else:
                await asyncio.sleep(0)
            if signal.is_set():
                yield AdapterEvent("cancelled", result("Scripted task cancelled; no effects occurred."), session)
            elif command.instruction.strip() == "[fail]":
                yield AdapterEvent("failed", result("Scripted fixture failure.", error="fixture_failure"), session)
            elif command.instruction.strip() == "[interrupt]":
                yield AdapterEvent("interrupted", result("Scripted fixture interrupted.", error="fixture_interruption"), session)
            else:
                summary = "Scripted reply: " + command.instruction
                yield AdapterEvent("output", {"text": bounded_text(summary[:2000])}, session)
                yield AdapterEvent("succeeded", result(summary), session)
        finally:
            self._running.pop(command.attempt_id, None)

    async def tools(self, project_path: str, execution_profile: str) -> dict:
        return tool_catalog("static", reason="The scripted fixture has no tools; it only echoes instructions.")

    async def cancel(self, attempt_id: str) -> None:
        signal = self._running.get(attempt_id)
        if signal is not None:
            signal.set()
