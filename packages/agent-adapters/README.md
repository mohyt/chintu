# Chintu agent adapters

Python 3.12+ local library implementing [adapter-protocol.md](../../contracts/adapter-protocol.md).
Uses the standard library at runtime. Importing the package never launches a provider or reads credentials.

## Independent checks

From the repository root:

```sh
PYTHONPATH=packages/agent-adapters/src python3 -m unittest discover -s packages/agent-adapters/tests -v
```

Tests copy a local Python fixture into a temporary directory and launch it as a
subprocess. They exercise the actual JSONL reader, stdin/argv separation, session
continuation, output limits, version checks, timeouts, cancellation and cleanup.
They do not call Claude/OpenAI, require login, or bill model usage.

For gateway imports, include `packages/agent-adapters/src` in `PYTHONPATH`, or
install the package in your chosen virtual environment with
`python3 -m pip install -e packages/agent-adapters` (setuptools may need fetching).

## Stable interface

```python
from chintu_adapters import ScriptedAdapter, TaskCommand

adapter = ScriptedAdapter()
command = TaskCommand(task_id, attempt_id, registered_project_path, instruction)
async for event in adapter.run(command):
    # The gateway persists the event and owns authorization/state transitions.
    persist(event)
```

`AdapterCapabilities`, `TaskCommand`, and `AdapterEvent` are frozen dataclasses.
`run` yields normalized events; `await adapter.cancel(attempt_id)` requests a
stop. Only a later `cancelled` event confirms stopping. Terminal payloads use
OpenAPI `TaskResult`, including an array of `partial_effects`.

## Scripted fixtures for M4

`ScriptedAdapter()` has no constructor arguments, filesystem operations or tools.
It advertises `inspect`/`develop`, continuation and cancellation. Session IDs are
deterministic UUIDv5 values derived from the task ID; a supplied follow-up session
ID is preserved. Exact trimmed fixture instructions are:

| Instruction | Outcome |
| --- | --- |
| Any ordinary text | `started`, `progress`, `output`, `succeeded`; reply echoes bounded input |
| `[wait]` | Remains active until cancellation; then `cancelled` with no effects |
| `[fail]` | `failed` with `fixture_failure` |
| `[interrupt]` | `interrupted`; never automatically retries |

Unknown cancellation is a no-op. Duplicate active attempt IDs fail. Approvals,
active steering and session recovery are unavailable; these fixtures do not prove
real provider control or desktop-chat import.

## Claude Agent SDK adapter

`ClaudeAgentAdapter` (provider `claude-agent`) runs the Claude Code tool loop through the
`claude-agent-sdk` package, installed with the `agent` extra. The SDK is imported lazily;
without it, or without a `claude` executable, `capabilities()` reports unavailable with a
reason. Profiles map to SDK permission modes and a workspace hook: `inspect` is `dontAsk`
with Read/Glob/Grep, `develop` is `dontAsk` with file tools and Bash, both confined to the
registered workspace by the PreToolUse hook in `confinement.py` (no bare `allowed_tools`;
the Bash check is a textual filter, see `deploy/README.md`), and `desktop` is
`bypassPermissions`, must be listed explicitly, and logs a warning. Follow-ups resume the SDK session;
`cancel` sends the SDK interrupt. `tests/test_agent_sdk.py` uses `tests/fake_agent_sdk.py`,
an in-process stand-in, so no inference or network is involved.

## Claude and Codex readiness

Use the [Ubuntu operator validation guide](OPERATOR-VALIDATION.md) for the explicit
readiness/trial harness. Its known-file, continuation and cancellation tests run
only after account selection and reviewed boundary evidence; it never writes
production activation flags. The same harness is tested with fake subprocesses.

`ClaudeAdapter()` and `CodexAdapter()` default to **unavailable**, even when their
executables are installed. The gateway must never silently activate them.
`RuntimeEvidence` is an operator-maintained record requiring a pinned version,
chosen/authenticated account, protocol fixtures, tested filesystem/tool/network
confinement, and verified cancellation. Continuation needs separate evidence.
Setting evidence flags is not confinement and does not prove those checks passed.
Profiles not named in reviewed evidence are rejected before launch. Auth and billing
selection must be resolved locally before real-provider tests; no account fallback
or credential extraction is implemented.

With reviewed evidence supplied, the adapter probes `--version`/`--help` on each
run and rejects version/flag mismatch. POSIX process groups are required. A prompt
travels only through stdin, with explicit argv and the registered directory as
cwd. Cwd alone does not isolate files. Operators must test loaded managed settings,
runtime tools, network access, child processes, and session-resume behavior within
the actual confinement environment. Processes that escape their process group
need containment supplied by that environment; fixture tests do not establish it.

The Claude CLI uses restricted/safe mode, explicit tools, denied interactive
permission prompts and no MCP tools. Permission denials produce failure; live
approval resolution is unsupported. Codex uses noninteractive JSONL, an explicit
read-only/workspace-write sandbox and `approval_policy="never"`, with user config
and rule loading disabled. Denied actions stay denied. Neither uses permission or
sandbox bypass flags, and neither claims active steering/approval/session recovery.
This is a bounded CLI integration foundation, not the final M6 approval adapter.

Output is capped per line (64 KiB), per stream (2 MiB default), per event text
(4,000 UTF-8 bytes), and per run (10 minutes default). Stderr is drained and discarded;
provider diagnostics, raw reasoning and tool arguments are not exported. A confirmed
provider terminal result is held until process exit. Lost/malformed output,
conflicting exit status or timeout becomes `interrupted`; inspect the workspace
for possible effects. Cancellation interrupts the process group, escalates and
reaps before emitting `cancelled`; it does not undo completed effects.

## Evidence and official sources

Read-only local inspection on 10 October 2026 found `codex-cli 0.162.0` and
Claude Code `2.1.296`; their version/help commands ran. No model request,
authentication check, billing test, Ubuntu confinement test, or phone test ran.
Real adapters remain disabled pending those compatibility checks.

Protocol implementation was checked against official
[Codex noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference),
[Claude programmatic CLI](https://code.claude.com/docs/en/headless), and
[Claude CLI reference](https://code.claude.com/docs/en/cli-reference).
The installed versions and fixtures are separate evidence from these documentation
claims. Pin and rerun compatibility tests before enabling a real runtime.
