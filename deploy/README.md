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
chmod 0600 var/gateway.local.json
```

The configuration names the workspaces the agent may touch, so keep it owner-only (the
daemon creates its own files with `umask 077`; the plist sets `Umask` and the unit `UMask`).
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
- Optional top-level `token_ttl_days` (1–365, default 30): how long a paired phone's
  credential stays valid before it must pair again.

Then validate without launching anything:

```sh
.venv/bin/chintu-gateway --config var/gateway.local.json check-config
```

### Execution profiles

| Profile | SDK permission mode | Tools | Confinement | Settings loaded |
| --- | --- | --- | --- | --- |
| `inspect` | `dontAsk` | Read, Glob, Grep | Hook: every path must resolve inside the workspace; everything else denied | user only |
| `develop` | `dontAsk` | Read, Glob, Grep, Edit, Write, MultiEdit, NotebookEdit, Bash | Hook: file paths inside the workspace; Bash text filter (below) | user and project |
| `desktop` | `bypassPermissions` | All built-in tools and loaded MCP servers | None: works like a trusted Claude Code session | user and project |

`inspect` and `develop` register **no** bare `allowed_tools` entries, because the SDK
auto-approves such entries before any callback runs. Instead a `PreToolUse` hook
(`packages/agent-adapters/src/chintu_adapters/confinement.py`) decides every tool call;
hooks run before every other permission step and a hook deny is final in every mode.

What the hook guarantees:

- Read, Glob, Grep, Edit, Write, MultiEdit and NotebookEdit only touch paths that
  resolve, after following symlinks and `..`, inside the registered workspace
  (`~` and absolute paths elsewhere are refused; a relative Glob pattern with `..` is refused).
- Any tool the profile does not list (WebFetch, WebSearch, Agent, MCP tools, …) is refused.
- Bash exists only in `develop` and is refused when the command text contains an
  absolute path outside the workspace, `~`, `..`, `$HOME`, `sudo`/`su`/`doas`, a
  `curl`/`wget` piped into a shell or interpreter, an unparsable quote, or a `cd`/`pushd`
  whose target leaves the workspace (bare `cd` included).
- `inspect` loads only the owner's user settings; a cloned repository's
  `.claude/settings.json` and project hooks are untrusted and never loaded.

What it does **not** guarantee: the Bash check is a text filter, not a sandbox. A
command that passes it can still reach outside the workspace through a symlink inside
the workspace, environment variables other than `HOME`, programs that resolve their own
paths (`git -C`, package managers reading `~/.config`, build tools), subshells that
compute paths, or the network. It also refuses some harmless commands (any `..`, such as
`git log A..B`). Use `inspect` when the workspace must not be able to act on the host.
`develop` loads project settings, so a workspace's own `.claude` hooks run there.

There is no interactive approval route from the phone. Anything that would ask for
permission (including `AskUserQuestion`) is denied with a visible progress note, and the
task continues or ends with that limitation.

### Enabling the desktop profile

The `desktop` profile is off unless it appears explicitly in both
`runtimes.claude-agent.profiles` and the project's `execution_profiles`. It bypasses tool
permission prompts for that workspace, so Claude can run any command the owner could.
Deny rules and hooks from the owner's Claude Code settings still apply. The gateway logs
`claude-agent desktop profile enabled: bypassPermissions, no workspace confinement` at
start and again per desktop task. Enable it only for workspaces where that is acceptable,
and keep `inspect` for everything else.

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

Enter the printed secret in the phone's pairing sheet within five minutes, or print the
QR page link and open it on this machine (it carries the per-start local proof from
`var/local.secret`, so it only works in a browser on the host):

```sh
.venv/bin/chintu-gateway --config var/gateway.local.json pair-url
```

The daemon must be running for pairing to succeed. Pairing is throttled to ten attempts
per minute and at most five unconsumed secrets exist at once. A phone's credential
expires after `token_ttl_days` (default 30) and the phone then pairs again. Revoke a phone
earlier with `DELETE /api/v1/clients/me` from that phone, from the desktop companion
(`GET /local/clients` and `POST /local/clients/{client_id}/revoke` with the local proof), or
offline:

```sh
.venv/bin/chintu-gateway --config var/gateway.local.json revoke --client-id <id>
.venv/bin/chintu-gateway --config var/gateway.local.json revoke --all
```

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

## Debian package (Ubuntu 24.04 and 26.04, Debian 13; amd64 and arm64)

`scripts/package/build-deb.py` produces `dist/chintu-desktop_<version>_amd64.deb` and
`dist/chintu-desktop_<version>_arm64.deb` (the release workflow runs it on an
`ubuntu-24.04` runner; on a Mac use `scripts/package/build-deb.sh`, which runs the same
script inside `python:3.12-slim` with Docker). The package carries the tray app, the
gateway, the adapters and every runtime dependency as wheels, pinned in
[`packaging/debian/requirements-linux.txt`](../packaging/debian/requirements-linux.txt)
to the versions in `requirements-dev.lock`.

Install, then check the layout the commands picked up:

```sh
sudo apt install ./chintu-desktop_0.4.2_amd64.deb   # or _arm64.deb
chintu-desktop --version
chintu-desktop status | head -40                    # layout.kind is "installed"
```

Where things live after installation:

| Path | Contents |
| --- | --- |
| `/opt/chintu/wheels/` | the three `chintu_*` wheels plus the pinned dependency wheels (incl. `claude-agent-sdk`, which bundles the Claude Code CLI) |
| `/opt/chintu/venv/` | created by `postinst`, not shipped: `python3 -m venv --system-site-packages`, then `pip install --no-index --no-deps` of every wheel above, offline |
| `/opt/chintu/share/chintu/openapi.json` | the API contract; the launchers export `CHINTU_CONTRACT` pointing here |
| `/opt/chintu/bin/chintu-desktop`, `/opt/chintu/bin/chintu-gateway` | launchers that `exec` the virtualenv's console scripts; `/usr/bin/chintu-desktop` and `/usr/bin/chintu-gateway` are symlinks to them |
| `/usr/share/applications/ai.chintu.desktop`, `/usr/share/icons/hicolor/256x256/apps/chintu.png` | the "Chintu" entry in the app grid (`chintu-desktop run`) and its icon |
| `~/.local/share/chintu/` (or `$XDG_DATA_HOME/chintu`, `CHINTU_HOME`) | per-user data, created on first use: `gateway.local.json`, `chintu.sqlite3`, `local.secret`, logs |

The virtualenv sees the system site-packages so that `gi` from `python3-gi` and the
AppIndicator typelib (`gir1.2-ayatanaappindicator3-0.1`) are importable; both are in
`Depends`, together with `python3-tk` for the pairing window. Removing the package
(`sudo apt remove chintu-desktop`) deletes the virtualenv again; nothing under the user's
home is touched. Upgrading recreates the virtualenv from the new package's wheels.

`postinst` also checks for Tailscale: when the `tailscale` command is missing it prints a
framed notice with the install one-liner (`curl -fsSL https://tailscale.com/install.sh | sh
&& sudo tailscale up`); when it is installed but `tailscale status` fails or reports
"Logged out"/"stopped", it prints a one-line hint to run `sudo tailscale up`. Neither
case fails the installation. Tailscale itself is not packaged here; it is a
`Recommends` and comes from Tailscale's own apt repository. The phone can only reach the
gateway once Tailscale is up and Serve is enabled ("Enable Tailscale Serve…" in the tray,
or `tailscale serve --bg 8765`).

On GNOME the tray icon is an AppIndicator, so the shell extension
`gnome-shell-extension-appindicator` (a `Recommends`) must be installed and enabled:
`gnome-extensions enable ubuntu-appindicators@ubuntu.com`, then log out and in. Without
it the app runs but no icon is shown. The first run from the app grid or
`chintu-desktop run` writes a default `gateway.local.json` into the user's Chintu
folder and offers "Start daemon" in the menu; `chintu-desktop install-autostart` adds a
`~/.config/autostart/chintu-desktop.desktop` entry so the tray starts at login
(`uninstall-autostart` removes it). The gateway commands in the sections above work the
same way with `chintu-gateway --config ~/.local/share/chintu/gateway.local.json ...`
instead of `.venv/bin/chintu-gateway --config var/gateway.local.json ...`.

## Operating notes

- One task runs at a time; others queue. Cancellation sends the SDK interrupt and reports
  `cancelled` only after the session ends; it does not undo completed effects.
- An attempt that ends without a result is `interrupted` and quarantines that workspace
  until the offline `reconcile` command records a reviewed stop.
- Logs contain task lifecycle lines only at INFO (IDs, kinds, word counts); instruction
  and output text appears only with `CHINTU_LOG_LEVEL=DEBUG`. Instructions and outputs
  live in the SQLite database under `var/`, which is private to the owner, and the log
  file is set to mode 0600 at startup.
- Each phone sees only the tasks, events and notes it submitted; a second paired phone
  cannot read or cancel them.
