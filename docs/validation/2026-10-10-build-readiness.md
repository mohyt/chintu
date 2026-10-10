# Build and integration readiness

10 October 2026 · follow-up to [the local baseline](2026-10-10-local-baseline.md)

## Fresh Python installation

The ordinary `uv sync` request failed DNS resolution. Approved `curl` downloads succeeded, allowing a fresh installation without reusing the previous dependency environment:

1. Downloaded 33 release wheels from PyPI, verified each SHA-256 against release metadata, and generated `requirements-dev.lock` with pinned versions/release hashes.
2. Installed into a new workspace `.venv` using `uv pip sync --no-index --find-links <temporary-wheelhouse> --require-hashes requirements-dev.lock`.
3. Built and installed both local editable packages using `uv pip install --no-index --no-build-isolation --no-deps -e packages/agent-adapters -e services/gateway`.
4. `uv pip check` passed for all 35 installed packages. Python is 3.12.13; OpenAPI validator is 0.7.2.

This establishes a fresh macOS installation using verified public artifacts. Normal package-index connectivity and Ubuntu installation remain separate checks. `make bootstrap` documents the equivalent network-enabled installation path.

## Checks and implementation

- Full OpenAPI 3.1 specification validation passed, alongside the existing 27 schema/five wire example/five invalid-payload checks.
- Checks passed: 31 gateway tests, 40 adapter/readiness tests, six independent HTTP wire checks (77 Python tests total), Android JVM core scenarios, and the scripted in-process demo. The changed gateway/config and wire suites passed again after the authentication-route guard was added.
- Strict operator configuration supports explicit reviewed provider selection; the example validates with all providers disabled and no database or inference side effect.
- Real-provider activation/execution reject inherited authentication/routing environment overrides using variable names only. Selected sign-in/storage/managed settings still require external review.
- Fake Claude/Codex subprocesses validate gateway lifecycle, continuation, confirmed cancellation, selected-provider isolation, and canonical workspace quarantine. Interrupted work is not automatically relaunched.
- The readiness harness creates disposable fixtures, inspects sanitized CLI status, and supports explicitly authorized Ubuntu trials. Trial measurements never automatically create production evidence or activate the gateway.
- A local setup inventory command and a pinned hosted verification workflow are present. Workflow YAML/action pins were checked locally; the workflow has not run or been published.

## Android tooling

Gradle 8.11.1 and Android command-line-tools 12.0 archives were downloaded from official sources, checked against published hashes, unpacked under ignored `apps/android/.tooling/`, and started for tool diagnostics. The [tooling helper](../../apps/android/tools/android.py) keeps caches local, handles preinstalled SDK/Gradle, and never accepts SDK terms automatically.

Gradle startup failed with `java.net.SocketException: Operation not permitted` because this execution sandbox disallows the daemon's local socket. A no-daemon/minimal-JVM attempt also failed. SDK Platform 35, Build Tools 35.0.0, platform-tools, and the SDK license record are absent. No APK compilation or lint pass is claimed. The hosted workflow uses the runner's preinstalled SDK and can perform those checks once run in a repository.

## Target setup remains pending

Read-only status inspection observed Codex 0.162.0 logged in using ChatGPT. Claude 2.1.296 reported `loggedIn: false`. These are local status observations, not verified inference, account entitlement, billing, or Ubuntu runtime evidence. No credentials or raw status data were saved. The Tailscale CLI failed to load preferences, so no connected tailnet or Ubuntu peer was established.

The phone model/Android version, speech language/Bluetooth device, Ubuntu host/tailnet, chosen provider/account, and tested execution boundary are still needed. No device was installed, remote host configured, live provider invoked, or permission setting changed. Continue with [setup and the first device demo](../09-setup-and-first-demo.md) once the actual targets are available.
