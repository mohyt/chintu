import asyncio
import fcntl
import logging
import sqlite3
from contextlib import suppress
from pathlib import Path

from chintu_adapters import TaskCommand

from .auth_env import assert_auth_environment
from .store import TERMINAL, encode, result

log = logging.getLogger("chintu.task")
# Per-attempt budget for progress, output and streamed output deltas; a streamed reply needs many small events.
OUTPUT_EVENT_LIMIT = 1500
OUTPUT_BYTE_LIMIT = 512 * 1024
TEXT_KINDS = {"progress", "output", "output_delta"}


def _preview(text, limit=200):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _words(text):
    return len(str(text or "").split())


class Manager:
    """Single serialized worker with conservative real-runtime recovery quarantine."""

    def __init__(self, store, contract, adapter, projects):
        self.store, self.contract, self.adapter, self.projects = store, contract, adapter, projects
        self.wakeup = asyncio.Event()
        self.scheduler = None
        self.lock = None
        self.faulted = False

    async def start(self):
        self.lock = open(str(self.store.path) + ".worker.lock", "a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            self.lock = None
            raise RuntimeError("Another gateway worker owns this database") from None
        try:
            self.store.recover()
            self.scheduler = asyncio.create_task(self._schedule())
        except BaseException:
            self.lock.close()
            self.lock = None
            raise

    async def close(self):
        try:
            if self.scheduler:
                self.scheduler.cancel()
                with suppress(asyncio.CancelledError):
                    await self.scheduler
            # Shutdown does not assert that a task was intentionally cancelled.
            self.store.recover()
        finally:
            if self.lock:
                self.lock.close()
                self.lock = None

    async def cancel(self, attempt_id):
        if attempt_id is not None and self.adapter is not None:
            await self.adapter.cancel(attempt_id)

    async def _schedule(self):
        try:
            while True:
                self.wakeup.clear()
                if self.adapter is None:
                    await self.wakeup.wait()
                    continue
                claimed = self.store.claim(self.adapter.capabilities().provider)
                if claimed is None:
                    await self.wakeup.wait()
                    continue
                snapshot, instruction = claimed
                await self._execute(snapshot, instruction)
        except sqlite3.Error:
            # Fail closed on worker storage loss. Operator restart reconciles active work.
            self.faulted = True

    async def _execute(self, snapshot, instruction):
        task_id, attempt_id = snapshot["task_id"], snapshot["attempt_id"]
        output_bytes, output_events = 0, 0
        terminal = False
        iterator = None
        try:
            if snapshot["project_id"] not in self.projects:
                raise ValueError("Project registration removed")
            caps = self.adapter.capabilities()
            assert_auth_environment(caps.provider)
            project = self.projects[snapshot["project_id"]]
            if (not caps.available or snapshot["provider"] != caps.provider
                    or snapshot["execution_profile"] not in caps.execution_profiles
                    or snapshot["execution_profile"] not in project["execution_profiles"]):
                raise ValueError("Queued task no longer has an available execution profile")
            workspace = self.store.workspace_for_task(task_id)
            # A conversation may narrow the run to a folder inside the workspace, never anywhere else.
            root = Path(project["path"]).resolve()
            if (str(Path(workspace).resolve()) != workspace or not Path(workspace).is_dir()
                    or not Path(workspace).is_relative_to(root)):
                raise ValueError("Registered workspace changed after task acceptance")
            command = TaskCommand(task_id, attempt_id, workspace, instruction,
                                  snapshot["execution_profile"], snapshot["provider_session_id"])
            # INFO carries identifiers and sizes only; instruction/progress/output text is DEBUG.
            log.info("task %s start provider=%s profile=%s project=%s conversation=%s resume=%s instruction_words=%d",
                     task_id[:8], snapshot["provider"], snapshot["execution_profile"], snapshot["project_id"],
                     (snapshot.get("conversation_id") or "-")[:8], bool(snapshot["provider_session_id"]), _words(instruction))
            log.debug("task %s instruction=%r", task_id[:8], _preview(instruction, 160))
            iterator = self.adapter.run(command)
            async for event in iterator:
                if event.kind == "started":
                    log.info("task %s session %s", task_id[:8], (event.provider_session_id or "")[:36])
                elif event.kind in {"progress", "output"}:
                    # Deltas are too frequent for INFO; their text reaches the log only as the finished output.
                    log.info("task %s %s words=%d", task_id[:8], event.kind, _words(event.payload.get("text")))
                    log.debug("task %s %s: %s", task_id[:8], event.kind, _preview(event.payload.get("text")))
                elif event.kind in TERMINAL:
                    log.info("task %s %s words=%d", task_id[:8], event.kind, _words(event.payload.get("summary")))
                    log.debug("task %s %s: %s", task_id[:8], event.kind, _preview(event.payload.get("summary")))
                if event.kind == "started":
                    if not event.provider_session_id or len(event.provider_session_id) > 512:
                        raise ValueError("Missing or oversized provider session")
                    payload = {}
                elif event.kind in TEXT_KINDS:
                    self.contract.validate("TextPayload", event.payload)
                    payload = event.payload
                    output_bytes += len(encode(payload).encode())
                    output_events += 1
                    if output_bytes > OUTPUT_BYTE_LIMIT or output_events > OUTPUT_EVENT_LIMIT:
                        raise ValueError("Adapter output budget exceeded")
                elif event.kind in TERMINAL:
                    self.contract.validate("TaskResult", event.payload)
                    payload = event.payload
                else:
                    raise ValueError("Unsupported adapter control event")
                if len(encode(payload).encode()) > 16 * 1024:
                    raise ValueError("Adapter event budget exceeded")
                self.store.adapter_event(task_id, attempt_id, event.kind, payload, event.provider_session_id)
                # An early cancellation can precede adapter registration. Deliver it
                # again after started, when the fixture can confirm execution stop.
                if event.kind == "started" and self.store.get(task_id)["state"] == "cancelling":
                    await self.adapter.cancel(attempt_id)
                if event.kind in TERMINAL:
                    terminal = True
                    break
            if not terminal:
                self.store.adapter_event(task_id, attempt_id, "interrupted", result(
                    "Worker ended without a confirmed outcome. Reconciliation is required.", "worker_lost",
                    partial_effects=snapshot["provider"] != "scripted"))
        except asyncio.CancelledError:
            raise
        except Exception as failure:
            # The local log may name the failure class; the phone-facing result stays generic.
            log.warning("task %s worker failure: %s", task_id[:8], failure.__class__.__name__)
            with suppress(Exception):
                await self.adapter.cancel(attempt_id)
            # Never expose exceptions containing prompts, credentials, or host paths.
            self.store.adapter_event(task_id, attempt_id, "interrupted", result(
                "Worker could not confirm an outcome. Reconciliation is required.", "worker_lost",
                partial_effects=snapshot["provider"] != "scripted"))
        finally:
            if iterator is not None and hasattr(iterator, "aclose"):
                with suppress(Exception):
                    await iterator.aclose()
