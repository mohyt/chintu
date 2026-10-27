import asyncio
import os
import secrets
import sqlite3
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import UUID

from chintu_adapters import AdapterCapabilities, RuntimeEvidence
from chintu_adapters.types import tool_catalog

import logging

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from .contract import Contract, DEFAULT_CONTRACT
from .auth_env import assert_auth_environment
from .errors import ApiError, unavailable
from .manager import Manager
from .store import Store
from .systems import TailscaleDiscovery
from . import pairing_page
from . import workspace as workspaces
from .stream import EventHub, StreamRegistry, frame


@dataclass(frozen=True)
class Settings:
    database: Path
    projects: dict = field(default_factory=lambda: {
        "fixture": {"display_name": "Scripted fixture", "path": Path.cwd(),
                    "execution_profiles": ["inspect", "develop"]}
    })
    contract_path: Path = DEFAULT_CONTRACT
    queue_limit: int = 10
    enable_worker: bool = True
    runtime_evidence: RuntimeEvidence | None = None
    login_method: str | None = None
    notes_dir: Path | None = None
    token_ttl_days: int = 30
    # Event stream: heartbeat keeps proxies and the phone's read timeout alive; streams are recycled after max seconds.
    stream_heartbeat_seconds: float = 15
    stream_max_seconds: float = 900
    stream_check_seconds: float = 5


DEFAULT_PORT = 8765
PAIR_ATTEMPTS, PAIR_WINDOW_SECONDS = 10, 60


def local_secret_path(settings: Settings) -> Path:
    """Per-start proof that a caller runs as the owner on this machine; mode 0600 beside the database."""
    return Path(settings.database).resolve().parent / "local.secret"


def write_local_secret(settings: Settings) -> str:
    path = local_secret_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, secret.encode())
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return secret


class RateLimiter:
    """Fixed in-memory budget: at most ``limit`` attempts per rolling ``window`` seconds."""

    def __init__(self, limit=PAIR_ATTEMPTS, window=PAIR_WINDOW_SECONDS):
        self.limit, self.window = limit, window
        self.attempts = deque()

    def retry_after(self, now=None):
        """0 when an attempt may proceed (and is recorded); otherwise seconds to wait."""
        now = time.monotonic() if now is None else now
        while self.attempts and now - self.attempts[0] >= self.window:
            self.attempts.popleft()
        if len(self.attempts) >= self.limit:
            return max(1, int(self.window - (now - self.attempts[0])) + 1)
        self.attempts.append(now)
        return 0


class BodyLimit:
    """Bound chunked bodies as well as Content-Length before JSON parsing."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        data = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(data) + len(chunk) > 64 * 1024:
                error = ApiError(413, "invalid_request", "Request body exceeds 64 KiB.")
                return await JSONResponse(error.body, 413)(scope, receive, send)
            data.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(data), "more_body": False}
            return await receive()

        return await self.app(scope, bounded_receive, send)


def create_app(settings: Settings, adapter=None, systems_discovery=None):
    # Protect SQLite side files. Ubuntu runs one private owner process.
    os.umask(0o077)
    contract = Contract(settings.contract_path)
    projects = []
    for project_id, config in settings.projects.items():
        projects.append(contract.validate("Project", {"project_id": project_id,
            "display_name": config["display_name"], "execution_profiles": config["execution_profiles"]}))
    contract.validate("Projects", {"projects": projects})
    if adapter is not None:
        caps = adapter.capabilities()
        contract.validate("ProviderCapabilities", {**asdict(caps), "execution_profiles": list(caps.execution_profiles)})
        if caps.provider == "claude-agent":
            # The SDK provider is enabled by explicit configuration; it carries no CLI protocol evidence.
            assert_auth_environment(caps.provider)
            if not caps.available or not settings.projects:
                raise ValueError("The claude-agent runtime must be available with at least one registered workspace")
            for config in settings.projects.values():
                if not Path(config["path"]).is_absolute() or not Path(config["path"]).is_dir():
                    raise ValueError("A real provider requires existing, absolute registered workspace directories")
        elif caps.provider != "scripted":
            assert_auth_environment(caps.provider)
            evidence = settings.runtime_evidence
            if (caps.provider not in {"claude", "codex"} or not caps.available or evidence is None
                    or getattr(adapter, "evidence", None) != evidence
                    or settings.login_method not in {"subscription", "api_key"}
                    or not evidence.auth_verified or not evidence.protocol_verified
                    or not evidence.confinement_verified or not evidence.cancellation_verified
                    or not evidence.record.strip()):
                raise ValueError("Explicit account method and reviewed, available runtime evidence are required")
            record = Path(evidence.record)
            if not record.is_file() or record.stat().st_size > 65536 or not record.read_bytes().strip():
                raise ValueError("Runtime evidence must reference an existing, nonempty local review record")
            for config in settings.projects.values():
                if not Path(config["path"]).is_absolute() or not Path(config["path"]).is_dir():
                    raise ValueError("A real provider requires existing, absolute registered workspace directories")
            if not settings.projects:
                raise ValueError("A real provider requires at least one registered workspace")
    store = Store(settings.database, settings.queue_limit,
                  project_paths={key: value["path"] for key, value in settings.projects.items()},
                  token_ttl_days=settings.token_ttl_days)
    local_secret = write_local_secret(settings)
    hub, streams = EventHub(), StreamRegistry()
    store.listeners.append(hub.notify)
    pair_limiter = RateLimiter()
    manager = Manager(store, contract, adapter, settings.projects)
    discovery = systems_discovery if systems_discovery is not None else TailscaleDiscovery()

    @asynccontextmanager
    async def lifespan(app):
        hub.bind(asyncio.get_running_loop())
        if settings.enable_worker:
            await manager.start()
        try:
            yield
        finally:
            if settings.enable_worker:
                await manager.close()

    app = FastAPI(title="Chintu gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimit)
    app.state.store, app.state.manager, app.state.contract = store, manager, contract
    app.state.local_secret = local_secret
    app.state.hub, app.state.streams = hub, streams

    @app.exception_handler(ApiError)
    async def api_error(request, error):
        return JSONResponse(error.body, error.status)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, error):
        return JSONResponse(ApiError(503, "storage_unavailable", "Storage is unavailable.", retryable=True).body, 503)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse(ApiError(422, "invalid_request", "Invalid request.").body, 422)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse(ApiError(error.status_code, "not_found" if error.status_code == 404 else "invalid_request",
                                     "Route not found." if error.status_code == 404 else "Invalid request.").body, error.status_code)

    def protocol(request: Request):
        if request.headers.get("X-Chintu-Protocol") != "1":
            raise ApiError(426, "protocol_mismatch", "X-Chintu-Protocol: 1 is required.")

    def identity(request: Request):
        protocol(request)
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or not 32 <= len(authorization[7:]) <= 512:
            raise ApiError(401, "unauthorized", "A valid client credential is required.")
        request.state.client_id = store.authenticate(authorization[7:])

    async def body(request, schema):
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise ApiError(422, "invalid_request", "Content-Type must be application/json.")
        try:
            value = await request.json()
        except (ValueError, UnicodeError):
            raise ApiError(422, "invalid_request", "Invalid JSON body.") from None
        value = contract.request(schema, value)
        for key in ("submission_key", "message_id", "conversation_id", "attempt_id"):
            if value.get(key) is not None:
                value[key] = str(UUID(value[key]))
        return value

    def identifier(value):
        try:
            if len(value) != 36:
                raise ValueError
            return str(UUID(value))
        except ValueError:
            raise ApiError(422, "invalid_request", "Invalid resource ID.") from None

    def integer(value, minimum=0, maximum=9007199254740991):
        try:
            number = int(value)
            if not minimum <= number <= maximum:
                raise ValueError
            return number
        except (TypeError, ValueError):
            raise ApiError(422, "invalid_request", "Invalid numeric query parameter.") from None

    def providers():
        available = asdict(adapter.capabilities()) if adapter is not None else None
        if available is not None:
            available["execution_profiles"] = list(available["execution_profiles"])
        return [{"provider": name, "available": False, "execution_profiles": [],
                 "continuation": False, "cancellation": False, "approvals": False,
                 "active_steering": False, "session_recovery": False,
                 "reason": "Provider is not enabled in this phase."}
                if available is None or name != available["provider"] else available
                for name in ("claude", "codex", "claude-agent", "scripted")]

    def validate_capability(value):
        if manager.faulted:
            raise ApiError(503, "storage_unavailable", "Worker storage needs operator recovery.", retryable=True)
        if value["project_id"] not in settings.projects:
            raise ApiError(404, "not_found", "Registered project not found.")
        if adapter is None:
            raise unavailable()
        capability = adapter.capabilities()
        if (not capability.available or value["provider"] != capability.provider
                or value["execution_profile"] not in capability.execution_profiles
                or value["execution_profile"] not in settings.projects[value["project_id"]]["execution_profiles"]):
            raise unavailable()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/info")
    async def info():
        return {"service": "chintu", "protocol_version": 1, "api_version": "1.0.0"}

    def local_only(request: Request, presented=None):
        """Every local endpoint needs loopback peer, clean headers, loopback Host and the local proof."""
        host = request.client.host if request.client else None
        server = request.scope.get("server") or (None, DEFAULT_PORT)
        port = server[1] if isinstance(server, (tuple, list)) and len(server) > 1 and server[1] else DEFAULT_PORT
        if presented is None:
            presented = request.headers.get(pairing_page.LOCAL_HEADER)
        return pairing_page.is_local_request(host, request.headers.items(), port, local_secret, presented)

    def refuse_local():
        # One generic answer: which check failed is not disclosed.
        return ApiError(403, "forbidden", "This endpoint is only available to the owner on this machine.")

    @app.get("/pair", include_in_schema=False)
    async def pair_page(request: Request):
        # Operator-only: a QR code with a fresh enrollment secret, shown on this machine's own screen.
        # A browser cannot set headers, so the proof travels as ?key= from the printed pair-url.
        if not local_only(request, request.query_params.get("key") or ""):
            return HTMLResponse(pairing_page.refused_page(), status_code=403)
        found = await discovery.discover()
        own = next((item for item in found.get("systems", []) if item.get("is_self")), None)
        if own is None:
            return HTMLResponse(pairing_page.unavailable_page(found.get("reason", "Tailscale is not connected.")), status_code=503)
        ttl = 600
        secret = store.create_enrollment(ttl)
        return HTMLResponse(pairing_page.render(own["gateway_url"], pairing_page.default_name(own["gateway_url"]), secret, ttl),
                            headers={"Cache-Control": "no-store"})

    @app.get("/local/status", include_in_schema=False)
    async def local_status(request: Request):
        # Operator-only presence summary for the desktop companion.
        if not local_only(request):
            raise refuse_local()
        found = await discovery.discover()
        own = next((item for item in found.get("systems", []) if item.get("is_self")), None)
        status = store.local_status()
        status.update({
            "service": "chintu",
            "provider": next((item["provider"] for item in providers() if item.get("available")), None),
            "tailscale": {"status": found.get("status"), "reason": found.get("reason"),
                          "gateway_url": own["gateway_url"] if own else None, "name": own["name"] if own else None},
            "pair_url": "http://127.0.0.1:%d/pair" % (request.url.port or DEFAULT_PORT),
        })
        return status

    @app.get("/local/clients", include_in_schema=False)
    async def local_clients(request: Request):
        if not local_only(request):
            raise refuse_local()
        return {"clients": store.clients()}

    @app.post("/local/clients/{client_id}/revoke", include_in_schema=False)
    async def local_revoke(request: Request, client_id: str):
        if not local_only(request):
            raise refuse_local()
        if not store.revoke(identifier(client_id)):
            raise ApiError(404, "not_found", "Client not found.")
        logging.getLogger("chintu.pair").info("revoked client %s locally", client_id[:8])
        return {"client_id": client_id, "revoked": True}

    @app.post("/local/pairing-code", include_in_schema=False)
    async def local_pairing_code(request: Request):
        # Operator-only: the desktop companion renders this payload as a QR in its own window.
        if not local_only(request):
            raise refuse_local()
        found = await discovery.discover()
        own = next((item for item in found.get("systems", []) if item.get("is_self")), None)
        if own is None:
            raise ApiError(503, "unavailable", found.get("reason") or "Tailscale is not connected.")
        ttl = 600
        secret = store.create_enrollment(ttl)
        name = pairing_page.default_name(own["gateway_url"])
        return {"payload": pairing_page.pairing_payload(own["gateway_url"], name, secret),
                "url": own["gateway_url"], "name": name, "expires_in": ttl}

    @app.post("/api/v1/pair", dependencies=[Depends(protocol)])
    async def pair(request: Request):
        wait = pair_limiter.retry_after()
        if wait:
            error = ApiError(429, "rate_limited", "Too many pairing attempts; try again later.", retryable=True)
            return JSONResponse(error.body, 429, headers={"Retry-After": str(wait)})
        value = await body(request, "PairRequest")
        paired = store.pair(value["enrollment_secret"], value["client_name"])
        logging.getLogger("chintu.pair").info("paired client %r (%s)", value["client_name"], paired["client_id"][:8])
        return paired

    api = APIRouter(prefix="/api/v1", dependencies=[Depends(identity)])

    @api.delete("/clients/me", status_code=204)
    async def revoke(request: Request):
        store.revoke(request.state.client_id)
        return Response(status_code=204)

    notes_dir = Path(settings.notes_dir) if settings.notes_dir else Path(settings.database).resolve().parent / "notes"

    @api.post("/notes", status_code=201)
    async def create_note(request: Request):
        await body(request, "NoteCreate")
        return contract.validate("NoteCreated", store.note_create(request.state.client_id))

    @api.post("/notes/{note_id}/chunks")
    async def append_note(note_id: str, request: Request):
        value = await body(request, "NoteChunk")
        return contract.validate("NoteProgress", store.note_append(note_id, request.state.client_id, value["text"]))

    @api.post("/notes/{note_id}/finish")
    async def finish_note(note_id: str, request: Request):
        await body(request, "NoteCreate")
        saved = store.note_finish(note_id, request.state.client_id, notes_dir)
        logging.getLogger("chintu.note").info("note %s saved as %s (%d words)", note_id[:8], saved["file"], saved["words"])
        return contract.validate("NoteSaved", saved)

    @api.get("/projects")
    async def list_projects():
        return {"projects": projects}

    @api.get("/capabilities")
    async def capabilities():
        return {"providers": providers()}

    @api.get("/systems")
    async def systems():
        return contract.validate("Systems", await discovery.discover())

    @api.post("/echo")
    async def echo(request: Request):
        value = await body(request, "EchoRequest")
        return {"text": value["text"], "mode": "echo"}

    @api.post("/tasks")
    async def submit(request: Request):
        value = await body(request, "SubmitTask")
        snapshot, replayed = store.submit(request.state.client_id, value, validate_capability)
        manager.wakeup.set()
        return JSONResponse({"task": snapshot, "replayed": replayed}, 200 if replayed else 202)

    @api.get("/tasks")
    async def list_tasks(request: Request):
        limit = integer(request.query_params.get("limit", "20"), 1, 100)
        cursor = request.query_params.get("cursor")
        if cursor is not None and len(cursor) > 512:
            raise ApiError(422, "invalid_request", "Task cursor is too long.")
        return store.tasks_page(limit, cursor, request.state.client_id)

    @api.get("/tasks/{task_id}")
    async def get_task(request: Request, task_id: str):
        return store.get(identifier(task_id), request.state.client_id)

    # --- Conversations: voice and chat share one agent session and working directory per conversation. ---
    @api.post("/conversations")
    async def create_conversation(request: Request):
        value = await body(request, "ConversationCreate")
        conversation, replayed = store.create_conversation(request.state.client_id, value, validate_capability)
        result = contract.validate("ConversationCreated", {"conversation": conversation, "replayed": replayed})
        return JSONResponse(result, 200 if replayed else 201)

    @api.get("/conversations")
    async def list_conversations(request: Request):
        limit = integer(request.query_params.get("limit", "20"), 1, 100)
        cursor = request.query_params.get("cursor")
        if cursor is not None and len(cursor) > 512:
            raise ApiError(422, "invalid_request", "Conversation cursor is too long.")
        return contract.validate("ConversationsPage", store.conversations_page(request.state.client_id, limit, cursor))

    @api.get("/conversations/{conversation_id}")
    async def get_conversation(request: Request, conversation_id: str):
        return contract.validate("Conversation", store.conversation(request.state.client_id, identifier(conversation_id)))

    @api.patch("/conversations/{conversation_id}")
    async def rename_conversation(request: Request, conversation_id: str):
        conversation_id = identifier(conversation_id)
        value = await body(request, "ConversationUpdate")
        if not value["title"].strip():
            raise ApiError(422, "invalid_request", "A conversation title cannot be blank.")
        return contract.validate("Conversation", store.rename_conversation(
            request.state.client_id, conversation_id, value["title"]))

    @api.delete("/conversations/{conversation_id}", status_code=204)
    async def archive_conversation(request: Request, conversation_id: str):
        store.archive_conversation(request.state.client_id, identifier(conversation_id))
        return Response(status_code=204)

    @api.get("/conversations/{conversation_id}/tasks")
    async def conversation_tasks(request: Request, conversation_id: str):
        conversation_id = identifier(conversation_id)
        store.conversation(request.state.client_id, conversation_id)
        limit = integer(request.query_params.get("limit", "20"), 1, 100)
        cursor = request.query_params.get("cursor")
        if cursor is not None and len(cursor) > 512:
            raise ApiError(422, "invalid_request", "Task cursor is too long.")
        return store.tasks_page(limit, cursor, request.state.client_id, conversation_id)

    def registered_root(project_id):
        if not isinstance(project_id, str) or project_id not in settings.projects:
            raise ApiError(404, "not_found", "Registered project not found.")
        return store.project_paths.get(project_id, "project:" + project_id)

    @api.get("/directories")
    async def directories(request: Request):
        project_id = request.query_params.get("project_id")
        root = registered_root(project_id)
        found = workspaces.listing(root, request.query_params.get("path", ""))
        return contract.validate("Directories", {"project_id": project_id, **found})

    @api.get("/tools")
    async def tools(request: Request):
        project_id, profile = request.query_params.get("project_id"), request.query_params.get("execution_profile")
        root = registered_root(project_id)
        if adapter is None:
            raise unavailable()
        capability = adapter.capabilities()
        if (not capability.available or profile not in capability.execution_profiles
                or profile not in settings.projects[project_id]["execution_profiles"]):
            raise unavailable()
        _, path = workspaces.resolve(root, request.query_params.get("working_directory", ""))
        if hasattr(adapter, "tools"):
            catalog = await adapter.tools(str(path), profile)
        else:
            catalog = tool_catalog("static", reason="This agent does not report its tools.")
        return contract.validate("ToolCatalog", {"provider": capability.provider, "project_id": project_id,
                                                 "execution_profile": profile, **catalog})

    @api.get("/events")
    async def events(request: Request):
        after = integer(request.query_params.get("after", "0"))
        limit = integer(request.query_params.get("limit", "100"), 1, 100)
        task_id = request.query_params.get("task_id")
        return store.events_page(after, limit, identifier(task_id) if task_id is not None else None,
                                 request.state.client_id)

    @api.get("/events/stream")
    async def event_stream(request: Request):
        """Server-sent events: replay after the cursor, then push each event as it commits."""
        client_id = request.state.client_id
        token = request.headers.get("Authorization", "")[7:]
        # Last-Event-ID is the standard SSE resume point and wins over the query cursor.
        resume = request.headers.get("Last-Event-ID")
        after = integer(resume if resume is not None else request.query_params.get("after", "0"))
        task_id = request.query_params.get("task_id")
        task_id = identifier(task_id) if task_id is not None else None
        lifetime = settings.stream_max_seconds
        if request.query_params.get("max_seconds") is not None:
            lifetime = min(lifetime, integer(request.query_params["max_seconds"], 1, 86400))
        heartbeat = settings.stream_heartbeat_seconds
        store.events_page(after, 1, task_id, client_id)  # An impossible cursor fails here, before the 200.
        ticket = streams.open(client_id)
        hub.bind(asyncio.get_running_loop())

        async def body():
            loop = asyncio.get_running_loop()
            cursor, started = after, loop.time()
            last_frame = last_auth = started
            try:
                yield frame(comment="chintu event stream", retry=2000) + frame(event="ready", data={
                    "cursor": cursor, "heartbeat_seconds": heartbeat, "max_seconds": lifetime})
                while not ticket.evicted:
                    now = loop.time()
                    if now - started >= lifetime:
                        break  # Recycle: the client reconnects at once from its last event ID.
                    if now - last_auth >= heartbeat:
                        try:
                            store.authenticate(token)  # A revoked or expired phone loses its stream too.
                        except ApiError:
                            break
                        last_auth = now
                    page = store.events_page(cursor, 100, task_id, client_id)
                    for event in page["events"]:
                        yield frame(event=event["kind"], data=event, event_id=event["event_id"])
                    cursor = page["next_cursor"]
                    if page["events"]:
                        last_frame = now
                        if page["has_more"]:
                            continue
                    elif now - last_frame >= heartbeat:
                        yield frame(comment="keepalive")
                        last_frame = now
                    # Commits in this process wake the stream at once; the timeout catches other writers.
                    await hub.wait(min(settings.stream_check_seconds, heartbeat - (now - last_frame),
                                       lifetime - (now - started)))
            finally:
                streams.close(ticket)

        return StreamingResponse(body(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    async def mutation(request, task_id, operation, schema):
        task_id = identifier(task_id)
        value = await body(request, schema)
        # Idempotency lookup must precede current capability and stale-version checks.
        capability = adapter.capabilities() if adapter is not None else AdapterCapabilities(
            "scripted", False, (), False, False, False, False, False, "Provider is not enabled.")
        snapshot, replayed, signal = store.mutate(request.state.client_id, task_id, operation,
                                                 value, capability)
        manager.wakeup.set()
        if signal:
            try:
                await manager.cancel(signal)
            except Exception:
                # Request is already committed. Poll for reconciled outcome.
                pass
        return {"task": snapshot, "replayed": replayed}

    @api.post("/tasks/{task_id}/messages")
    async def followup(request: Request, task_id: str):
        return await mutation(request, task_id, "followup", "FollowUp")

    @api.post("/tasks/{task_id}/cancel")
    async def cancel(request: Request, task_id: str):
        return await mutation(request, task_id, "cancel", "CancelTask")

    @api.post("/tasks/{task_id}/inputs/{request_id}/answer")
    async def answer(request: Request, task_id: str, request_id: str):
        identifier(request_id)
        await body(request, "AnswerInput")
        store.get(identifier(task_id), request.state.client_id)
        raise unavailable()

    @api.post("/tasks/{task_id}/approvals/{request_id}/decision")
    async def decision(request: Request, task_id: str, request_id: str):
        identifier(request_id)
        await body(request, "ApprovalDecision")
        store.get(identifier(task_id), request.state.client_id)
        raise unavailable()

    @api.get("/artifacts/{artifact_id}")
    async def artifact(artifact_id: str):
        identifier(artifact_id)
        raise unavailable()

    app.include_router(api)
    return app
