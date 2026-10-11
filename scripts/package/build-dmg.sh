#!/usr/bin/env bash
# Build, ad-hoc sign, verify and smoke-test the macOS app bundle, then pack it into a disk image at
# dist/Chintu-<version>-<arch>.dmg (arch from `uname -m`: arm64 on Apple silicon, x86_64 on Intel).
#
# Usage (from anywhere): scripts/package/build-dmg.sh
#
# Steps:
#   1. A fresh build virtualenv at build/macos/venv (`uv venv` when uv is installed, else `python3 -m venv`;
#      CHINTU_BUILD_PYTHON names a specific interpreter for either tool). PyInstaller needs a Python with
#      a shared libpython: uv's managed CPython and actions/setup-python both qualify; a pyenv build
#      compiled without --enable-shared does not.
#   2. packaging/requirements-runtime.txt (the pins from requirements-dev.lock), the three Chintu packages
#      non-editable with --no-deps, and packaging/requirements-build.txt (PyInstaller).
#   3. The app icon from the tray orb (packaging/macos/make_icns.py), then PyInstaller with
#      packaging/macos/chintu.spec into build/macos/dist/Chintu.app.
#   4. The bundled Claude Code CLI keeps (or gets back) its executable bit, the bundle is ad-hoc signed and
#      verified, Info.plist is checked, and scripts/package/smoke-macos.sh runs against the bundle.
#   5. hdiutil packs Chintu.app plus an Applications symlink into the compressed (UDZO) disk image.
#
# Needs no sudo and no network beyond the package index. The result is ad-hoc signed, not notarized:
# see "macOS app bundle" in apps/desktop/README.md for the first-launch Gatekeeper step.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT"

[ "$(uname -s)" = "Darwin" ] || { echo "build-dmg.sh only runs on macOS" >&2; exit 1; }
VERSION=$(tr -d '[:space:]' < VERSION)
[ -n "$VERSION" ] || { echo "VERSION file is empty" >&2; exit 1; }
ARCH=$(uname -m)

BUILD="$ROOT/build/macos"
VENV="$BUILD/venv"
DISTPATH="$BUILD/dist"
WORKPATH="$BUILD/work"
APP="$DISTPATH/Chintu.app"
ICNS="$BUILD/Chintu.icns"
PY="$VENV/bin/python"
DMG="$ROOT/dist/Chintu-$VERSION-$ARCH.dmg"
STARTED=$SECONDS

say() { printf '==> %s\n' "$*"; }
die() { echo "build-dmg.sh: $*" >&2; exit 1; }

# --- 1. clean build environment -------------------------------------------------------------------
say "Creating the build virtualenv at $VENV"
rm -rf "$VENV" "$DISTPATH" "$WORKPATH"
mkdir -p "$BUILD"
if command -v uv > /dev/null 2>&1; then
  uv venv --quiet --python "${CHINTU_BUILD_PYTHON:-3.12}" "$VENV"
  pip_install() { uv pip install --quiet --python "$PY" "$@"; }
else
  "${CHINTU_BUILD_PYTHON:-python3}" -m venv "$VENV"
  "$PY" -m pip install --quiet --upgrade pip
  pip_install() { "$PY" -m pip install --quiet "$@"; }
fi
"$PY" - <<'EOF'
import sys, sysconfig
print("build python: %s (%s)" % (sys.version.split()[0], sys.executable))
if sys.version_info[:2] != (3, 12):
    sys.exit("the bundle is built with Python 3.12 (requirements-runtime.txt is locked against it)")
if not (sysconfig.get_config_var("Py_ENABLE_SHARED") or sysconfig.get_config_var("PYTHONFRAMEWORK")):
    sys.exit("this Python has no shared libpython; PyInstaller cannot use it (use uv's CPython or python.org's)")
EOF

# --- 2. dependencies ------------------------------------------------------------------------------
say "Installing the pinned runtime dependencies"
pip_install -r packaging/requirements-runtime.txt
say "Installing chintu-adapters, chintu-gateway and chintu-desktop"
pip_install --no-deps ./packages/agent-adapters ./services/gateway ./apps/desktop
say "Installing PyInstaller"
pip_install -r packaging/requirements-build.txt

# --- 3. icon and bundle ---------------------------------------------------------------------------
say "Rendering the app icon"
"$PY" packaging/macos/make_icns.py "$ICNS"
say "Running PyInstaller"
CHINTU_ICNS="$ICNS" "$PY" -m PyInstaller --noconfirm --clean packaging/macos/chintu.spec \
  --distpath "$DISTPATH" --workpath "$WORKPATH"
[ -x "$APP/Contents/MacOS/chintu-desktop" ] || die "PyInstaller did not produce $APP"

# --- 4. bundled CLI, signature, plist, smoke test ---------------------------------------------------
CLI=$(find "$APP/Contents" -path '*/claude_agent_sdk/_bundled/claude' -type f | head -n 1)
[ -n "$CLI" ] || die "the bundled Claude Code CLI is missing from the app"
if [ -x "$CLI" ]; then
  say "Bundled Claude Code CLI kept its executable bit: $CLI"
else
  say "PyInstaller dropped the executable bit on the bundled CLI; restoring it"
  chmod 755 "$CLI"
fi

say "Ad-hoc signing the bundle"
codesign --force --deep --sign - --timestamp=none "$APP"
codesign --verify --deep --strict "$APP"
say "Signature verified"

say "Checking Info.plist"
PLIST="$APP/Contents/Info.plist"
plist() { /usr/libexec/PlistBuddy -c "Print :$1" "$PLIST"; }
[ "$(plist CFBundleIdentifier)" = "ai.chintu.desktop" ] || die "CFBundleIdentifier is not ai.chintu.desktop"
[ "$(plist CFBundleShortVersionString)" = "$VERSION" ] || die "CFBundleShortVersionString does not match VERSION"
[ "$(plist CFBundleVersion)" = "$VERSION" ] || die "CFBundleVersion does not match VERSION"
[ "$(plist LSUIElement)" = "true" ] || die "LSUIElement is not true"
[ "$(plist CFBundleExecutable)" = "chintu-desktop" ] || die "CFBundleExecutable is not chintu-desktop"

say "Smoke-testing the bundle"
"$ROOT/scripts/package/smoke-macos.sh" "$APP"

# --- 5. disk image --------------------------------------------------------------------------------
say "Packing the disk image"
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/chintu-dmg.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/Chintu.app"
ln -s /Applications "$STAGE/Applications"
mkdir -p "$ROOT/dist"
rm -f "$DMG"
hdiutil create -volname "Chintu $VERSION" -srcfolder "$STAGE" -ov -format UDZO -quiet "$DMG"

say "Packaged $DMG ($(du -h "$DMG" | cut -f1)) in $((SECONDS - STARTED)) s"
