#!/usr/bin/env bash
# Chintu installer: installs or upgrades Chintu (and its Ataria daily board) on this computer.
# It picks the right package from GitHub Releases, verifies its checksum, stops the running
# Chintu, installs, sets up Ataria if you want it, and starts the tray again.
#
#   curl -fsSL https://raw.githubusercontent.com/mohyt/chintu/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/mohyt/chintu/main/install.sh | bash -s -- --pre
#
# Options (each also has an environment variable):
#   --version X.Y.Z[-rcN]  install that release                     CHINTU_VERSION
#   --pre                  newest release, test releases included    CHINTU_PRE=1
#   --file PATH            install a package you already have        CHINTU_PACKAGE
#   --force                reinstall the same version, or downgrade  CHINTU_FORCE=1
#   --ataria / --no-ataria set up Ataria now / skip the question     CHINTU_ATARIA=1|0
#   --ataria-profile PATH  use this ataria.json; no setup questions   CHINTU_ATARIA_PROFILE
#   --no-start             install without starting the tray          CHINTU_NO_START=1
#   GITHUB_TOKEN           only needed while the repository is private
#
# Supports Ubuntu/Debian (amd64, arm64) and macOS on Apple silicon. Tailscale and a Claude Code
# or Codex sign-in are separate; the script checks for them and says what to run.
set -euo pipefail

REPO="mohyt/chintu"
API="https://api.github.com/repos/$REPO"

say() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" > /dev/null 2>&1 || die "$1 is required"; }

usage() {
  cat <<'USAGE'
Install or upgrade Chintu (and its Ataria daily board).

  install.sh [--version X.Y.Z[-rcN] | --pre | --file PATH] [--force]
             [--ataria | --no-ataria | --ataria-profile PATH] [--no-start]

  --version   install that release            --pre        newest release, test releases included
  --file      install a package you have      --force      reinstall the same version, or downgrade
  --ataria    set up Ataria now               --no-ataria  skip the Ataria question
  --ataria-profile PATH   use an ataria.json you already have; no setup questions
  --no-start  install without starting the tray
USAGE
}

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

# vercmp A B prints -1, 0 or 1. Versions are X.Y.Z or X.Y.Z-rcN; a release sorts after its candidates.
vercmp() {
  awk -v a="$1" -v b="$2" '
    function key(v,   parts, rc) {
      rc = 999999
      if (match(v, /-rc[0-9]+$/)) { rc = substr(v, RSTART + 3) + 0; v = substr(v, 1, RSTART - 1) }
      split(v, parts, ".")
      return sprintf("%09d%09d%09d%09d", parts[1], parts[2], parts[3], rc)
    }
    BEGIN { ka = key(a); kb = key(b); print (ka < kb ? -1 : (ka > kb ? 1 : 0)) }'
}

detect_platform() {
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
      # The package bundles wheels for the system Python of these releases (3.12, 3.13, 3.14).
      case "$release" in
        24.04|25.10|26.04|13) ;;
        *) warn "packages are tested on Ubuntu 24.04 and 26.04 and Debian 13; this is $pretty" ;;
      esac
      APP_BIN=/opt/chintu/venv/bin/chintu-desktop
      PROFILE="${XDG_DATA_HOME:-$HOME/.local/share}/chintu/ataria.json"
      ;;
    Darwin)
      ARCH="$(uname -m)"
      [ "$ARCH" = "arm64" ] || die "only Apple silicon builds are published; build Intel with scripts/package/build-dmg.sh"
      APP_BIN=/Applications/Chintu.app/Contents/MacOS/chintu-desktop
      PROFILE="$HOME/Library/Application Support/Chintu/ataria.json"
      ;;
    *) die "unsupported system: $OS (Chintu desktop runs on Ubuntu and macOS)" ;;
  esac
}

asset_name() {  # asset_name <version>
  case "$OS" in
    Linux) echo "chintu-desktop_${1}_${ARCH}.deb" ;;
    Darwin) echo "Chintu-${1}-arm64.dmg" ;;
  esac
}

# The installed version in X.Y.Z[-rcN] form, or nothing.
installed_version() {
  case "$OS" in
    Linux) dpkg-query -W -f='${Status} ${Version}\n' chintu-desktop 2> /dev/null \
      | awk '/install ok installed/ { v = $NF; sub(/~rc/, "-rc", v); print v }' ;;
    Darwin) [ -f /Applications/Chintu.app/Contents/Info.plist ] \
      && /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' /Applications/Chintu.app/Contents/Info.plist 2> /dev/null ;;
  esac
  return 0
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

# Picks the release: an explicit version, the newest including test releases, or the latest stable.
choose_release() {
  if [ -n "${CHINTU_VERSION:-}" ]; then
    TAG="v${CHINTU_VERSION#v}"
    fetch "$API/releases/tags/$TAG" "$WORK/release.json" || die "release $TAG not found"
  elif [ "${CHINTU_PRE:-0}" = 1 ]; then
    fetch "$API/releases?per_page=20" "$WORK/releases.json" \
      || die "could not list releases (private repository? set GITHUB_TOKEN)"
    # Newest first; skip drafts. One key per line, whether GitHub sends pretty or minified JSON.
    TAG="$(tr ',{}' '\n' < "$WORK/releases.json" | awk -F'"' '
      /"tag_name":/ { tag = $4 }
      /"draft": *false/ && tag != "" { print tag; exit }
      /"draft": *true/ { tag = "" }')"
    [ -n "$TAG" ] || die "no published release found"
    fetch "$API/releases/tags/$TAG" "$WORK/release.json" || die "release $TAG not found"
  else
    fetch "$API/releases/latest" "$WORK/release.json" \
      || die "could not read the latest release (private repository? set GITHUB_TOKEN)"
  fi
  tr ',{}' '\n' < "$WORK/release.json" > "$WORK/release.lines"
  mv "$WORK/release.lines" "$WORK/release.json"
  [ -n "${TAG:-}" ] || TAG="$(awk -F'"' '/"tag_name":/ { print $4; exit }' "$WORK/release.json")"
  VERSION="${TAG#v}"
}

package_from_file() {
  [ -f "$CHINTU_PACKAGE" ] || die "no such file: $CHINTU_PACKAGE"
  ASSET="$(basename "$CHINTU_PACKAGE")"
  case "$OS:$ASSET" in
    Linux:chintu-desktop_*_"$ARCH".deb) VERSION="${ASSET#chintu-desktop_}"; VERSION="${VERSION%_"$ARCH".deb}" ;;
    Darwin:Chintu-*-arm64.dmg) VERSION="${ASSET#Chintu-}"; VERSION="${VERSION%-arm64.dmg}" ;;
    *) die "$ASSET is not a Chintu package for $OS $ARCH" ;;
  esac
  cp "$CHINTU_PACKAGE" "$WORK/$ASSET"
  TAG="v$VERSION"
  warn "installing a local file: its checksum is not checked against a release"
}

# Stops the tray and the gateway it supervises, so the upgrade never leaves old code running.
stop_running() {
  case "$OS" in
    Linux)
      if pgrep -u "$(id -u)" -f '^/opt/chintu/venv/bin/python3? /opt/chintu/venv/bin/chintu-(desktop|gateway)' > /dev/null 2>&1; then
        say "Stopping the running Chintu"
        pkill -u "$(id -u)" -f '^/opt/chintu/venv/bin/python3? /opt/chintu/venv/bin/chintu-(desktop|gateway)' || true
      fi ;;
    Darwin)
      if pgrep -f 'Chintu.app/Contents/MacOS/chintu-desktop' > /dev/null 2>&1; then
        say "Stopping the running Chintu"
        osascript -e 'quit app "Chintu"' > /dev/null 2>&1 || true
        sleep 2
        pkill -f 'Chintu.app/Contents/MacOS/chintu-desktop' 2> /dev/null || true
      fi ;;
  esac
  local _
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    case "$OS" in
      Linux) pgrep -u "$(id -u)" -f '^/opt/chintu/venv/bin/python3? /opt/chintu/venv/bin/chintu-' > /dev/null 2>&1 || return 0 ;;
      Darwin) pgrep -f 'Chintu.app/Contents/MacOS/chintu-desktop' > /dev/null 2>&1 || return 0 ;;
    esac
    sleep 1
  done
  warn "Chintu did not stop within 10 seconds; continuing"
}

restart_after_failure() {
  local code=$?
  if [ "$code" -ne 0 ]; then
    warn "install failed; starting the installed Chintu again"
    start_tray || true
  fi
  rm -rf "$WORK"
}

# Asks for the administrator password up front, so a missing terminal fails before Chintu is stopped.
ensure_admin() {
  case "$OS" in
    Linux) [ "$(id -u)" -eq 0 ] && return 0 ;;
    Darwin) [ -w /Applications ] && return 0 ;;
  esac
  need sudo
  # sudo asks on the terminal itself, so this works under `curl ... | bash` too; it fails without one.
  sudo -v || die "sudo could not ask for your password (no terminal). Nothing was changed. Run the installer in a terminal on this computer, or over SSH with one (ssh -t)."
}

install_linux() {
  local sudo=""
  [ "$(id -u)" -eq 0 ] || { need sudo; sudo=sudo; }
  # apt reads the file as the _apt user, so the folder must be world-readable.
  chmod 755 "$WORK"; chmod 644 "$WORK/$ASSET"
  say "Installing $ASSET (sudo may ask for your password)"
  $sudo apt-get update -qq
  # --allow-downgrades only matters with --force; apt refuses an older version otherwise.
  $sudo apt-get install -y --allow-downgrades "$WORK/$ASSET"
  if [ "$(id -u)" -eq 0 ]; then
    warn "ran as root: log in as your user and run 'chintu-desktop install-autostart' and 'chintu-desktop run'"
    return
  fi
  chintu-desktop init > /dev/null
  chintu-desktop install-autostart > /dev/null
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
  local sudo=""
  [ -w /Applications ] || sudo=sudo
  $sudo rm -rf /Applications/Chintu.app
  $sudo ditto "$MOUNT/Chintu.app" /Applications/Chintu.app
  hdiutil detach -quiet "$MOUNT"
  # The app is ad-hoc signed, not notarized; clear the quarantine flag so Gatekeeper lets it open.
  $sudo xattr -dr com.apple.quarantine /Applications/Chintu.app 2> /dev/null || true
}

# Starts the tray in the desktop session, also when this script runs over SSH.
start_tray() {
  [ "${CHINTU_NO_START:-0}" != 1 ] || { say "Not starting Chintu (--no-start)"; return 0; }
  case "$OS" in
    Darwin)
      open -a /Applications/Chintu.app
      say "Chintu is running in the menu bar" ;;
    Linux)
      [ "$(id -u)" -ne 0 ] || return 0
      local runtime
      runtime="/run/user/$(id -u)"
      if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && [ -S "$runtime/bus" ]; then
        # Over SSH: borrow the logged-in desktop session, if there is one.
        if [ -S "$runtime/wayland-0" ]; then export WAYLAND_DISPLAY=wayland-0; fi
        if [ -S /tmp/.X11-unix/X0 ]; then export DISPLAY=:0; fi
        export XDG_RUNTIME_DIR="$runtime" DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime/bus"
      fi
      if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        # A transient user service outlives this shell (and an SSH session) and gets the session's display.
        local pass=() name
        for name in DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS XDG_CURRENT_DESKTOP XDG_SESSION_TYPE PATH; do
          [ -z "${!name:-}" ] || pass+=("--setenv=$name=${!name}")
        done
        if ! { command -v systemd-run > /dev/null 2>&1 \
               && systemd-run --user --quiet --collect --unit "chintu-desktop-$$" "${pass[@]}" \
                    chintu-desktop run > /dev/null 2>&1; }; then
          nohup chintu-desktop run > /dev/null 2>&1 &
        fi
        say "Chintu is running in the top panel"
      else
        say "No desktop session found. Start it with: chintu-desktop run (it also starts at your next login)"
      fi ;;
  esac
}

# Ataria, the daily board: use a profile you name, keep an existing one, or ask the setup questions.
# Whenever a profile exists the questions are never asked; `chintu-desktop ataria init` edits it later.
setup_ataria() {
  local cli="$APP_BIN"
  [ -x "$cli" ] || { warn "cannot find $cli; skipping Ataria"; return 0; }
  if [ -n "${CHINTU_ATARIA_PROFILE:-}" ]; then
    local given="${CHINTU_ATARIA_PROFILE/#\~/$HOME}"
    [ -f "$given" ] || die "--ataria-profile: no such file: $given"
    if [ "$(cd "$(dirname "$given")" && pwd -P)/$(basename "$given")" != "$(cd "$(dirname "$PROFILE")" 2> /dev/null && pwd -P)/$(basename "$PROFILE")" ]; then
      mkdir -p "$(dirname "$PROFILE")"
      if [ -f "$PROFILE" ] && ! cmp -s "$given" "$PROFILE"; then
        cp -p "$PROFILE" "$PROFILE.$(date +%Y%m%d-%H%M%S).bak"
        say "Kept your previous Ataria profile as $PROFILE.<date>.bak"
      fi
      (umask 077 && cp "$given" "$PROFILE")
      chmod 600 "$PROFILE"
      say "Using the Ataria profile from $given"
    fi
  fi
  if [ -f "$PROFILE" ]; then
    say "Checking your Ataria setup ($PROFILE)"
    "$cli" ataria doctor || warn "fix the items marked FAIL, then run: chintu-desktop ataria doctor"
    return 0
  fi
  local want="${CHINTU_ATARIA:-}"
  if [ -z "$want" ]; then
    if [ -r /dev/tty ] && [ -w /dev/tty ]; then
      printf '\nAtaria builds a daily board from your mail, calendar, work and news, and shows it in the\nChintu phone app. Set it up now? It asks about you, your personal life, your work (optional) and which agent runs each job. [y/N]: ' > /dev/tty
      local answer=""
      read -r answer < /dev/tty || true
      case "$answer" in [Yy]*) want=1 ;; *) want=0 ;; esac
    else
      want=0
    fi
  fi
  if [ "$want" = 1 ]; then
    [ -r /dev/tty ] || die "Ataria setup asks questions, but there is no terminal; run: chintu-desktop ataria init"
    say "Setting up Ataria"
    "$cli" ataria init < /dev/tty > /dev/tty 2>&1 || warn "Ataria setup did not finish; run it again with: chintu-desktop ataria init"
  else
    say "Ataria is not set up. Set it up any time with: chintu-desktop ataria init"
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
  elif [ "$OS" = Linux ] && [ "$(id -u)" -ne 0 ]; then
    # Without operator rights the tray's "Enable Tailscale Serve" fails with "access denied".
    local prefs
    prefs="$(tailscale debug prefs 2> /dev/null || true)"
    if [ -n "$prefs" ] && ! printf '%s' "$prefs" | grep -Eq "\"OperatorUser\": *\"$(id -un)\""; then
      warn "let Chintu turn on Tailscale Serve without sudo: sudo tailscale set --operator=$(id -un)"
    fi
  fi
  [ -d "$HOME/.claude" ] || command -v codex > /dev/null 2>&1 \
    || warn "no Claude Code or Codex sign-in found; install one and run it once to log in"
  return 0
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --version) [ $# -ge 2 ] || die "--version needs a value"; CHINTU_VERSION="$2"; shift ;;
      --version=*) CHINTU_VERSION="${1#*=}" ;;
      --pre) CHINTU_PRE=1 ;;
      --file) [ $# -ge 2 ] || die "--file needs a path"; CHINTU_PACKAGE="$2"; shift ;;
      --file=*) CHINTU_PACKAGE="${1#*=}" ;;
      --force) CHINTU_FORCE=1 ;;
      --ataria) CHINTU_ATARIA=1 ;;
      --no-ataria) CHINTU_ATARIA=0 ;;
      --ataria-profile) [ $# -ge 2 ] || die "--ataria-profile needs a path"; CHINTU_ATARIA_PROFILE="$2"; shift ;;
      --ataria-profile=*) CHINTU_ATARIA_PROFILE="${1#*=}" ;;
      --no-start) CHINTU_NO_START=1 ;;
      -h|--help) usage; exit 0 ;;
      *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
  done
}

main() {
  parse_args "$@"
  need curl
  need awk
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/chintu-install.XXXXXX")"
  trap 'rm -rf "$WORK"' EXIT
  TAG=""
  detect_platform

  if [ -n "${CHINTU_PACKAGE:-}" ]; then
    package_from_file
  else
    choose_release
    ASSET="$(asset_name "$VERSION")"
  fi

  local current
  current="$(installed_version)"
  if [ -n "$current" ] && [ "${CHINTU_FORCE:-0}" != 1 ]; then
    case "$(vercmp "$current" "$VERSION")" in
      0) say "Chintu $VERSION is already installed"
         setup_ataria; check_prerequisites; return 0 ;;
      1) say "Chintu $current is installed, newer than $VERSION; nothing to do (--force installs $VERSION anyway)"
         return 0 ;;
    esac
  fi
  say "Chintu ${current:+$current → }$VERSION for $OS $ARCH"

  if [ -z "${CHINTU_PACKAGE:-}" ]; then
    download_asset "$WORK/release.json" "$ASSET" "$WORK/$ASSET"
    verify "$WORK/release.json" "$WORK/$ASSET"
  fi
  ensure_admin
  stop_running
  # If the install fails after Chintu was stopped, start the version that is still installed.
  trap restart_after_failure EXIT
  case "$OS" in
    Linux) install_linux ;;
    Darwin) install_macos ;;
  esac
  trap 'rm -rf "$WORK"' EXIT
  setup_ataria
  start_tray
  check_prerequisites
  if [ -n "$current" ]; then
    say "Done: upgraded from $current to $VERSION."
  else
    say "Done. Pair your phone from the tray: Pair phone, then scan the QR code in the Chintu app."
  fi
}

# Tests source the functions without running the installer.
if [ "${CHINTU_INSTALL_SOURCED:-0}" != 1 ]; then
  main "$@"
fi
