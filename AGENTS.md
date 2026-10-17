# Repository Guidelines

Chintu (Conversational Hands-free Interface to Networked Terminals & Utilities) is an Android voice companion that talks over Tailscale to a daemon on the owner's Mac or Ubuntu machines, where a Claude Agent SDK runtime performs tasks. The repository holds the phone app, the gateway daemon, the agent adapters, the desktop tray app, the shared wire contract, packaging and the design documents. `README.md` is the entry point; `docs/07-decisions-and-sources.md` is the decision register.

## Project Structure

| Path | What lives there |
| --- | --- |
| `apps/android/` | Kotlin app (API 31+): speech, audio routing, pairing, activity and note modes; `tools/android.py` provisions a workspace-local SDK and Gradle |
| `apps/desktop/` | Python tray app for macOS and GNOME: phone presence, QR pairing, gateway supervision, Tailscale Serve, revocation |
| `services/gateway/` | FastAPI gateway on loopback with SQLite task store, pairing, local-proof endpoints and voice notes |
| `packages/agent-adapters/` | Claude Agent SDK adapter, execution-profile confinement and the scripted fixture |
| `contracts/` | OpenAPI 3.1 contract, adapter protocol and wire examples; `scripts/check_contract.py` validates them |
| `tests/` | Integration tests across gateway and adapters |
| `packaging/`, `scripts/package/` | APK, macOS app bundle and Debian package builds; `.github/workflows/release.yml` publishes them |
| `deploy/`, `config/` | launchd and systemd units, example operator configuration |
| `docs/` | Numbered design documents, validation records and release notes |

## Build and Test

```sh
make bootstrap                              # uv venv + hash-pinned requirements-dev.lock + editable packages
make PYTHON=.venv/bin/python test demo      # contract, gateway, adapters, integration, Android core logic
.venv/bin/python -m unittest discover -s apps/desktop/tests
python3 apps/android/tools/android.py build :app:assembleDebug :app:lintDebug
scripts/package/build-apk.sh | build-dmg.sh | build-deb.sh
```

`make test-android-core` needs `JAVA_HOME` pointing at a JDK 17 or 21. Gradle and the Android SDK are fetched into `apps/android/.tooling/` and are never taken from `PATH`. CI (`.github/workflows/verify.yml`) runs the Python suites and the Android build on every push and pull request.

## Conventions

- Python 3.12, standard library first, `unittest` for tests, no formatter or linter configured; keep modules small and functions pure where the tray or the gateway renders state from them.
- Kotlin for Android with procedural Canvas drawing and no third-party UI libraries; keep dialogs as in-app sheets.
- Every HTTP route must exist in `contracts/openapi.json` first; the gateway validates payloads against it and the checker runs in CI.
- Documents follow the `NN-kebab-case.md` pattern with requirement IDs (`FR-01`, `NFR-01`) and decision IDs (`D1`); keep confirmed facts, proposals and measured evidence distinct. Record behaviour changes in the validation matrix of `docs/06-delivery-plan.md`.

## Versions and Releases

The root `VERSION` file is the single version; the Android `versionName`, the three `pyproject.toml` files and `chintu_desktop.__version__` must match it. Tagging `v<version>` builds and publishes the packages. See `docs/11-packaging-and-releases.md`.

## Commits and Pull Requests

Concise imperative subjects (`Harden local endpoints`, `docs: clarify cancellation outcomes`), no attribution trailers. Pull requests describe the change, the affected requirement or decision IDs, the validation performed and open questions; include screenshots for UI changes.

## Security and Scope

Never commit credentials, keystores, dictated notes, databases or raw audio; `.gitignore` covers `var/`, `*.jks`, `notes/` and `*.sqlite3`. Local endpoints require the per-start secret in `var/local.secret`; `inspect` and `develop` profiles are workspace-confined through the PreToolUse hook. Keep task-scoped authorisation and the distinction between stopping speech and cancelling execution as the design documents specify. Report vulnerabilities as described in `SECURITY.md`.
