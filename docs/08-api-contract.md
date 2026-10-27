# API contract

Contract v1.0.0 · 10 October 2026 · implementation baseline

## Purpose and ownership

Android converts voice to text and speaks returned text. Ubuntu owns authentication, registered projects, agent execution, durable task state, and history. There is no microphone/audio upload endpoint and no OpenAI speech dependency. This contract is agreed as the baseline for parallel implementation; it does not claim that an endpoint or device test has passed.

The machine-readable HTTP contract is [openapi.json](../contracts/openapi.json), using OpenAPI 3.1.0. JSON schemas, examples, and enums in that file are authoritative for field names. The internal runner contract is [adapter-protocol.md](../contracts/adapter-protocol.md). Contract changes must update examples, both clients, and focused checks together.

## Transport and authentication

Use private HTTPS through Tailscale Serve; development binds only to `127.0.0.1`. JSON uses UTF-8 and snake_case. Timestamps are UTC RFC 3339 strings. IDs are opaque UUIDs except registered project slugs, integer event cursors, and discovered-system IDs (canonical MagicDNS names). Clients never provide host filesystem paths or commands to launch a runner.

`GET /health` and `GET /api/v1/info` expose only service/protocol availability. Other `/api/v1` operations require `X-Chintu-Protocol: 1`. Pairing uses a short-lived, single-use secret created through a local Ubuntu command. Successful pairing returns a client ID and opaque bearer token; all remaining endpoints require `Authorization: Bearer <token>`. Store only token verifiers on Ubuntu. Pairing credentials never appear in URLs, logs, or examples. If a pairing response is lost, obtain a new enrollment secret locally; do not reuse a consumed secret. Pairing is throttled (`429` with `Retry-After` after ten attempts per minute), credentials expire after the gateway's `token_ttl_days` (`401` afterwards; pair again), and every task, event and note is visible only to the client that created it (`404` otherwise).

All enrolled clients belong to the one configured owner and may access that owner's tasks. Idempotency is scoped to the authenticated client and operation. Revocation rejects subsequent requests. Version mismatch returns `426`; authentication failure returns `401`. Unsupported features return `501` and are advertised as unavailable. Default development exposes only the explicitly enabled scripted adapter, never automatically selects a real provider or billing method.

## Operations

| Method and route | Purpose | Initial phase |
| --- | --- | --- |
| `GET /health` | Process availability | M2 |
| `GET /api/v1/info` | Protocol discovery; no private task data | M2 |
| `POST /api/v1/pair` | Exchange local enrollment secret for phone credential | M2 |
| `DELETE /api/v1/clients/me` | Revoke the current client | M2 |
| `GET /api/v1/projects` | List registered IDs and allowed profiles | M2/M4 |
| `GET /api/v1/capabilities` | Discover enabled providers and supported controls | M2/M3 |
| `GET /api/v1/systems` | Discover candidate computers visible to this gateway in Tailscale | M2 |
| `POST /api/v1/echo` | Return a test text reply; creates no task | M2 |
| `POST /api/v1/tasks` | Durably submit work | M4 |
| `GET /api/v1/tasks` | Page through current task snapshots | M4 |
| `GET /api/v1/tasks/{task_id}` | Get current state, result, or pending input | M4 |
| `GET /api/v1/events` | Fetch ordered events after a cursor | M4 |
| `POST /api/v1/tasks/{task_id}/messages` | Follow up in the same idle provider session | M4/M6 |
| `POST /api/v1/tasks/{task_id}/cancel` | Request cancellation; return current state | M4/M6 |
| `POST /api/v1/tasks/{task_id}/inputs/{request_id}/answer` | Answer an ordinary clarification | M6 |
| `POST /api/v1/tasks/{task_id}/approvals/{request_id}/decision` | Allow/deny the exact recorded action | M6 |
| `GET /api/v1/artifacts/{artifact_id}` | Retrieve an authorized result artifact | M6 |
| `POST /api/v1/conversations` | Start a conversation pinned to a workspace folder, agent and profile (idempotent on `conversation_id`) | Conversations |
| `GET /api/v1/conversations` | Page through conversations, most recently active first | Conversations |
| `GET /api/v1/conversations/{conversation_id}` | Read one conversation | Conversations |
| `PATCH /api/v1/conversations/{conversation_id}` | Rename a conversation | Conversations |
| `DELETE /api/v1/conversations/{conversation_id}` | Archive an idle conversation | Conversations |
| `GET /api/v1/conversations/{conversation_id}/tasks` | Turns of one conversation, oldest first | Conversations |
| `GET /api/v1/directories` | Subfolders of a registered workspace, for choosing a working directory | Conversations |
| `GET /api/v1/tools` | Tools, MCP servers and skills under one profile | Conversations |

M2's echo reply is explicitly a test reply. M4 can implement lifecycle behavior with a scripted adapter; real-provider capability claims require M3/M6 evidence. Unimplemented routes must fail explicitly rather than return invented success.

## Conversations, working directories and tools

A conversation groups tasks that continue one agent session. `POST /api/v1/conversations` takes a client-generated `conversation_id`, `project_id`, `provider`, `execution_profile`, an optional `working_directory` relative to the registered workspace (empty for the root) and an optional `title`. It returns `201` with `{conversation, replayed: false}`; repeating it with the same workspace, agent, profile and folder returns `200` with `replayed: true` (the title is not compared, because the first turn or a rename may set it). Different content, or another client's ID, returns `409 idempotency_conflict`. A folder outside the workspace (absolute, `~`, `..`, or a symlink that leaves it) returns `422`; a missing folder or a file returns `404`.

`POST /api/v1/tasks` with a `conversation_id` adds a turn. The task must use the conversation's project, provider and profile, or the gateway returns `409 invalid_state` ("start a new one"); the same applies to an archived conversation or one whose folder no longer exists. An unknown `conversation_id` creates a conversation at the workspace root, titled from the first instruction. Another client's conversation is `404`. The turn's snapshot carries the conversation's current `provider_session_id`, refreshed when the worker claims it.

A `Conversation` reports `state` (`working` while any turn is unfinished), `turns`, `last_task_id`, a bounded `last_text` (latest result summary or instruction) and timestamps. Lists are ordered by `updated_at` and paged with an opaque cursor. `DELETE` archives only an idle conversation; archived conversations disappear from the list and accept no turns, while their tasks stay in history.

`GET /api/v1/directories?project_id=…&path=…` lists visible subfolders (hidden folders and links leaving the workspace are omitted; links inside it report their target path), the folder's `parent` (`null` at the root), and `truncated` beyond 500 entries. `GET /api/v1/tools?project_id=…&execution_profile=…&working_directory=…` returns a `ToolCatalog`: `tools` (`builtin` or `mcp` with its `server`), `mcp_servers` with connection status and tool counts, `skills`, a `source` (`static` for fixed profile lists, `probe` when the agent was asked, `cached` for a recent probe or the last session's tool list) and a `reason` when the list is partial. The profile must be enabled for the project and runtime, otherwise `501`.

## Computer discovery and switching

`GET /api/v1/systems` requires the same protocol header and enrolled bearer token as
task operations. It reads local `tailscale status --json` with explicit argv, a
three-second deadline, a 512 KiB output limit, and no shell, network scan, remote
probe, or Tailscale API credential. A five-second cache serializes repeated probes.
Only `Self` and `Peer` entries with Linux, macOS, or Windows and valid ASCII `.ts.net`
DNS names are returned. Discovery retains offline devices, de-duplicates by canonical
DNS identity, and returns at most 100 computers. Names are display/matching data and
may be ambiguous; clients must not treat spoken names as authorization.

```json
{
  "status": "available",
  "reason": null,
  "systems": [
    {
      "system_id": "cobra-ubuntu.example.ts.net",
      "name": "Cobra Ubuntu",
      "os": "linux",
      "online": true,
      "gateway_url": "https://cobra-ubuntu.example.ts.net",
      "is_self": false
    }
  ]
}
```

`os` is `linux`, `macos`, or `windows`. `system_id` is the lowercase DNS name without
its trailing dot. URLs have exactly `https://` plus that canonical `.ts.net` host,
with no port, path, userinfo, or credential. Private IPs, public keys, owner profiles,
daemon diagnostics, and login URLs are not exported. The online flag describes
Tailscale control-plane connectivity, not Chintu readiness. Names/DNS IDs can change
when a machine is renamed; clients should refresh and keep per-origin credentials.

A working discovery with no eligible entries returns `available` and an empty list.
Missing CLI, disconnected/logged-out Tailscale, timeout, excessive output, command
failure, or unsupported JSON returns HTTP `200` with `status: "unavailable"`, a
bounded generic `reason`, and an empty list. Authentication/protocol failures retain
their ordinary `401`/`426` envelopes.

The list is the gateway's visible tailnet subset, not a guarantee of phone visibility
or Chintu installation. Initial setup still needs one manually paired gateway.
Before switching systems, the phone must pair separately and verify that candidate's
authenticated projects, capabilities, and task API. Never forward the current gateway's
token to a discovered host. Switching does not move/cancel existing remote tasks;
their origin and history remain attached to the machine that accepted them.

The source format follows the [Tailscale CLI documentation](https://tailscale.com/docs/reference/tailscale-cli)
and [upstream status types](https://github.com/tailscale/tailscale/blob/main/ipn/ipnstate/ipnstate.go).
Compatibility with the user's actual Tailscale version remains a device/setup check.

## Submission and retry rules

The phone submits final transcript text only. It supplies a new `submission_key` UUID once per intentional new task and persists the request/key until acceptance is known. Retrying the same client/key and normalized content returns the original task with `replayed: true`; changed content returns `409 idempotency_conflict`. The check and durable insert are transactional. A full queue returns `429`; a failed commit returns `503` and launches nothing.

```json
{
  "submission_key": "11111111-1111-4111-8111-111111111111",
  "project_id": "fixture",
  "provider": "scripted",
  "instruction": "Summarize this project",
  "execution_profile": "inspect",
  "conversation_id": "22222222-2222-4222-8222-222222222222"
}
```

New submissions return `202` with `{ "task": <TaskSnapshot>, "replayed": false }`; retries return `200` with the current snapshot of that same task. Task provider/project are immutable. A new provider choice creates a new task. `scripted` is a development fixture, not Claude or Codex.

Follow-up, answer, approval, and cancel operations carry a `message_id` UUID. Retries check the stored message digest before stale-version checks, so retrying an accepted mutation does not execute it again. Follow-ups/answers/decisions also require `expected_task_version`. New stale commands return `409 stale_task_version`. A cancel is safe for the named task without a version precondition; a terminal task returns its existing outcome. Client UUIDs identify operations, not execution attempts or security authority.

## State and result semantics

States are `queued`, `running`, `waiting_for_input`, `cancelling`, `succeeded`, `failed`, `cancelled`, and `interrupted`. `interrupted` requires reconciliation and never automatically launches another attempt. State transitions follow [02-architecture.md](02-architecture.md). Every stored task event increments `task_version`; the event and snapshot change commit together. Cancellation acknowledgment does not prove the worker stopped. A committed success is never overwritten by a late cancel.

Text events stream a run as it happens: `progress` names each tool step, `output_delta` carries the next piece of reply text while the agent writes it, and `output` carries each finished message (bounded to 2,000 characters). Concatenating a task's `output_delta` payloads up to the next `output` or `progress` event gives the full message, which may be longer than the bounded `output` or the result `summary`; clients that do not stream may ignore deltas. One attempt may store at most 1,500 text events and 512 KiB of text payload; beyond that the worker stops the attempt as `interrupted`, so adapters keep their own lower stream budget.

Snapshots expose task/attempt/provider-session identity, state/version, timestamps, optional result, and optional pending input. Results include a concise `summary`, bounded `speech_text`, artifact references, known partial effects, and an optional structured error. Speech must agree with the recorded state. Ordinary questions and approval requests have distinct kinds; approval decisions bind request, task, attempt, action digest, scope, and expiry. No spoken answer or model output can expand authority.

Initial follow-up support accepts an explicit instruction only for a succeeded task with a known idle provider session and advertised continuation capability. The same task becomes queued, its current result clears, and the worker creates a new attempt while preserving the provider session. Prior results remain in events. Running-task steering is unsupported unless advertised; interrupted work requires separate reconciliation and cannot use this route to silently rerun.

## Events and reconnect

Initial delivery uses `GET /api/v1/events?after=0&limit=100` polling. A page contains `events`, `next_cursor`, and `has_more`. Events use globally increasing integer IDs; the cursor advances to the last returned ID. Optional `task_id` filtering still uses global IDs. Empty pages preserve the requested cursor. Unknown future event kinds may be displayed as text or ignored; clients must preserve cursor progression.

An event contains `event_id`, `task_id`, nullable `attempt_id`, `kind`, `timestamp`, `task_version`, and a bounded typed `payload`. Accepted/state/progress/question/approval/result events never contain raw reasoning, credentials, or audio. Examples live under [contracts/examples](../contracts/examples/README.md).

Duplicate event IDs are ignored. Replayed events rebuild task history without replaying old speech. If retention removes the requested history, return `410 cursor_expired` with `details.resync_cursor`. The phone sets that cursor, refetches task snapshots, then resumes events; fetching events after that cursor catches mutations made during snapshot retrieval. A cursor ahead of the server returns `400 invalid_cursor`. Initial prototypes retain all events; retention support is a later release check. WebSocket delivery is deferred and must use the same envelopes/replay rules when added.

## Errors and limits

Errors use `{ "error": { "code", "message", "retryable", "details" } }`. Human-readable messages contain no secrets or arbitrary host paths. Stable codes include `invalid_request`, `unauthorized`, `protocol_mismatch`, `not_found`, `idempotency_conflict`, `stale_task_version`, `invalid_state`, `unsupported_capability`, `cursor_expired`, `invalid_cursor`, `queue_full`, and `storage_unavailable`. Automatic retry is limited to a retryable transport/storage error with the same request ID; never repeat an uncertain agent attempt automatically.

Request bodies are bounded to 64 KiB; instructions/messages to 8,000 characters; echo/speech text to 2,000 characters; event payloads to 16 KiB; pages to 100 records. Reject unknown request fields. Artifacts use server-resolved IDs, never client paths. Validate request identity, lengths, schema, and provider/profile support before launch.

## Parallel implementation boundaries

- Android owns `apps/android/`: M1 speech screen and M2 typed/polling client. SDK/device tests remain separate evidence.
- Gateway owns `services/gateway/`: M2 private API and M4 SQLite lifecycle with a scripted adapter. No real provider starts by default.
- Adapters own `packages/agent-adapters/`: common types, scripted runner, local Claude/Codex integrations, and fixture tests. Real inference waits for a selected account/workspace.
- The coordinating agent owns `contracts/`, root setup, and integration checks. Changes crossing a boundary are communicated before implementation.
