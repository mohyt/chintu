#!/usr/bin/env bash
# Build, sign and verify the Chintu release APK, then place it at dist/chintu-<version>.apk.
#
# Usage (from anywhere): scripts/package/build-apk.sh
#
# Version: read from the repository-root VERSION file (also used by app/build.gradle.kts).
#
# Signing material, in order of precedence:
#   1. Environment variables (CI / GitHub Actions):
#        CHINTU_KEYSTORE           path to the .jks/.p12 keystore (absolute, or relative to the repo root)
#        CHINTU_KEYSTORE_PASSWORD  keystore password
#        CHINTU_KEY_ALIAS          key alias (release key is "chintu")
#        CHINTU_KEY_PASSWORD       key password
#      In GitHub Actions store the keystore as a base64 secret and decode it before running, e.g.
#        echo "$KEYSTORE_B64" | base64 -d > "$RUNNER_TEMP/chintu-release.jks"
#        CHINTU_KEYSTORE="$RUNNER_TEMP/chintu-release.jks" scripts/package/build-apk.sh
#   2. apps/android/keystore.properties (git-ignored) with storeFile, storePassword, keyAlias,
#      keyPassword. Locally this is a symlink to ~/Library/Application Support/Chintu/signing/.
#
# Toolchain: python3 apps/android/tools/android.py provisions Gradle and the command-line tools under
# apps/android/.tooling/ (or honours ANDROID_HOME / ANDROID_SDK_ROOT) and uses JAVA_HOME, defaulting
# to the Homebrew JDK 21 on macOS. SDK Platform 35 and Build Tools 35.0.0 must already be installed
# (GitHub's ubuntu runners ship them in ANDROID_HOME; locally run the "licenses" and "sdk" commands).
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT"

ANDROID_PY="python3 apps/android/tools/android.py"
TOOLING="$ROOT/apps/android/.tooling"
BUILD_TOOLS_VERSION="35.0.0"

VERSION=$(tr -d '[:space:]' < VERSION)
[ -n "$VERSION" ] || { echo "VERSION file is empty" >&2; exit 1; }

# Make a relative CHINTU_KEYSTORE absolute so Gradle resolves it from the repo root, not apps/android.
if [ -n "${CHINTU_KEYSTORE:-}" ] && [ "${CHINTU_KEYSTORE#/}" = "$CHINTU_KEYSTORE" ]; then
  export CHINTU_KEYSTORE="$ROOT/$CHINTU_KEYSTORE"
fi

if [ -z "${JAVA_HOME:-}" ] && [ -d /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home ]; then
  export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
fi

SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$TOOLING/sdk}}
# android.py only accepts its own pinned Gradle under .tooling, never one found on PATH (CI runners have one).
if [ ! -x "$TOOLING/gradle-8.11.1/bin/gradle" ]; then
  echo "==> Pinned Gradle missing; fetching pinned tool archives"
  $ANDROID_PY fetch
elif [ ! -d "$SDK/build-tools/$BUILD_TOOLS_VERSION" ]; then
  echo "==> SDK build-tools missing under $SDK; fetching pinned tool archives"
  $ANDROID_PY fetch
fi

echo "==> Building Chintu $VERSION (release)"
$ANDROID_PY build :app:assembleRelease

APK="$ROOT/apps/android/app/build/outputs/apk/release/app-release.apk"
[ -f "$APK" ] || { echo "Expected APK not produced: $APK" >&2; exit 1; }

APKSIGNER="$SDK/build-tools/$BUILD_TOOLS_VERSION/apksigner"
if [ ! -x "$APKSIGNER" ]; then
  APKSIGNER=$(ls -1 "$SDK"/build-tools/*/apksigner 2>/dev/null | sort -V | tail -n 1 || true)
fi
[ -n "$APKSIGNER" ] && [ -x "$APKSIGNER" ] || { echo "apksigner not found under $SDK/build-tools" >&2; exit 1; }

echo "==> Verifying signature with $APKSIGNER"
CERTS=$("$APKSIGNER" verify --print-certs "$APK")
printf '%s\n' "$CERTS"
if ! printf '%s\n' "$CERTS" | grep -q 'certificate DN: CN=Chintu'; then
  echo "APK is not signed with the Chintu release certificate" >&2
  exit 1
fi

AAPT2="$SDK/build-tools/$BUILD_TOOLS_VERSION/aapt2"
if [ -x "$AAPT2" ]; then
  BADGING=$("$AAPT2" dump badging "$APK")
  if ! printf '%s\n' "$BADGING" | head -n 1 | grep -q "versionName='$VERSION'"; then
    echo "APK versionName does not match VERSION ($VERSION):" >&2
    printf '%s\n' "$BADGING" | head -n 1 >&2
    exit 1
  fi
  if printf '%s\n' "$BADGING" | grep -q 'application-debuggable'; then
    echo "Release APK is debuggable; refusing to package it" >&2
    exit 1
  fi
fi

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/chintu-$VERSION.apk"
cp "$APK" "$OUT"
echo "==> Packaged $OUT ($(du -h "$OUT" | cut -f1))"
