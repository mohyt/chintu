# Agent integration and contracts

Design v0.2 · 10 October 2026 · implementation baseline. The interfaces below describe required behavior; current endpoint support is recorded separately.

## Integration boundary

Chintu delegates to agent runtimes on Ubuntu. Those runtimes execute local tools and contact model providers. Chintu owns the user-facing task, identity, lifecycle, and history; each provider owns its opaque internal conversation state.

The adapter boundary avoids making a desktop app's visible window part of the control protocol. If continuing a particular existing Claude Desktop or ChatGPT conversation is a hard requirement, resolve Q3 before implementation. A CLI or SDK session must not be presented as an imported desktop chat.

## Provider strategy

| Provider path | Proposed use | Constraint to validate |
| --- | --- | --- |
| Claude Code programmatic CLI | First compatibility spike and bounded local tasks | Supported login, structured output, continuation, tool limits, and behavior when extra approval is required |
| Claude Agent SDK | Implemented as the `claude-agent` provider (D21): full built-in tool loop, the owner's configured MCP servers, same-session follow-ups, and interrupt-based cancellation | Uses the owner's own Claude Code login on the host; Anthropic restricts offering claude.ai login inside third-party SDK products, so this stays a single-owner integration and billing remains Q4 |
| Codex SDK | Bounded jobs and reusable local sessions | Installed/pinned runtime compatibility, authentication, and required streaming features |
| Codex local app-server | Detailed conversation, approval, and streaming integration | Version-specific schema; connect locally using supported transport |

Official Claude documentation describes programmatic CLI execution and SDK permission callbacks. It directs third-party SDK integrations to supported API authentication unless separately approved. Codex provides an SDK and app-server interfaces, and its documentation distinguishes local/open-source authentication from commercial hosted integration. See S5–S9.

Do not extract login tokens from one application and inject them into another. Do not silently fall back from a subscription-based workflow to paid API usage or another provider. Provider/billing choice is visible configuration.

### Claude Agent SDK provider

`packages/agent-adapters/src/chintu_adapters/agent_sdk.py` implements `claude-agent`. The gateway selects it with `serve --provider claude-agent` when `runtimes.claude-agent.enabled` is true; no CLI protocol evidence record is required because the SDK owns the transport. Each attempt opens one `ClaudeSDKClient` in the registered workspace, sends the instruction over the SDK stream (never argv), and normalizes `SystemMessage(init)` to `started`, assistant tool calls to bounded `progress`, assistant text to `output`, and the `ResultMessage` to `succeeded`/`failed`. A follow-up passes the stored session ID as `resume`. Cancellation calls `interrupt()` and reports `cancelled` only after the session ends; a stream that ends without a result is `interrupted`.

| Profile | SDK permission mode | Built-in tools | Prompted actions |
| --- | --- | --- | --- |
| `inspect` | `dontAsk` | Read, Glob, Grep, WebSearch, WebFetch | Denied |
| `develop` | `acceptEdits` | Claude Code preset; Edit, Write, Notebook, Bash pre-approved | Denied |
| `desktop` | `bypassPermissions` | Claude Code preset plus loaded MCP servers | Denied (only `AskUserQuestion`, org-`ask` connectors, and critical-path removals still prompt) |

`desktop` exists only when listed explicitly in both the runtime block and the project. `load_user_settings` loads the owner's user/project Claude Code settings so MCP servers configured on that host are available; claude.ai and Claude Desktop cloud connectors are not reachable from the SDK. See [deploy/README.md](../deploy/README.md) for daemon operation.

Codex's public WebSocket app-server transport is currently documented as experimental. The proposed gateway uses a local integration, preferably stdio, and provides Chintu's own authenticated remote contract. Pin versions and verify actual capabilities during the spike. See S8.

## Adapter capability contract

Each adapter advertises capabilities at startup, based on verified runtime support rather than provider-name assumptions.

| Operation | Required behavior |
| --- | --- |
| Inspect capabilities | Report supported continuation, active steering, approvals, cancellation, session recovery, and tool restrictions |
| Validate task | Check project, requested execution profile, available provider/model, and required features before queueing |
| Start attempt | Establish one provider session and emit its identifier before irreversible work where the provider allows this |
| Stream events | Normalize observable progress, output, questions, permission requests, errors, and completion |
| Continue task | Reuse the correct session; distinguish a new turn from steering an active turn |
| Answer question | Deliver a bounded answer to a specific outstanding request |
| Resolve approval | Resume only the requested action under the approved scope, or return an explicit unsupported capability |
| Interrupt | Request provider cancellation, then reconcile actual process/session status |
| Reconcile | Establish known running/completed/failed/interrupted state without blindly launching another run |
| Collect result | Return a concise summary, output references, exit/error evidence, and known partial effects |

If a non-interactive CLI cannot support a live approval callback, it must not hang indefinitely or run with globally bypassed permissions. Either execute a pre-authorized bounded task, return a blocked result with a clear reason, or use the approved richer adapter. V1 is not complete until its claimed approval behavior works with both selected adapters.

Use structured process arguments rather than shell-interpolating the spoken instruction. Project IDs resolve server-side to canonical registered directories. Prompts are data. Session IDs and raw model output never become executable commands by string concatenation.

## Conceptual application interfaces

| Interface | Purpose | Semantics |
| --- | --- | --- |
| Enroll/revoke client | Establish or remove access | Desktop-authorized enrollment; explicit revocation |
| List projects/capabilities | Show what this host can do | Returns only owner-authorized configuration |
| Submit task | Start new work | Idempotency key required; acknowledges only after durable commit |
| Get/list tasks | Read state and bounded history | Includes task version and timestamps |
| Continue task | Supply a new instruction to an existing task | Idempotent message ID; active-turn steering only if adapter supports it |
| Answer input request | Resolve ordinary clarification | Request ID and task version required |
| Decide approval | Grant or deny a specific action | Bound to client, task, attempt, action digest, scope, and expiry |
| Cancel task | Stop pending or active work | Idempotent; returns current state, not a premature success claim |
| Subscribe to events | Follow live progress or recover history | Cursor-based replay and snapshot fallback |
| Select/attach conversation | Associate phone interaction with project/task | Speech-session state stays on Android; no server audio connection |
| Retrieve artifact | Fetch an output | Opaque artifact ID; no arbitrary host file path in the request |

Concrete route names, JSON schemas, limits, and version negotiation are defined in [08-api-contract.md](08-api-contract.md) and [OpenAPI](../contracts/openapi.json). Initial event delivery uses REST polling. These behaviors remain the compatibility contract; clients check the protocol before processing events.

## Task submission and event fields

| Message | Fields |
| --- | --- |
| New task | Client-generated idempotency key, project ID, provider choice, instruction, execution profile, conversation ID, optional task title |
| Accepted response | Task ID, state, task version, initial event cursor, selected provider/project |
| Follow-up | Message ID, task ID, expected task version, instruction, referenced input request if applicable |
| Event envelope | Event ID, task ID, attempt ID, kind, timestamp, task version, bounded payload |
| Approval request | Request ID, proposed action summary, normalized action digest, requested scope, expiry, available decisions |
| Result | Task/attempt IDs, final state, human summary, artifact IDs, known partial work, structured error if any |

Use server timestamps for ordering within records and a database-generated event cursor for replay; phone clock accuracy is not a consistency mechanism. The same idempotency key with different canonical request content is a conflict. It must never modify the original task or create a second task silently.

Minimum normalized event kinds are task accepted, started, progress, output, input requested, approval requested, approval resolved, cancellation requested, completed, failed, interrupted, and artifact created. High-volume output may be coalesced or stored as an artifact; state transitions and approval decisions must never be dropped.

## Continuing, retrying, and changing provider

An idle session can accept a new turn if the adapter supports resume. An active session accepts steering only when documented and verified; otherwise the message is held visibly or the user is asked whether to cancel and continue later. Do not start a second runner against the same active provider session.

A retry creates a new attempt only after the prior attempt's effects and ownership are known. Idempotent application requests do not make file edits, network requests, or model-driven commands exactly-once operations.

Switching provider creates a new task. The user sees the summary and selected files/context to carry over. Provider-internal state, credentials, and hidden reasoning are not transferred. V1 does not require an automated provider handoff feature.

## Permission model

| Profile | Intended permission scope |
| --- | --- |
| Inspect | Read approved project content with tool and sandbox controls; no arbitrary executable project hooks assumed safe |
| Develop | Edit within an agreed workspace and run explicitly permitted development operations |
| Desktop | Trusted, prompt-free execution with all built-in tools and the host's configured MCP servers; opt-in per runtime and per project (`claude-agent` only) |
| Extended tools | Additional browser, connector, or external actions only after their specific setup and grants |

Permission profiles describe enforceable runner settings, not prompt text. A configured working directory is not filesystem isolation. If an adapter cannot provide the requested restrictions, the gateway rejects that profile or requires a documented alternative environment.

The user may grant a task-scoped permission in advance. The gateway preserves that decision across ordinary steps and reconnection. A new approval applies only to the request shown, cannot expand itself through model output, and cannot be reused by a different task or attempt. Detailed enforcement and revocation are in [05-security-and-operations.md](05-security-and-operations.md).
