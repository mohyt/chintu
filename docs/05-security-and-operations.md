# Security and operations

Design v0.2 · 10 October 2026 · proposed

## Operating boundary

Chintu is a single-owner personal tool that can access local project data and perform real desktop work. The security design must preserve the owner's selected scope without turning every normal agent step into a new confirmation.

All custom inbound access is through the private Tailscale endpoint. Bind the gateway to loopback behind Tailscale Serve and restrict tailnet access to the intended owner/device policy. Serve supports HTTPS and tailnet access rules; no public publishing is part of this plan. See S3 in [the source ledger](07-decisions-and-sources.md).

## Pairing and application authorization

1. The owner initiates pairing locally on Ubuntu.
2. The desktop presents a short-lived, single-use enrollment secret over a local setup surface.
3. The phone connects through Tailscale and exchanges that secret for its own revocable application credential.
4. Ubuntu records the enrolled client and stores a verifier rather than logging the credential.
5. The phone keeps its credential in platform-protected storage. Provider credentials remain off the phone.

Enrollment secrets require expiry, attempt limits, and consumption after a successful exchange. Tailscale restricts network reachability; application authorization identifies the client allowed to issue Chintu requests. A request body containing a client ID is not proof of identity.

Check authorization on every control request and event/artifact subscription. Revocation prevents new requests, closes the client's live connections, and resolves pending permission requests safely. Existing task handling after revocation is an explicit owner policy: the proposed default is to prevent new privilege grants and keep existing authorized tasks observable locally, with a separate stop-all control.

If Tailscale identity headers are consumed, accept them only through the loopback Serve proxy and never trust the same header supplied through another listener. Do not accept arbitrary browser origins. Redact tokens, credentials, and enrollment data from logs.

## Action authorization

Prior task-scoped grants remain valid within their stated scope. An agent may work autonomously under those grants. Requests for additional access identify the task, attempt, proposed action, scope, and expiry.

Bind an approval to a normalized digest of the exact pending action. Revalidate immediately before execution. If the action or target changes materially, the earlier approval does not cover it. Reject expired, replayed, duplicate-conflicting, wrong-task, or wrong-client decisions. Expiry never means approval.

V1 permission elevation uses an unlocked authenticated phone UI. Voice can resolve ordinary questions but does not establish new execution authority. This avoids treating nearby audio or an agent's own playback as the owner's authorization.

## Execution boundary

Run Chintu as a normal Ubuntu user. Do not grant it root as a convenience. Provider processes inherit only needed credentials and tool configuration. A task selects a registered project and a tested permission profile.

Path validation includes canonical resolution and an explicit policy for symlinks. The gateway's project allowlist does not, by itself, confine a subprocess that can access the rest of the user's files. Actual confinement depends on the provider runner's sandbox and/or an OS isolation environment. Validate that boundary with an attempted out-of-scope read/write before claiming a profile is restricted.

If native runtime controls do not enforce the desired profile, use a dedicated execution identity or a tested container/sandbox with explicitly mounted workspaces. GUI access and credential stores require separate treatment in that environment. This is a release gate, not an assumed feature of setting a working directory.

Project content can include executable hooks and agent instructions. Read-only inspection must not automatically execute unreviewed project startup hooks or broad plugins. Load only intentionally configured integrations for the initial restricted profile. Imported web pages, repository files, and tool output cannot change owner grants.

## Credentials and privacy

Use each provider's supported sign-in or API credential mechanism. Keep secrets in the OS/provider credential store or a deliberately configured restricted secret file where supported. Do not commit credentials, export desktop session tokens to the phone, or include them in backups of the source project.

Raw audio is transient on Android, absent from ordinary logs, and never transmitted to Ubuntu. Select supported on-device recognition and offline TTS voices where available; explicitly configure any network-backed Android speech service and document its data boundary. No OpenAI speech API is used. Retain task instructions, concise results, and operational events according to a configurable policy. Proposed initial retention is 30 days for completed-task history and 7 days for bounded diagnostic output, with artifacts kept until the owner deletes them. Never prune active tasks or unresolved approval records needed for reconciliation.

Deleting Chintu's local history does not imply deletion of provider-retained data. The final provider choice should include its applicable data controls. No provider-specific retention guarantee is asserted in this design.

## Ubuntu service lifecycle

The application runs as a systemd user service, supervised separately from individual tasks. Confirm the target Ubuntu release, Python/runtime requirements, existing Tailscale installation, and provider availability during M0.

User-service startup without an interactive desktop login and operation after logout depend on the machine's chosen systemd/login configuration. Establish this explicitly during setup; do not assume a user service alone guarantees boot-time availability. Preventing suspend is an owner-controlled machine policy, not something an agent silently changes.

Before dispatch, verify that the desktop, database, workspace, and selected runtime are usable. Provider failure should leave the application available for history, cancellation, or another explicitly chosen task. Local browser/GUI work additionally depends on a usable logged-in graphical session and supported tools.

## Failure handling

| Failure | Intended response |
| --- | --- |
| Phone or Tailscale disconnects | Keep accepted work running; replay events on reconnection |
| Desktop asleep/offline | Show stale history and unavailable host; retain an unsent draft rather than claim acceptance |
| Android recognition or TTS unavailable | Show the speech error; retain typing, task results, and existing jobs |
| Agent authentication expired | Fail or block the affected run with a sign-in requirement; never expose credentials in the error |
| Provider throttling | Bounded retry only where execution has not started or the provider supports safe retry; show the delay |
| Gateway restart | Reconcile workers and attempts before dispatch; do not blindly rerun in-progress tasks |
| Desktop reboot | Preserve history; mark uncertain attempts interrupted until reconciled |
| Database/disk failure | Stop accepting new jobs before acknowledgment; preserve observable errors and avoid untracked execution |
| Oversized output | Bound memory, preserve state events, store a bounded artifact or explicit truncation marker |
| Cancellation timeout | Report cancelling/interrupted state; confirm process-group termination before claiming cancellation |

## Resource bounds and observability

Start with one active task, a queue limit of ten, bounded per-task output, and configurable maximum run time and approval-wait time. Show limits in configuration and task status. A default 30-minute run budget and 15-minute unanswered-input expiry are proposals to validate against actual workflows, not hard product requirements.

Measure gateway acknowledgment latency, recognition finalization, end-of-speech to first TTS audio, provider startup latency, task duration, reconnect recovery time, phone speech-queue depth, cancellation latency, and approval wait time. Record task and attempt IDs consistently. Use local structured logs initially; an external observability service is optional.

API spending caps are enforced only to the extent provider and adapter telemetry permit. Token usage is not universally available for subscription sessions. Report unavailable cost estimates as unavailable. Do not claim a hard currency cap from an approximate token counter; use provider-level budgets where supported.

## Backup and upgrade plan

Keep design and future source under the project home. Runtime data lives in `var/` and is excluded from source control. Take consistent SQLite backups using an appropriate database backup mechanism; copying a live main database file alone is insufficient when auxiliary journal files are in use. Back up artifact metadata and files consistently.

Pin provider runtimes and schemas. Test upgrades on a small read-only task and an approval/cancellation contract suite before daily use. Database migrations require a backup and a documented rollback or forward-recovery plan. Restore testing must show history survives without automatically replaying side effects.
