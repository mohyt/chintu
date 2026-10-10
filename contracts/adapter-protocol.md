# Internal adapter contract

Version 1.0.0. Package: `chintu_adapters`, Python 3.12+. This is a local library contract, not a remotely exposed provider protocol.

Export frozen dataclasses from `chintu_adapters`:

- `AdapterCapabilities(provider: str, available: bool, execution_profiles: tuple[str, ...], continuation: bool, cancellation: bool, approvals: bool, active_steering: bool, session_recovery: bool, reason: str | None = None)`.
- `TaskCommand(task_id: str, attempt_id: str, project_path: str, instruction: str, execution_profile: str = "inspect", provider_session_id: str | None = None)`.
- `AdapterEvent(kind: str, payload: dict, provider_session_id: str | None = None)`.

An adapter exposes `capabilities() -> AdapterCapabilities`, `run(command: TaskCommand) -> AsyncIterator[AdapterEvent]`, and `async cancel(attempt_id: str) -> None`. Cancellation delivers an interrupt; it never asserts terminal success before a terminal event. `run` yields normalized `started`, `progress`, `output`, `input_requested`, `approval_requested`, `succeeded`, `failed`, `cancelled`, or `interrupted` events. Final result payloads use the HTTP `TaskResult` fields. Unexpected process loss is `interrupted`; an intentional cancellation is `cancelled` only after execution stop is confirmed.

`started` identifies the provider session through `provider_session_id`. The task manager owns task IDs, attempt IDs, event IDs, versions, durable state, and authorization. Adapters never manufacture those durable fields or mark the HTTP request accepted. Progress/output payloads use `{ "text": <bounded text> }`; final payloads use `{ "summary", "speech_text", "artifact_ids", "partial_effects", "error" }`. Input payloads match `PendingInput` in OpenAPI; only the task manager stores and authorizes resolutions.

The scripted adapter advertises provider `scripted`, inspect/develop fixture profiles, continuation and confirmed cancellation; it has no real filesystem/tool effects. Define deterministic documented fixture instructions and emit stable session IDs for follow-up tests. Approvals/active steering/recovery must be false until actually implemented. Real adapters may advertise unavailable with a reason; executable presence alone does not prove auth, permissions, or supported protocol compatibility. They must never bypass permissions or silently choose billing.

The `claude-agent` adapter wraps the Claude Agent SDK. It advertises the profiles listed in its configuration, a subset of `inspect`, `develop`, and `desktop`; `desktop` maps to the SDK's `bypassPermissions` mode and must be listed explicitly. It never passes credentials, resumes a session from `provider_session_id`, and emits `cancelled` only after `interrupt()` ends the session. Permission prompts are denied, not answered, because approvals are unavailable.

Gateway injection accepts an adapter instance; its own fixture transport tests must not require a real provider account. Provider credentials remain in supported provider storage. Real process arguments are passed as argv, never shell-interpolated instruction strings. Real inspect/develop capabilities require tested runtime confinement; unsupported profiles must fail before launch.
