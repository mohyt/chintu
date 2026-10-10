import asyncio
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import unittest

from chintu_adapters import (
    AdapterCapabilities, AdapterEvent, ClaudeAdapter, CodexAdapter,
    RuntimeEvidence, ScriptedAdapter, TaskCommand,
)

SESSION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EVIDENCE = RuntimeEvidence("1.2.3", ("inspect", "develop"), True, True, True, True, True,
                           "fixture subprocess test; not real-provider evidence")


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.write_text(Path(__file__).with_name("fixture_runtime.py").read_text())
        self.runtime.chmod(0o700)

    def command(self, instruction="hello", **kwargs):
        return TaskCommand("task", "attempt", str(self.root), instruction, **kwargs)

    def adapter(self, cls=CodexAdapter, **kwargs):
        return cls(str(self.runtime), evidence=EVIDENCE, stop_seconds=0.05, **kwargs)

    async def collect(self, adapter, command):
        return [event async for event in adapter.run(command)]

    def test_types_frozen(self):
        for value in (self.command(), AdapterEvent("started", {}), ScriptedAdapter().capabilities()):
            with self.assertRaises(FrozenInstanceError):
                value.kind = "changed"

    def test_real_providers_fail_closed(self):
        for cls in (ClaudeAdapter, CodexAdapter):
            caps = cls(str(self.runtime)).capabilities()
            self.assertFalse(caps.available)
            self.assertEqual(caps.execution_profiles, ())
            self.assertFalse(caps.approvals)
            self.assertFalse(caps.continuation)

    def test_missing_evidence_fields_disable(self):
        for field in ("auth_verified", "protocol_verified", "confinement_verified", "cancellation_verified"):
            self.assertFalse(CodexAdapter(str(self.runtime), evidence=replace(EVIDENCE, **{field: False})).capabilities().available)

    async def test_scripted_success_and_continuation(self):
        adapter = ScriptedAdapter()
        events = await self.collect(adapter, self.command("hello"))
        self.assertEqual(events[-1].kind, "succeeded")
        session = events[0].provider_session_id
        continued = await self.collect(adapter, self.command("follow", provider_session_id=session))
        self.assertEqual(continued[0].provider_session_id, session)
        self.assertEqual(events[-1].payload["partial_effects"], [])

    async def test_scripted_fail_and_interrupt(self):
        adapter = ScriptedAdapter()
        for text, expected in (("[fail]", "failed"), ("[interrupt]", "interrupted")):
            self.assertEqual((await self.collect(adapter, self.command(text)))[-1].kind, expected)

    async def test_scripted_wait_cancel(self):
        adapter = ScriptedAdapter()
        stream = adapter.run(self.command("[wait]"))
        self.assertEqual((await anext(stream)).kind, "started")
        await adapter.cancel("attempt")
        events = [event async for event in stream]
        self.assertEqual(events[-1].kind, "cancelled")
        self.assertEqual(adapter._running, {})

    async def test_cancel_unknown_is_noop(self):
        await ScriptedAdapter().cancel("missing")
        await self.adapter().cancel("missing")

    async def test_literal_prompt_both_providers(self):
        instruction = f"--dangerously-bypass-approvals-and-sandbox; $(touch {self.root}/BAD) `echo pwn`\nline 2"
        for cls in (CodexAdapter, ClaudeAdapter):
            events = await self.collect(self.adapter(cls), self.command(instruction))
            self.assertEqual(events[-1].kind, "succeeded")
            output = next(e.payload["text"] for e in events if e.kind == "output")
            parsed = json.loads(output)
            self.assertEqual(parsed["instruction"], instruction)
            self.assertNotIn(instruction, parsed["argv"])
            self.assertFalse((self.root / "BAD").exists())
            self.assertFalse(any("dangerously" in arg for arg in parsed["argv"]))

    async def test_resume_exact_session_both(self):
        for cls in (CodexAdapter, ClaudeAdapter):
            events = await self.collect(self.adapter(cls), self.command("follow", provider_session_id=SESSION))
            self.assertEqual(events[-1].kind, "succeeded")
            args = json.loads(next(e.payload["text"] for e in events if e.kind == "output"))["argv"]
            self.assertIn(SESSION, args)
            self.assertNotIn("--last", args)

    async def test_reject_unsupported_profile_before_probe(self):
        adapter = self.adapter()
        events = await self.collect(adapter, self.command(execution_profile="extended"))
        self.assertEqual([e.kind for e in events], ["failed"])
        self.assertEqual(events[-1].payload["error"]["code"], "unsupported_capability")

    async def test_continuation_disabled(self):
        adapter = CodexAdapter(str(self.runtime), evidence=replace(EVIDENCE, continuation_verified=False))
        self.assertEqual((await self.collect(adapter, self.command(provider_session_id=SESSION)))[-1].kind, "failed")

    async def test_invalid_session_is_data_not_option(self):
        events = await self.collect(self.adapter(), self.command(provider_session_id="--last"))
        self.assertEqual(events[-1].payload["error"]["code"], "invalid_request")

    async def test_pin_mismatch_no_provider_start(self):
        adapter = CodexAdapter(str(self.runtime), evidence=replace(EVIDENCE, version="9.9.9"))
        self.assertEqual([e.kind for e in await self.collect(adapter, self.command())], ["failed"])

    async def test_process_loss_malformed_limits_and_changed_session(self):
        for cls in (CodexAdapter, ClaudeAdapter):
            for text in ("[death]", "[malformed]", "[huge]", "[bad-shape]", "[session-change]", "[exit-after-success]"):
                with self.subTest(provider=cls.__name__, text=text):
                    adapter = self.adapter(cls)
                    events = await self.collect(adapter, self.command(text))
                    self.assertEqual(events[-1].kind, "interrupted")
                    self.assertFalse(any(e.kind == "succeeded" for e in events))
                    self.assertEqual(adapter._processes, {})

    async def test_no_reasoning_or_stderr_export(self):
        for cls in (CodexAdapter, ClaudeAdapter):
            for text in ("[reasoning]", "[stderr]"):
                events = await self.collect(self.adapter(cls), self.command(text))
                serialized = json.dumps([e.payload for e in events])
                self.assertNotIn("SECRET REASONING", serialized)
                self.assertNotIn("SENSITIVE STDERR", serialized)

    async def test_provider_failure_and_approval_denial(self):
        failed = await self.collect(self.adapter(), self.command("[fail]"))
        self.assertEqual(failed[-1].kind, "failed")
        denied = await self.collect(self.adapter(ClaudeAdapter), self.command("[permission]"))
        self.assertEqual(denied[-1].payload["error"]["code"], "unsupported_capability")

    async def test_cancel_confirms_process_stop(self):
        adapter = self.adapter()
        stream = adapter.run(self.command("[hang]"))
        self.assertEqual((await anext(stream)).kind, "started")
        process = adapter._processes["attempt"]
        await adapter.cancel("attempt")
        events = [event async for event in stream]
        self.assertEqual(events[-1].kind, "cancelled")
        self.assertIsNotNone(process.returncode)
        self.assertEqual(adapter._processes, {})

    async def test_timeout_never_reports_success(self):
        events = await self.collect(self.adapter(timeout_seconds=2), self.command("[hang]"))
        self.assertEqual(events[0].kind, "started")
        self.assertEqual(events[-1].kind, "interrupted")

    async def test_closed_consumer_stops_runtime(self):
        adapter = self.adapter()
        stream = adapter.run(self.command("[hang]"))
        await anext(stream)
        process = adapter._processes["attempt"]
        await stream.aclose()
        self.assertIsNotNone(process.returncode)
        self.assertEqual(adapter._active, set())

    async def test_timeout_does_not_cancel_paused_consumer(self):
        adapter = self.adapter(timeout_seconds=1)
        stream = adapter.run(self.command("[hang]"))
        self.assertEqual((await anext(stream)).kind, "started")
        # This pause represents the gateway committing an event, outside an
        # adapter await. Its task must not get a surprise CancelledError.
        await asyncio.sleep(1.1)
        events = [event async for event in stream]
        self.assertEqual(events[-1].kind, "interrupted")

    async def test_cancellation_with_full_stdout_and_paused_consumer(self):
        adapter = self.adapter()
        stream = adapter.run(self.command("[flood]"))
        await anext(stream)
        await asyncio.sleep(0.1)
        await asyncio.wait_for(adapter.cancel("attempt"), 1)
        events = [event async for event in stream]
        self.assertEqual(events[-1].kind, "cancelled")

    async def test_unicode_payload_byte_limit(self):
        for adapter in (ScriptedAdapter(), self.adapter()):
            events = await self.collect(adapter, self.command("😀" * 4000))
            for event in events:
                self.assertLessEqual(len(json.dumps(event.payload, ensure_ascii=False).encode()), 16384)

    async def test_duplicate_active_attempt_is_rejected(self):
        for adapter in (ScriptedAdapter(), self.adapter()):
            stream = adapter.run(self.command("[wait]" if isinstance(adapter, ScriptedAdapter) else "[hang]"))
            await anext(stream)
            rejected = await self.collect(adapter, self.command())
            self.assertEqual([event.kind for event in rejected], ["failed"])
            await adapter.cancel("attempt")
            await stream.aclose()


if __name__ == "__main__":
    unittest.main()
