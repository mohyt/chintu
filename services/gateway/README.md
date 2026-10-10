# Chintu gateway: independent M2/M4 fixture slice

Python 3.12+ implements the [shared HTTP contract](../../contracts/openapi.json).
Android sends text; this service has no audio upload or speech provider.
The fixture is useful for transport and lifecycle tests without a provider account.
Claude and Codex are disabled by default. Installing their CLIs does not enable them.

## Install and run

From the repository root, with package access available:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e packages/agent-adapters -e 'services/gateway[test]'
.venv/bin/chintu-gateway --database var/chintu.sqlite3 serve --scripted --contract contracts/openapi.json
```

The CLI always binds `127.0.0.1:8765`, uses one worker, and disables HTTP access logging.
Omit `--scripted` for pairing/discovery/echo with all execution providers disabled.
`--port` selects another local port. Private phone HTTPS/Tailscale Serve configuration
is an operator/device check; do not expose this port publicly. A second worker on the
same database is rejected by an OS file lock. Run as the intended unprivileged owner.

In a separate local terminal, create an enrollment secret:

```sh
.venv/bin/chintu-gateway --database var/chintu.sqlite3 enroll --ttl 300
```

Enter that secret in the phone pairing screen, or open `http://127.0.0.1:8765/pair` in a browser on this machine: it shows a QR code carrying the gateway URL, machine name and a fresh one-time secret for the app to scan. The page, `GET /local/status` (presence summary for the desktop companion) and `POST /local/pairing-code` (fresh QR payload for the companion's own window) answer only direct loopback requests; through Tailscale Serve they return 403. It expires after five minutes and is
consumed transactionally once. Keep it out of URLs and command history. Pairing returns
an opaque bearer token; SQLite stores SHA-256 verifiers of randomly generated secrets
and tokens. `DELETE /api/v1/clients/me` revokes the caller. API requests other than
health/info require `X-Chintu-Protocol: 1`; authenticated routes also require the bearer
credential. The database, its side files, and task text are private local state.

## Test without a phone

```sh
.venv/bin/python -m unittest discover -s services/gateway/tests -v
```

Tests exercise authentication/revocation, concurrent submission retries, conflicts,
commit rollback, queue limits, cursor replay, follow-up versions, cancellation races,
worker exclusion, output limits, and restart interruption. They validate response and
error schemas against the shared contract, use temporary databases, and make no real
inference requests. Test fixtures: normal text succeeds; `[wait]` waits for confirmed
cancellation; `[fail]` fails; `[interrupt]` interrupts. Follow-up requires a succeeded
idle session and creates a new attempt under the same task/session.

## Voice notes

Authenticated `POST /api/v1/notes` opens a note, `POST /api/v1/notes/{id}/chunks` appends one
spoken phrase (up to 4000 characters, 500 chunks), and `POST /api/v1/notes/{id}/finish` writes
`YYYY-MM-DD-HHMM-<id>.md` into the operator's `notes_dir` (top-level config key, `~` allowed;
default `notes/` beside the database; mode 0600) and returns the file name,
word count and text. Notes belong to the pairing client; another client gets 404. Finishing is
idempotent; appending after finish returns 409. No model touches a note; summarising or acting on notes is an ordinary instruction, and the `claude-agent` persona is told where the folder is.

## Candidate computer discovery

Authenticated `GET /api/v1/systems` lists Linux/macOS/Windows computers visible to the
gateway's Tailscale daemon, including this machine. It runs only local
`tailscale status --json` (3-second timeout, 512 KiB maximum, 5-second cache), never
scans the network or contacts a candidate. No Tailscale API key is needed. URLs use
canonical HTTPS `.ts.net` DNS names; unsafe/missing names and unsupported OS entries
are omitted, and at most 100 candidates are returned. Missing/disconnected Tailscale
has an explicit unavailable result instead of an unexplained empty list.

Online means connected to Tailscale's control plane. Chintu may not be installed or
served at the candidate's URL. The phone must verify/pair each host separately and
must never reuse another gateway's bearer token. Initial pairing still needs a known
gateway URL. See the [response contract](../../docs/08-api-contract.md#computer-discovery-and-switching).

The operator-config tests also launch local Claude/Codex **protocol fixture subprocesses**
for success, continuation, process death, reaped cancellation and restart quarantine.
Their evidence flags describe those fake subprocess tests, not real authentication,
billing or Ubuntu confinement. No real provider is selected or invoked by this suite.

## Explicit real-provider configuration

Start from the disabled [example](../../config/gateway.example.json). Keep your local
configuration/review records under ignored `var/`, outside tracked examples:

```sh
mkdir -p var
cp config/gateway.example.json var/gateway.local.json
.venv/bin/chintu-gateway --config var/gateway.local.json check-config
```

Replace the absolute workspace placeholder and choose `inspect` initially. Relative
database/review-file references resolve from the configuration directory. Unknown
fields, relative workspace paths, duplicate IDs, invalid profiles and credential fields
are rejected. `check-config` checks syntax only: it does not create a database, launch
a runtime, read credential storage, verify login, or perform inference. With no
`--provider`, serving a config still enables no real provider, even if its `enabled`
flag was edited.

First complete the adapter's [operator trial](../../packages/agent-adapters/OPERATOR-VALIDATION.md)
using the owner's chosen provider, account method and reviewed confinement boundary.
A fixture trial report does not establish confinement for a production workspace.
Then review authentication/billing, pinned runtime compatibility, selected workspace
confinement, and process cancellation in a separate nonempty local record. Only after
those checks should the chosen runtime entry set `enabled: true`, its `login_method`
to `subscription` or `api_key`, and its `evidence` to the full record:

```json
{
  "version": "<exact tested runtime version>",
  "execution_profiles": ["inspect"],
  "auth_verified": true,
  "protocol_verified": true,
  "confinement_verified": true,
  "cancellation_verified": true,
  "continuation_verified": false,
  "record": "<path to reviewed local evidence file>"
}
```

This is a field illustration, not measured evidence. The executable must be available;
real-provider app startup requires the matching evidence, selected login method,
nonempty review file and existing absolute registered workspace directories. Choosing
a login method does not verify sign-in. Evidence booleans are operator assertions;
configuration does not enforce confinement by itself. `subscription` means ChatGPT
for Codex or Claude.ai for Claude in the trial harness; `api_key` maps directly.
Keep account/billing secrets in the provider's supported local storage.
For this baseline, both subscription and saved API-key selection refuse inherited
authentication/routing overrides, including `OPENAI_API_KEY`, `CODEX_API_KEY`, Codex
access tokens/workload identity, `ANTHROPIC_API_KEY`, Claude OAuth/cloud-provider flags,
endpoint overrides, and alternate auth-storage roots. The guard checks variable names
only, never values, at selection, app creation and before execution. Remove ambiguous
overrides in the operator launch environment; Chintu does not silently strip them or
choose an account. External verification must still establish the actual stored login
and loaded managed settings. References: [official OpenAI environment documentation](https://learn.chatgpt.com/docs/config-file/environment-variables)
and [Claude environment documentation](https://code.claude.com/docs/en/env-vars).

Once reviewed, the explicit commands are:

```sh
.venv/bin/chintu-gateway --config var/gateway.local.json serve --provider codex --contract contracts/openapi.json
.venv/bin/chintu-gateway --config var/gateway.local.json enroll --ttl 300
```

Use `--provider claude` for the separately reviewed Claude entry. Exactly one provider
runs; queued tasks for other providers remain queued. No billing/account fallback is
implemented. Continuation stays unavailable until separately evidenced. Live approval,
clarification resolution and artifacts remain `501`.

## Claude Agent SDK provider

`--provider claude-agent` runs tasks through the Claude Agent SDK with Claude Code's full
tool loop, the host's configured MCP servers, same-session follow-ups and interrupt-based
cancellation. It needs the `claude-agent-sdk` package (`packages/agent-adapters[agent]`)
and a signed-in `claude` executable; it never receives an API key. The runtime block has
`enabled`, `executable`, `model`, `profiles`, `load_user_settings` and optional
`max_turns`/`timeout_seconds` instead of CLI protocol evidence. `desktop` must be listed
explicitly in both the runtime and the project to exist.

```sh
.venv/bin/chintu-gateway --config var/gateway.local.json serve --provider claude-agent
```

Daemon installation (launchd/systemd), pairing, Tailscale Serve, enabling `desktop`, and
which connectors are reachable are described in [deploy/README.md](../../deploy/README.md).

## Durable behavior and limits

Acceptance follows a SQLite `synchronous=FULL` transaction containing the task, event,
and client operation key. Matching retries return the current snapshot of the original
task, before stale-version/capability/queue checks; changed content returns `409`.
Snapshots and events increment versions together. One worker serializes all registered
workspaces. The default pending queue is ten tasks. Bodies are limited to 64 KiB;
instructions to 8,000 characters; pages to 100; event payloads to 16 KiB. Each attempt
has a 128 KiB/256-event progress/output budget. History is retained without pruning in
this prototype; add disk limits and retention/cursor-expiry behavior before daily use.

Running cancellation first records `cancelling`; only a terminal adapter event confirms
stop. Queued cancellation launches nothing, and late cancellation preserves committed
success. On shutdown/startup, unresolved active attempts become `interrupted`; they are
never silently retried. Accepted queued fixture work can start after restart. Provider session
recovery is unavailable. If worker storage fails, execution fails closed until operator
recovery/restart. Physical Ubuntu confinement and crash/process ownership checks,
approvals, clarification resolution and artifacts still require M3/M6/M7 evidence.
Unimplemented controls return `501`, rather than fabricated success.

Every interrupted real-provider attempt quarantines its canonical workspace path.
Startup records unresolved active work as interrupted before any queue claim. Tasks
already queued for that path remain queued, and new work returns `409 invalid_state`;
another project slug pointing at the same directory cannot bypass quarantine. Other
unblocked workspaces may proceed. Workspace remapping cannot silently move accepted
work into another directory.

Stop the gateway, check externally that the affected runtime and its descendants have
stopped, inspect partial effects, and save a nonempty local reconciliation record. Then:

```sh
.venv/bin/chintu-gateway --config var/gateway.local.json reconcile --task-id UUID --confirmed-stopped --record var/reconciliation.md
```

The command refuses to run while the worker lock is held. It records the operator's
assertion and clears that task's quarantine; the file alone cannot prove process stop.
It never relaunches the interrupted task or changes its result/events. After restart,
already queued work may proceed. No remote route clears workspace quarantine.

## Python integration

```python
from pathlib import Path
from chintu_adapters import ScriptedAdapter
from chintu_gateway import Settings, create_app

app = create_app(Settings(database=Path("var/chintu.sqlite3")), adapter=ScriptedAdapter())
secret = app.state.store.create_enrollment()
```

`Settings.projects` maps registered slugs to `display_name`, server-only `path`, and
`execution_profiles`. No client may supply a filesystem path. `enable_worker=False` is
available for isolated storage/transport tests. The contract path defaults to the
repository's shared file; pass `contract_path`/`--contract` when installed elsewhere.

## Verification environment

The current 47 gateway tests pass in the workspace Python 3.12 environment with fresh
SHA-256-verified wheels, FastAPI 0.141.1 and Starlette 1.3.1. The six `claude-agent`
tests use an in-process fake SDK module; one real inspect-profile call was run separately
through `scripts/demo_agent_sdk.py` on 10 October 2026. The initial fixture tests
also passed using read-only caches when direct dependency downloads failed DNS. These
framework versions are pinned. No real login, billing, confinement, provider inference,
Ubuntu/Tailscale or device check ran. Starlette's existing `httpx` TestClient emits a
deprecation warning recommending `httpx2`; the installed `httpx` path passed the tests.
