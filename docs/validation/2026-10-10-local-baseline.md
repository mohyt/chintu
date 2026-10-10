# Local implementation evidence

10 October 2026 · contract v1.0.0 · development baseline

This records the initial fixture baseline. See [build and integration readiness](2026-10-10-build-readiness.md) for subsequent fresh installation, full specification validation, and provider configuration work.

## Environment and scope

Checks ran on the current macOS development machine with Python 3.12.3 and JDK 21. Python framework versions were FastAPI 0.141.1, Starlette 1.3.1, httpx 0.28.1, and jsonschema 4.25.1. Dependencies came from an existing read-only local cache because package downloads failed DNS resolution. A fresh `uv sync` installation has not been verified.

Three parallel implementation agents worked on Android, the gateway, and adapters after the HTTP and local adapter contracts were written. The coordinating agent added shared schemas, examples, root setup, and independent wire checks. No deployment, real inference, account changes, or commits occurred.

## Observed results

| Check | Observed result | What it establishes |
| --- | --- | --- |
| Contract checker | Passed 27 schema definitions, references/operations, five wire examples, five invalid payload checks | Internal contract consistency and example validity |
| Gateway suite | 19 tests passed | Durable acceptance, retries, authorization, queue limits, cancellation, follow-ups, recovery, and worker ownership with fixtures |
| Adapter suite | 23 tests passed | Scripted behavior and local subprocess protocol/limits/cleanup using fake runtimes |
| Independent API integration | Six tests passed | Gateway responses/events match shared schemas, replay preserves identity, follow-up/cancel/revocation behave as specified |
| Android JVM core suite | Passed | Persisted request identity, atomic acceptance identity, failed persistence/restart recovery, event deduplication, silent catch-up, and cursor resynchronization |
| In-process demo | Recorded `succeeded`; returned `Scripted reply: Summarize this project` | Pairing → text submission → durable task → speech-ready text with no-effects fixture |

The normal repository entry points are `uv run make test` and `uv run make demo` after dependency installation. These checks used equivalent source paths and the cached libraries. The test client emitted an httpx deprecation warning; checks completed successfully.

Final integration review fixed acceptance persistence before clearing the phone's retry request and reduced phone polling to ten tasks/twenty events per page within its 1 MiB response ceiling. Focused core and wire checks passed after the fixes. Future approval/artifact snapshot sizes need a separate bound review.

## Phase status and remaining gates

- **M0:** Android device/language, Ubuntu/Tailscale host, fixture workspace, and provider authentication/billing choices remain unconfirmed.
- **M1:** Android speech/client source and core checks are present. APK compilation, lint, permissions, Bluetooth routing, offline engine availability, and physical-device behavior remain untested. Minimum Android 12 is provisional.
- **M2:** Local API/authentication and client source are present. Actual phone-to-Ubuntu private HTTPS, Wi-Fi/cellular reconnection, and enrollment on the target devices remain untested.
- **M3:** Scripted and subprocess foundations pass fixtures. Read-only CLI checks found Codex 0.162.0 and Claude Code 2.1.296. Authentication, live JSONL/session compatibility, Ubuntu confinement, and real cancellation remain unverified; real providers are disabled by default.
- **M4:** Fixture lifecycle/durability checks pass. Approval/clarification/artifact operations return explicit unsupported errors; approval expiry and real worker reconciliation are not established. History retention/backup and storage exhaustion remain release work.
- **M5–M8:** End-to-end real voice tasks, complete provider controls, daily reliability, and hands-free behavior have not passed acceptance gates.

Full OpenAPI specification validation could not run because its validator was unavailable. Android SDK/Gradle and dependency fetching were unavailable, so no APK or Android lint pass is claimed. Follow-up execution preserves the session and prior result events; the current API does not expose a complete follow-up transcript. These local results do not establish V1 readiness.
