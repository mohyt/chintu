#!/usr/bin/env bash
# Chintu desktop installer: picks the right package from GitHub Releases for this computer,
# verifies its checksum, installs it and starts the tray.
#
#   curl -fsSL https://raw.githubusercontent.com/mohyt/chintu/main/install.sh | bash
#
# Environment:
#   CHINTU_VERSION   release to install, e.g. 0.4.0 (default: latest)
#   GITHUB_TOKEN     only needed while the repository is private
#   CHINTU_NO_START  set to 1 to install without starting the tray
#
# Supports Ubuntu/Debian (amd64, arm64) and macOS on Apple silicon. Tailscale and a Claude Code
# sign-in are separate; the script checks for both and says what to run.
set -euo pipefail

REPO="mohyt/chintu"
API="https://api.github.com/repos/$REPO"

say() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" > /dev/null 2>&1 || die "$1 is required"; }

fetch() {  # fetch <url> <output> [accept]
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    curl -fsSL --retry 3 -H "Authorization: Bearer $GITHUB_TOKEN" \
      -H "Accept: ${3:-application/vnd.github+json}" -o "$2" "$1"
  else
    curl -fsSL --retry 3 -H "Accept: ${3:-application/vnd.github+json}" -o "$2" "$1"
  fi
}

# Prints the API download URL of the named asset from a release JSON file. GitHub lists each
# asset's "url" before its "name", so remember the last asset URL and print it on the name match.
asset_url() {
  awk -v want="$2" '
    /"url": *"https:\/\/api\.github\.com\/repos\/.*\/releases\/assets\// {
      u = $0; sub(/.*"url": *"/, "", u); sub(/".*/, "", u) }
    /"name": *"/ { n = $0; sub(/.*"name": *"/, "", n); sub(/".*/, "", n)
      if (n == want && u != "") { print u; exit } }
  ' "$1"
}

# Prints the hex SHA-256 GitHub records for the named asset, if any.
asset_digest() {
  awk -v want="$2" '
    /"name": *"/ { n = $0; sub(/.*"name": *"/, "", n); sub(/".*/, "", n); found = (n == want) }
    found && /"digest": *"sha256:/ { d = $0; sub(/.*"sha256:/, "", d); sub(/".*/, "", d); print d; exit }
  ' "$1"
}

download_asset() {  # download_asset <release.json> <name> <output>
  local url
  url="$(asset_url "$1" "$2")"
  [ -n "$url" ] || die "release $TAG has no asset named $2"
  fetch "$url" "$3" application/octet-stream
}

detect() {
  OS="$(uname -s)"
  case "$OS" in
    Linux)
      local distro="" release="" pretty="unknown"
      if [ -r /etc/os-release ]; then
        # A subshell, because os-release defines VERSION and would overwrite ours.
        # shellcheck source=/dev/null
        distro="$(. /etc/os-release && echo " ${ID:-} ${ID_LIKE:-} ")"
        # shellcheck source=/dev/null
        release="$(. /etc/os-release && echo "${VERSION_ID:-}")"
        # shellcheck source=/dev/null
        pretty="$(. /etc/os-release && echo "${PRETTY_NAME:-unknown}")"
      fi
      case "$distro" in
        *" ubuntu "*|*" debian "*) ;;
        *) die "Linux installs need Ubuntu or Debian (found $pretty)" ;;
      esac
      need dpkg
      ARCH="$(dpkg --print-architecture)"
      case "$ARCH" in amd64|arm64) ;; *) die "no Debian package for $ARCH" ;; esac
      ASSET="chintu-desktop_${VERSION}_${ARCH}.deb"
      [ "$release" = "24.04" ] || warn "packages are built for Ubuntu 24.04; this is $pretty"
      ;;
    Darwin)
      ARCH="$(uname -m)"
      [ "$ARCH" = "arm64" ] || die "only Apple silicon builds are published; build Intel with scripts/package/build-dmg.sh"
      ASSET="Chintu-${VERSION}-arm64.dmg"
      ;;
    *) die "unsupported system: $OS (Chintu desktop runs on Ubuntu and macOS)" ;;
  esac
}

verify() {  # verify <release.json> <file>
  local expected actual
  expected="$(asset_digest "$1" "$ASSET")"
  if [ -z "$expected" ] && [ -n "$(asset_url "$1" SHA256SUMS)" ]; then
    download_asset "$1" SHA256SUMS "$WORK/SHA256SUMS"
    expected="$(awk -v f="$ASSET" '$2 == f || $2 == "*" f { print $1 }' "$WORK/SHA256SUMS")"
  fi
  if [ -z "$expected" ]; then
    warn "release $TAG publishes no checksum for $ASSET; skipping verification"
    return
  fi
  if command -v sha256sum > /dev/null 2>&1; then
    actual="$(sha256sum "$2" | awk '{print $1}')"
  else
    actual="$(shasum -a 256 "$2" | awk '{print $1}')"
  fi
  [ "$expected" = "$actual" ] || die "checksum mismatch for $ASSET"
  say "Checksum verified"
}

install_linux() {
  local sudo=""
  [ "$(id -u)" -eq 0 ] || { need sudo; sudo=sudo; }
  # apt reads the file as the _apt user, so the folder must be world-readable.
  chmod 755 "$WORK"; chmod 644 "$WORK/$ASSET"
  say "Installing $ASSET (sudo may ask for your password)"
  $sudo apt-get update -qq
  $sudo apt-get install -y "$WORK/$ASSET"

  if [ "$(id -u)" -eq 0 ]; then
    warn "ran as root: log in as your user and run 'chintu-desktop install-autostart' and 'chintu-desktop run'"
    return
  fi
  chintu-desktop init > /dev/null
  chintu-desktop install-autostart
  if [ "${CHINTU_NO_START:-0}" != "1" ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    pkill -u "$(id -u)" -f '/opt/chintu/venv/bin/chintu-desktop' 2> /dev/null || true
    nohup chintu-desktop run > /dev/null 2>&1 &
    say "Chintu is running in the top panel"
  else
    say "Start it with: chintu-desktop run (it also starts at your next login)"
  fi
  case "${XDG_CURRENT_DESKTOP:-}" in
    *GNOME*) command -v gnome-extensions > /dev/null 2>&1 \
      && ! gnome-extensions list --enabled 2> /dev/null | grep -qi appindicator \
      && warn "enable the AppIndicator extension to see the tray icon: sudo apt install gnome-shell-extension-appindicator, then log out and in" ;;
  esac
  return 0
}

install_macos() {
  MOUNT="$WORK/mnt"
  mkdir -p "$MOUNT"
  say "Installing Chintu.app into /Applications"
  hdiutil attach -quiet -nobrowse -readonly -mountpoint "$MOUNT" "$WORK/$ASSET"
  trap 'hdiutil detach -quiet "$MOUNT" 2> /dev/null || true; rm -rf "$WORK"' EXIT
  [ -d "$MOUNT/Chintu.app" ] || die "disk image has no Chintu.app"
  osascript -e 'quit app "Chintu"' > /dev/null 2>&1 || true
  local sudo=""
  [ -w /Applications ] || sudo=sudo
  $sudo rm -rf /Applications/Chintu.app
  $sudo ditto "$MOUNT/Chintu.app" /Applications/Chintu.app
  hdiutil detach -quiet "$MOUNT"
  # The app is ad-hoc signed, not notarized; clear the quarantine flag so Gatekeeper lets it open.
  $sudo xattr -dr com.apple.quarantine /Applications/Chintu.app 2> /dev/null || true
  if [ "${CHINTU_NO_START:-0}" != "1" ]; then
    open -a /Applications/Chintu.app
    say "Chintu is running in the menu bar"
  fi
}

check_prerequisites() {
  local ts=tailscale
  [ "$OS" = Darwin ] && [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ] \
    && ts=/Applications/Tailscale.app/Contents/MacOS/Tailscale
  if ! command -v "$ts" > /dev/null 2>&1; then
    if [ "$OS" = Linux ]; then
      warn "Tailscale is not installed: curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up"
    else
      warn "Tailscale is not installed: get it from https://tailscale.com/download/mac and sign in"
    fi
  fi
  [ -d "$HOME/.claude" ] || warn "no Claude Code sign-in found; install Claude Code and run 'claude' once to log in"
  return 0
}

main() {
  need curl
  need awk
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/chintu-install.XXXXXX")"
  trap 'rm -rf "$WORK"' EXIT
  TAG=""

  if [ -n "${CHINTU_VERSION:-}" ]; then
    TAG="v${CHINTU_VERSION#v}"
    fetch "$API/releases/tags/$TAG" "$WORK/release.json" || die "release $TAG not found"
  else
    fetch "$API/releases/latest" "$WORK/release.json" \
      || die "could not read the latest release (private repository? set GITHUB_TOKEN)"
  fi
  # One key per line, whether GitHub sends pretty or minified JSON.
  tr ',{}' '\n' < "$WORK/release.json" > "$WORK/release.lines"
  mv "$WORK/release.lines" "$WORK/release.json"
  [ -n "${TAG:-}" ] || TAG="$(awk -F'"' '/"tag_name":/ { print $4; exit }' "$WORK/release.json")"
  VERSION="${TAG#v}"
  detect
  say "Chintu $VERSION for $OS $ARCH"

  download_asset "$WORK/release.json" "$ASSET" "$WORK/$ASSET"
  verify "$WORK/release.json" "$WORK/$ASSET"
  case "$OS" in
    Linux) install_linux ;;
    Darwin) install_macos ;;
  esac
  check_prerequisites
  say "Done. Pair your phone from the tray: Pair phone, then scan the QR code in the Chintu app."
}

main "$@"
