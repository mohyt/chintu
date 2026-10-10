# Running the gateway as a daemon

These files run `chintu-gateway serve --provider claude-agent` as the owner's user on a
Mac (launchd) or Ubuntu (systemd user unit). The daemon is the same gateway process the
phone already talks to; the Claude Agent SDK provider is an execution provider inside it,
not a second network service. Nothing here is installed automatically.

## Prerequisites on the host

- Python 3.12 virtual environment at `.venv` with the two local packages and the SDK extra:
  `uv pip install --python .venv/bin/python -e packages/agent-adapters[agent] -e services/gateway`.
- Claude Code CLI installed and signed in as the same user that will run the daemon. Run
  `claude` once interactively and complete the sign-in; the SDK reuses that stored login.
  Do not export `ANTHROPIC_API_KEY` or other Claude auth/routing overrides in the daemon's
  environment; the gateway refuses to start when they are present.
- Tailscale installed and connected on the host and the phone.

## Configure

```sh
cp config/gateway.example.json var/gateway.local.json
```

Edit `var/gateway.local.json`:

- `projects[].path`: absolute workspace directories the agent may run in. `execution_profiles`
  lists the profiles allowed for that workspace.
- `runtimes.claude-agent.enabled`: `true`.
- `runtimes.claude-agent.profiles`: profiles the runtime offers, a subset of `inspect`,
  `develop`, `desktop`. A task runs only when its profile is listed for both the runtime
  and the project.
- `runtimes.claude-agent.model`: default `claude-opus-5-5`.
- `runtimes.claude-agent.load_user_settings`: `true` loads the owner's Claude Code user and
  project settings, including configured MCP servers; `false` runs with built-in tools only.
- Optional `max_turns` (1–1000) and `timeout_seconds` (30–86400, default 1800).

Then validate without launching anything:

```sh
.venv/bin/chintu-gateway --config var/gateway.local.json check-config
```

| Profile | SDK permission mode | Tools | Effects |
| --- | --- | --- | --- |
| `inspect` | `dontAsk` | Read, Glob, Grep, WebSearch, WebFetch only | Read-only; any call that would prompt is denied |
| `develop` | `acceptEdits` | All built-in tools; Edit/Write/Bash/Notebook pre-approved | Edits and commands inside the workspace |
| `desktop` | `bypassPermissions` | All built-in tools and loaded MCP servers | Works like a trusted Claude Desktop/Claude Code session |

There is no interactive approval route from the phone. Anything that would ask for
permission (including `AskUserQuestion`) is denied with a visible progress note, and the
task continues or ends with that limitation.

### Enabling the desktop profile

The `desktop` profile is off unless it appears explicitly in both
`runtimes.claude-agent.profiles` and the project's `execution_profiles`. It bypasses tool
permission prompts for that workspace, so Claude can run any command the owner could.
Deny rules and hooks from the owner's Claude Code settings still apply. Enable it only for
workspaces where that is acceptable, and keep `inspect` for everything else.

## Connectors and MCP servers

With `load_user_settings: true`, the connectors available to a task are the MCP servers
configured for Claude Code on that machine: user scope (`claude mcp add --scope user …`),
project `.mcp.json`, plugin-provided servers, and, when the host's `claude` login is a
claude.ai account, the cloud connectors enabled for that account (they appear to the agent as
`mcp__claude_ai_*` tools; observed live on the owner's Mac with Claude Docs). Claude Desktop's
own local connector configuration is separate and is not read. In `inspect`, MCP tool calls that need approval are denied; in
`develop`, only servers with allow rules in settings run without prompting; in `desktop`,
they run.

## Pair the phone

```sh
.venv/bin/chintu-gateway --config var/gateway.local.json enroll --ttl 300
```

Enter the printed secret in the phone's pairing sheet within five minutes. The daemon must
be running for pairing to succeed. Revoke a phone with `DELETE /api/v1/clients/me` from
that phone, or delete the client row in the database with the daemon stopped.

## Expose the port privately with Tailscale Serve

The gateway binds `127.0.0.1:8765` only. Publish it to the tailnet over HTTPS:

```sh
tailscale serve --bg 8765
tailscale serve status
```

The phone then uses `https://<host>.<tailnet>.ts.net`. `tailscale serve reset` removes the
mapping. Never use `tailscale funnel`; the gateway is for the owner's tailnet only.

## macOS: launchd agent

```sh
mkdir -p var
sed "s|__REPO__|$PWD|g; s|__HOME__|$HOME|g" deploy/macos/ai.chintu.gateway.plist \
  > ~/Library/LaunchAgents/ai.chintu.gateway.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.chintu.gateway.plist
launchctl kickstart -k gui/$(id -u)/ai.chintu.gateway
tail -f var/gateway.log var/gateway.err.log
```

Stop and remove with `launchctl bootout gui/$(id -u)/ai.chintu.gateway`. The agent runs
only while the owner is logged in to the Mac, which is also when the keychain-backed
Claude login is available. The `PATH` in the plist includes `~/.local/bin` so the SDK
finds the owner's `claude` executable; adjust it if the CLI lives elsewhere.

## Ubuntu: systemd user unit

```sh
mkdir -p ~/.config/systemd/user var
cp deploy/ubuntu/chintu-gateway.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now chintu-gateway
loginctl enable-linger "$USER"
journalctl --user -u chintu-gateway -f
```

`enable-linger` keeps the user manager, and therefore the daemon, running after logout and
across reboots. The unit assumes the repository at `~/dev/continuum/chintu`; edit
`WorkingDirectory` and `ExecStart` otherwise.

## Operating notes

- One task runs at a time; others queue. Cancellation sends the SDK interrupt and reports
  `cancelled` only after the session ends; it does not undo completed effects.
- An attempt that ends without a result is `interrupted` and quarantines that workspace
  until the offline `reconcile` command records a reviewed stop.
- Logs contain task lifecycle lines only. Instructions and outputs live in the SQLite
  database under `var/`, which is private to the owner.
