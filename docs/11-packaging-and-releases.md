# 11. Packaging and releases

Chintu ships as three installable packages, published only on GitHub Releases for now:

| Package | Platform | Built by | Contents |
| --- | --- | --- | --- |
| `chintu-<version>.apk` | Android 12+ (Pixel) | `scripts/package/build-apk.sh` | The companion app, signed with the Chintu release key |
| `Chintu-<version>-<arch>.dmg` | macOS 13+ | `scripts/package/build-dmg.sh` | `Chintu.app`: menu-bar tray, gateway daemon and the Claude Agent SDK with its bundled CLI, frozen with PyInstaller |
| `chintu-desktop_<version>_<arch>.deb` | Ubuntu 24.04 (amd64, arm64) | `scripts/package/build-deb.py` | Tray, gateway and vendored wheels; a venv is created under `/opt/chintu/venv` on install |

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
`CHINTU_KEYSTORE_B64`, `CHINTU_KEYSTORE_PASSWORD`, `CHINTU_KEY_ALIAS`, `CHINTU_KEY_PASSWORD`.

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

## Limits of the current packaging

- Nothing is notarized or signed with a developer identity. The DMG is for the owner's own
  machines, not for public distribution through Gatekeeper.
- The Debian package builds a venv at install time with `--system-site-packages` so the tray can
  use PyGObject; it is not a Debian-policy package and is not meant for an apt repository.
- Only arm64 macOS is built in the workflow. An Intel build needs `build-dmg.sh` run on an Intel
  Mac; the script names the image by `uname -m`.
