#!/usr/bin/env bash
# Smoke-test a built Chintu.app without installing it: the CLI surface, the bundled gateway with the
# generated operator config, the local proof, the pairing page, and (outside CI) one real inspect task
# through the Claude Code CLI shipped inside the bundle.
#
# Usage: scripts/package/smoke-macos.sh <path-to-Chintu.app>
#
# The test uses a throwaway CHINTU_HOME and the first free port in 8782-8789, so it never touches a
# running Chintu on 8765. `claude` is removed from the gateway's PATH so the adapter must fall back to
# the CLI inside the bundle. With CI set the real task is skipped (no Claude login on a runner) and the
# pairing page may answer 503 (no Tailscale there).
set -euo pipefail

APP=${1:-}
[ -n "$APP" ] || { echo "usage: smoke-macos.sh <path-to-Chintu.app>" >&2; exit 2; }
APP=$(cd "$APP" && pwd)
BIN="$APP/Contents/MacOS/chintu-desktop"
PLIST="$APP/Contents/Info.plist"
[ -x "$BIN" ] || { echo "not an app bundle with Contents/MacOS/chintu-desktop: $APP" >&2; exit 2; }

say() { printf 'smoke: %s\n' "$*"; }
fail() { echo "smoke: FAIL: $*" >&2; exit 1; }
have_python() { command -v python3 > /dev/null 2>&1; }
json_get() {  # json_get '<expr over d>' < json ; e.g. json_get 'd["task"]["task_id"]'
  if have_python; then
    python3 -I -c 'import json,sys; d=json.load(sys.stdin); print('"$1"')'
  else
    local key=${1##*[\"}; key=${key%%\"*}   # last quoted key; enough for the flat fields we read
    sed -n 's/.*"'"$key"'": *"\{0,1\}\([^",}]*\)"\{0,1\}.*/\1/p' | head -n 1
  fi
}

EXPECTED=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PLIST")
export CHINTU_HOME
CHINTU_HOME=$(mktemp -d "${TMPDIR:-/tmp}/chintu-smoke.XXXXXX")
PORT=""
for candidate in 8782 8783 8784 8785 8786 8787 8788 8789; do
  if ! lsof -nP -iTCP:"$candidate" -sTCP:LISTEN > /dev/null 2>&1; then PORT=$candidate; break; fi
done
[ -n "$PORT" ] || fail "no free port in 8782-8789"
say "app=$APP home=$CHINTU_HOME port=$PORT"

GATEWAY_PID=""
cleanup() {
  if [ -n "$GATEWAY_PID" ] && kill -0 "$GATEWAY_PID" 2> /dev/null; then
    pkill -P "$GATEWAY_PID" 2> /dev/null || true
    kill "$GATEWAY_PID" 2> /dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$GATEWAY_PID" 2> /dev/null || break; sleep 1; done
    kill -9 "$GATEWAY_PID" 2> /dev/null || true
  fi
  pkill -f "$APP/Contents/.*/_bundled/claude" 2> /dev/null || true
}
trap cleanup EXIT

# --- (a) version ----------------------------------------------------------------------------------
OUT=$("$BIN" --version)
[ "$OUT" = "chintu-desktop $EXPECTED" ] || fail "--version printed '$OUT', expected 'chintu-desktop $EXPECTED'"
say "(a) --version: $OUT"

# --- (b) status shows the bundle layout -------------------------------------------------------------
STATUS=$("$BIN" status --port "$PORT" --home "$CHINTU_HOME")
printf '%s\n' "$STATUS" | grep -q '"kind": "bundle"' || { printf '%s\n' "$STATUS"; fail "status did not report layout.kind == bundle"; }
printf '%s\n' "$STATUS" | grep -q '"gateway_command": \[' || fail "status has no gateway_command"
say "(b) status: layout.kind == bundle, presence $(printf '%s\n' "$STATUS" | json_get 'd["presence"]')"

# --- (c) the gateway CLI is reachable -----------------------------------------------------------------
"$BIN" gateway --help | grep -q 'serve' || fail "gateway --help does not mention serve"
say "(c) gateway --help ok"

# --- (d) generated config, bundled CLI, gateway up, local proof, pairing page ---------------------------
CONFIG=$("$BIN" --home "$CHINTU_HOME" init)
[ -f "$CONFIG" ] || fail "init did not create $CONFIG"
CHECK=$("$BIN" gateway --config "$CONFIG" check-config)
printf '%s\n' "$CHECK" | grep -q '"config_valid": true' || { printf '%s\n' "$CHECK"; fail "check-config rejected the generated config"; }
say "(d) init wrote $CONFIG; check-config ok"

CLI=$(find "$APP/Contents" -path '*/claude_agent_sdk/_bundled/claude' -type f | head -n 1)
[ -n "$CLI" ] || fail "the bundled Claude Code CLI is not in the app"
[ -x "$CLI" ] || fail "the bundled Claude Code CLI is not executable: $CLI"
CLI_VERSION=$("$CLI" --version 2>&1 | head -n 1 || true)
say "(d) bundled CLI $CLI -> '$CLI_VERSION'"

# A PATH without any `claude` so the adapter must resolve the CLI inside the bundle.
FILTERED_PATH=""
IFS=':' read -r -a dirs <<< "$PATH"
for dir in "${dirs[@]}"; do
  [ -n "$dir" ] || continue
  if [ -x "$dir/claude" ]; then continue; fi
  FILTERED_PATH="${FILTERED_PATH:+$FILTERED_PATH:}$dir"
done
if command -v claude > /dev/null 2>&1; then
  say "(d) removed $(command -v claude) from the gateway's PATH"
fi

GATEWAY_LOG="$CHINTU_HOME/smoke-gateway.log"
( cd "$CHINTU_HOME" && PATH="$FILTERED_PATH" exec "$BIN" gateway --config "$CONFIG" serve --provider claude-agent --port "$PORT" ) \
  > "$GATEWAY_LOG" 2>&1 &
GATEWAY_PID=$!
SECRET_FILE="$CHINTU_HOME/local.secret"
for _ in $(seq 1 60); do
  if [ -s "$SECRET_FILE" ] && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN > /dev/null 2>&1; then break; fi
  kill -0 "$GATEWAY_PID" 2> /dev/null || { cat "$GATEWAY_LOG"; fail "the gateway exited early"; }
  sleep 1
done
[ -s "$SECRET_FILE" ] || { cat "$GATEWAY_LOG"; fail "no local.secret after 60 s"; }
SECRET=$(tr -d '[:space:]' < "$SECRET_FILE")
say "(d) gateway pid $GATEWAY_PID listening on $PORT; local.secret present"

BODY_FILE="$CHINTU_HOME/local-status.json"
CODE=$(curl -s -o "$BODY_FILE" -w '%{http_code}' -H "X-Chintu-Local: $SECRET" -H "Host: 127.0.0.1:$PORT" \
  "http://127.0.0.1:$PORT/local/status")
[ "$CODE" = "200" ] || { cat "$BODY_FILE"; echo; cat "$GATEWAY_LOG"; fail "/local/status answered $CODE"; }
PROVIDER=$(json_get 'd["provider"]' < "$BODY_FILE")
[ "$PROVIDER" = "claude-agent" ] || { cat "$BODY_FILE"; echo; fail "/local/status provider is '$PROVIDER' (the bundled CLI was not resolved?)"; }
say "(d) /local/status 200, provider=$PROVIDER"

PAIR_FILE="$CHINTU_HOME/pair.html"
PAIR_CODE=$(curl -s -o "$PAIR_FILE" -w '%{http_code};%{content_type}' -H "Host: 127.0.0.1:$PORT" \
  "http://127.0.0.1:$PORT/pair?key=$SECRET")
case "$PAIR_CODE" in
  200\;text/html*) say "(d) /pair 200 text/html" ;;
  503\;text/html*) if [ -n "${CI:-}" ]; then say "(d) /pair 503 (no Tailscale on this runner); accepted under CI";
                   else fail "/pair answered 503: $(head -c 300 "$PAIR_FILE")"; fi ;;
  *) head -c 300 "$PAIR_FILE"; echo; fail "/pair answered $PAIR_CODE" ;;
esac
grep -qi '<html' "$PAIR_FILE" || fail "/pair did not return HTML"

# --- (e) one real inspect task through the bundled CLI (this machine only) --------------------------------
if [ -z "${CI:-}" ]; then
  ENROLL=$(cd "$CHINTU_HOME" && PATH="$FILTERED_PATH" "$BIN" gateway --config "$CONFIG" enroll)
  [ -n "$ENROLL" ] || fail "enroll printed nothing"
  PAIRED=$(curl -s -f -X POST -H 'X-Chintu-Protocol: 1' -H 'Content-Type: application/json' \
    -d "{\"enrollment_secret\":\"$ENROLL\",\"client_name\":\"smoke-macos\"}" "http://127.0.0.1:$PORT/api/v1/pair") \
    || fail "pairing failed"
  TOKEN=$(printf '%s' "$PAIRED" | json_get 'd["token"]')
  [ -n "$TOKEN" ] || fail "no token in the pairing response"
  say "(e) paired as smoke-macos"

  INSTRUCTION="List the files in my Documents folder and tell me how many there are"
  SUBMIT=$(curl -s -X POST -H 'X-Chintu-Protocol: 1' -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"submission_key\":\"$(uuidgen | tr 'A-Z' 'a-z')\",\"project_id\":\"home\",\"provider\":\"claude-agent\",\"instruction\":\"$INSTRUCTION\",\"execution_profile\":\"inspect\"}" \
    "http://127.0.0.1:$PORT/api/v1/tasks")
  TASK_ID=$(printf '%s' "$SUBMIT" | json_get 'd["task"]["task_id"]') || { printf '%s\n' "$SUBMIT"; fail "task submission failed"; }
  [ -n "$TASK_ID" ] || { printf '%s\n' "$SUBMIT"; fail "task submission returned no task_id"; }
  say "(e) submitted task $TASK_ID (project home, profile inspect)"

  STATE=""; CLI_SEEN=""; SNAPSHOT=""
  for _ in $(seq 1 100); do
    sleep 3
    SNAPSHOT=$(curl -s -H 'X-Chintu-Protocol: 1' -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/api/v1/tasks/$TASK_ID")
    STATE=$(printf '%s' "$SNAPSHOT" | json_get 'd["state"]')
    if [ -z "$CLI_SEEN" ]; then
      # argv[0] of a running claude process; the CLI also spawns `/bin/zsh -c ...` children that mention its path.
      CLI_SEEN=$(ps -axo args= | awk '$1 ~ /\/claude_agent_sdk\/_bundled\/claude$/ {print $1; exit}' || true)
    fi
    case "$STATE" in succeeded|failed|cancelled|interrupted) break ;; esac
  done
  say "(e) task $TASK_ID ended in state '$STATE'"
  say "(e) claude process seen at: ${CLI_SEEN:-<none observed>}"
  if have_python; then
    printf '%s' "$SNAPSHOT" | python3 -I -c 'import json,sys; d=json.load(sys.stdin); r=d.get("result") or {}; print("smoke: (e) result:", json.dumps(r)[:600])'
  fi
  [ "$STATE" = "succeeded" ] || { tail -n 40 "$GATEWAY_LOG"; fail "the inspect task did not succeed"; }
  if [ -n "$CLI_SEEN" ] && [ "$CLI_SEEN" != "$CLI" ]; then
    fail "the task ran $CLI_SEEN, not the bundled CLI $CLI"
  fi
else
  say "(e) skipped the real inspect task (CI is set)"
fi

say "stopping the gateway"
cleanup
GATEWAY_PID=""
rm -rf "$CHINTU_HOME"   # kept on failure, so the gateway log can be read
say "PASS ($APP, version $EXPECTED, port $PORT)"
