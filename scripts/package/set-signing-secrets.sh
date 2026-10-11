#!/usr/bin/env bash
# Upload the Android release signing identity to GitHub Actions secrets so the
# "Release packages" workflow can sign the APK. Reads the same keystore.properties the
# local build uses (apps/android/keystore.properties or the file given as $1); the
# values never appear on the terminal. Requires `gh auth login` with repo scope. The key alias is
# not uploaded: the workflow sets it in plain text, so the logs are not masked wherever "chintu" appears.
set -euo pipefail

props="${1:-$(cd "$(dirname "$0")/../.." && pwd)/apps/android/keystore.properties}"
test -r "$props" || { echo "keystore.properties not found: $props" >&2; exit 1; }

value() { grep "^$1=" "$props" | head -1 | cut -d= -f2-; }
store="$(value storeFile)"
case "$store" in /*) ;; *) store="$(dirname "$props")/$store" ;; esac
test -r "$store" || { echo "keystore not found: $store" >&2; exit 1; }

base64 < "$store" | tr -d '\n' | gh secret set CHINTU_KEYSTORE_B64
value storePassword | tr -d '\n' | gh secret set CHINTU_KEYSTORE_PASSWORD
value keyPassword | tr -d '\n' | gh secret set CHINTU_KEY_PASSWORD
gh secret list
