# 11. Packaging and releases

Chintu ships as three installable packages, published only on GitHub Releases for now:

| Package | Platform | Built by | Contents |
| --- | --- | --- | --- |
| `chintu-<version>.apk` | Android 12+ (Pixel) | `scripts/package/build-apk.sh` | The companion app, signed with the Chintu release key |
| `Chintu-<version>-<arch>.dmg` | macOS 13+ | `scripts/package/build-dmg.sh` | `Chintu.app`: menu-bar tray, gateway daemon and the Claude Agent SDK with its bundled CLI, frozen with PyInstaller |
| `chintu-desktop_<version>_<arch>.deb` | Ubuntu 24.04 and 26.04, Debian 13 (amd64, arm64) | `scripts/package/build-deb.py` | Tray, gateway and vendored wheels; a venv is created under `/opt/chintu/venv` on install |

The version comes from the root `VERSION` file. The Android `versionName`, the three Python
packages and the release tag `v<version>` must all match it; the release workflow refuses a
tag that does not.

## Where an installed Chintu keeps its files

Outside a repo checkout the desktop app uses a per-user data folder (see
[apps/desktop/README.md](../apps/desktop/README.md)):

- macOS: `~/Library/Application Support/Chintu`
- Linux: `$XDG_DATA_HOME/chintu` or `~/.local/share/chintu`
- Override with `CHINTU_HOME` or `--home`.

It holds `gateway.local.json` (written on first start with one `home` project in
`inspect`/`develop`), `chintu.sqlite3`, `local.secret`, the gateway log and nothing else.
Dictated notes go to `~/Documents/Chintu Notes` unless the config says otherwise.
"Open Chintu folder" in the tray menu reveals it.

## Building locally

```
scripts/package/build-apk.sh        # needs apps/android/keystore.properties or CHINTU_KEYSTORE* env
scripts/package/build-dmg.sh        # macOS only; ad-hoc signed, smoke-tested from the mounted image
scripts/package/build-deb.sh        # runs build-deb.py inside python:3.12-slim via Docker
```

Outputs land in `dist/` (git-ignored). Each script verifies what it built: `apksigner
verify` and `aapt2 dump badging` for the APK, `codesign --verify` plus
`scripts/package/smoke-macos.sh` for the app bundle, and an install into a clean Ubuntu
container for the Debian packages.

### Android signing key

The release keystore lives outside the repository at
`~/Library/Application Support/Chintu/signing/chintu-release.jks` with its
`keystore.properties` beside it; `apps/android/keystore.properties` is a git-ignored symlink to
that file. Back the folder up: updates must be signed with the same key, and Android refuses an
APK signed with a different one. GitHub Actions gets the same identity from four repository
secrets, uploaded once with `scripts/package/set-signing-secrets.sh`:
`CHINTU_KEYSTORE_B64`, `CHINTU_KEYSTORE_PASSWORD` and `CHINTU_KEY_PASSWORD` (the alias is plain text in the workflow).

## Publishing a release

1. Bump `VERSION`, the Android `versionCode`, the three `pyproject.toml` versions and
   `chintu_desktop.__version__`; write `docs/releases/v<version>.md`.
2. Commit and push, then tag: `git tag v<version> && git push origin v<version>`.
3. The `Release packages` workflow (`.github/workflows/release.yml`) builds the APK on Ubuntu,
   the Debian packages on Ubuntu, the disk image on `macos-15`, creates the release for the tag if
   it does not exist yet (notes from `docs/releases/`), and uploads the three assets.

The same assets can be published by hand from local builds with
`gh release create v<version> dist/*`; a later workflow run replaces them in place.

## Installing

- **One command (Ubuntu/Debian amd64 and arm64, Apple silicon macOS):**
  `curl -fsSL https://raw.githubusercontent.com/mohyt/chintu/main/install.sh | bash`. The root
  `install.sh` reads the release from the GitHub API (`CHINTU_VERSION` pins one, `GITHUB_TOKEN`
  is needed only while the repository is private), downloads the asset for the OS and CPU, checks
  it against the SHA-256 digest GitHub records for the asset (or the release's `SHA256SUMS`), then
  installs with `apt-get` or copies `Chintu.app` into `/Applications` and clears quarantine. On
  Linux it also runs `chintu-desktop init` and `install-autostart` and starts the tray when a
  graphical session is present (`CHINTU_NO_START=1` skips that). It ends with Tailscale and Claude
  Code sign-in hints. The release workflow attaches `install.sh` and `SHA256SUMS` to each release.
  Verified 17 October 2026 against v0.4.0: clean `ubuntu:24.04` containers on amd64 and arm64
  (installed and started the gateway as a sudo user), and on macOS the download, digest check and
  disk image copy into a scratch folder under the system bash 3.2.
- **Python versions in the Debian package:** the `.deb` installs into a venv built from the
  system `python3`, so it carries the version-specific wheels (cffi, pydantic-core, Pillow,
  rpds-py) for CPython 3.12, 3.13 and 3.14 under `/opt/chintu/wheels/cp3XX`, and the shared
  pure-Python and abi3 wheels once. postinst installs the set for the running interpreter and
  stops with a clear message for any other version; `Depends` limits `python3` to 3.12–3.14.
  v0.4.0 carried only 3.12 wheels and failed on Ubuntu 26.04 ("cffi … is not a supported wheel
  on this platform").
- **Android:** open the APK on the phone (allow installs from the browser or `adb install -r`).
  Updates install over the previous version because the key does not change.
- **macOS:** open the DMG, drag `Chintu.app` to Applications. The app is ad-hoc signed and not
  notarized, so the first launch needs right-click → Open, or
  `xattr -dr com.apple.quarantine /Applications/Chintu.app`. Install Tailscale separately and let the
  tray enable Serve when it asks.
- **Ubuntu:** `sudo apt install ./chintu-desktop_<version>_<arch>.deb`. The package depends on
  `python3-venv`, `python3-tk`, `python3-gi` and the Ayatana AppIndicator libraries; GNOME needs the
  AppIndicator shell extension for the tray icon. Start with `chintu-desktop run`, or
  `chintu-desktop install-autostart` for the login item. Tailscale is a separate install.

## Tailscale is checked, not bundled

The phone reaches a computer only over the tailnet, so every install path checks for Tailscale
instead of assuming it:

- The Debian `postinst` prints a framed warning with the one-line installer when the `tailscale`
  CLI is missing, and a shorter hint when it is installed but signed out.
- The tray re-discovers the CLI on every refresh and shows "Tailscale is not installed" with an
  "Install Tailscale…" item that opens the download page; a signed-out or stopped Tailscale shows
  what to run. The Serve controls only appear once Tailscale answers. `chintu-desktop status`
  includes the same check under `tailscale`.
- The gateway logs a warning at start when no CLI is found.

Claude Code sign-in is per machine: the gateway uses the owner's existing claude.ai login (the
same `~/.claude` credentials Claude Code uses), which the bundled CLI picks up.

## Repository protections

`scripts/github-hardening.sh` applies the GitHub-side controls once the repository is public:
Dependabot alerts and automated fixes, secret scanning with push protection, branch protection on
`main` (CI must pass, no force pushes or deletion; the owner may still push directly) and read-only
workflow tokens. CI also scans the whole history with a checksum-pinned gitleaks on every push.
Vulnerability reports follow `SECURITY.md`.

## Limits of the current packaging

- Nothing is notarized or signed with a developer identity. The DMG is for the owner's own
  machines, not for public distribution through Gatekeeper.
- The Debian package builds a venv at install time with `--system-site-packages` so the tray can
  use PyGObject; it is not a Debian-policy package and is not meant for an apt repository.
- Only arm64 macOS is built in the workflow. An Intel build needs `build-dmg.sh` run on an Intel
  Mac; the script names the image by `uname -m`.
