# Chintu

**C**onversational **H**ands-free **I**nterface to **N**etworked **T**erminals & **U**tilities. Your voice. Your computers.

Talk to local Claude/Codex tasks from an Android phone. Android handles speech recognition and speech playback; the private Ubuntu gateway handles text requests, task execution, and history. No OpenAI speech API is used.

The [Chintu experience](docs/10-chintu-experience.md) uses a minimal purple-orb home screen, automatic Bluetooth detection with phone fallback, a call-style audio selector, and named computers on Tailscale. Say “Chintu, switch to my Mac” to change the destination after pairing each computer. See [redesign verification](docs/validation/2026-10-10-experience-redesign.md) and [Android installation](apps/android/README.md).

## Install

Packages are published on [GitHub Releases](https://github.com/mohyt/chintu/releases): the Android APK, a macOS disk image with the menu-bar app and daemon, and Debian packages for Ubuntu 24.04. Install the desktop app on each computer, pair the phone from its QR window, and talk. On Ubuntu/Debian (amd64, arm64) or an Apple silicon Mac, one command picks the right package, verifies its SHA-256 and starts the tray:

```sh
curl -fsSL https://raw.githubusercontent.com/mohyt/chintu/main/install.sh | bash
```

`CHINTU_VERSION=0.4.0` pins a release. See [packaging and releases](docs/11-packaging-and-releases.md) for the caveats and for building the packages yourself.

What the signatures mean: the APK is signed with the maintainer's long-lived release key, so updates install over each other. The macOS app is ad-hoc signed and not notarized, so the first launch needs right-click → Open. The Debian packages are unsigned; check the checksums GitHub shows beside each release asset. Tailscale and a Claude Code sign-in are installed separately on each computer. Security reports: see [SECURITY.md](SECURITY.md).

## Contract first

- [API guide](docs/08-api-contract.md): routes, authentication, retries, errors, and event replay.
- [OpenAPI 3.1 contract](contracts/openapi.json): authoritative JSON fields and schemas.
- [Adapter protocol](contracts/adapter-protocol.md): local runner interface shared by the gateway and providers.
- [Delivery plan](docs/06-delivery-plan.md): independently testable phases and pass criteria.
- [Setup and first device demo](docs/09-setup-and-first-demo.md): concrete build, speech, transport, and provider sequence.

The first prototype baseline is implemented. See [build readiness and remaining device gates](docs/validation/2026-10-10-build-readiness.md). Passing local fixture tests does not establish real provider authentication/confinement, Android compilation, physical-device audio behavior, or Ubuntu/Tailscale operation.

## Components

| Path | Responsibility |
| --- | --- |
| `apps/android/` | Android speech experiment, typed client, and task UI |
| `apps/desktop/` | macOS menu bar / GNOME panel companion: phone presence, pairing QR, gateway supervision |
| `services/gateway/` | Private API and durable SQLite task handling |
| `packages/agent-adapters/` | Scripted fixtures and local provider integration |
| `contracts/` | Shared wire contract and synthetic examples |
| `deploy/` | launchd/systemd units and daemon operation notes for the Claude Agent SDK provider |
| `docs/` | Requirements, architecture, decisions, delivery, and API guide |

Component READMEs describe their own setup, checks, and remaining limitations. Device/account choices are configured explicitly; the prototype does not deploy itself or start real provider inference by default.

## Local development

Python 3.12+, `uv`, and a JDK are needed for the local checks. Dependency downloads require network access. Android compilation additionally requires its SDK and the Gradle version documented in [the Android README](apps/android/README.md).

```sh
make bootstrap
make PYTHON=.venv/bin/python test demo
```

`make bootstrap` installs the hash-pinned Python 3.12 development dependencies from `requirements-dev.lock`, then builds/installs the two local packages without fetching build dependencies. It can require network access. The lock contains release hashes for platform-specific wheels; fresh installation has been checked on macOS, while Ubuntu verification is configured in [CI](.github/workflows/verify.yml). `uv sync` remains available for ordinary dependency resolution, but does not consume this requirements lock.

`make test` runs contract, gateway, adapter, integration, and Android core logic checks. It does not build an APK or run physical-device tests. `make demo` demonstrates pairing and a text task entirely in-process with a scripted no-effects adapter; it prints no credentials and starts no provider inference.

## Contract verification

With dependency downloads available:

```sh
uv run --with 'jsonschema>=4,<5' --with 'openapi-spec-validator>=0.7,<0.9' python scripts/check_contract.py
```

The checker validates examples, invalid payload rejection, operation IDs, route parameters, and schemas. It runs full OpenAPI validation when `openapi-spec-validator` is installed; otherwise it explicitly reports that check as unavailable. The pinned bootstrap includes that validator. Runtime and device acceptance checks are tracked separately in the delivery plan.

## Target-device preparation

Run `make inspect` on the development machine or Ubuntu host for a non-secret tool inventory. It does not launch inference or decide an account. See [Android tool setup](apps/android/README.md) for pinned downloads and explicit SDK license review; `make android-build` builds the APK and runs lint once prerequisites are installed. The workflow builds against the hosted runner's preinstalled SDK, and does not automatically accept SDK terms.

For a real provider, start from [the disabled operator configuration](config/gateway.example.json) and the [adapter validation instructions](packages/agent-adapters/README.md). Runtime availability needs recorded authentication, protocol, confinement, and cancellation checks on the intended host. Choosing a provider alone does not establish those checks. Local machine setup, private transport, and phone audio acceptance remain separate gates.

## License

[Apache License 2.0](LICENSE). Copyright 2026 Mohit Srivastava.
