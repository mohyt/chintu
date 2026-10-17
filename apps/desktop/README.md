# Chintu Desktop: menu bar / top panel companion

One Python codebase that sits in the macOS menu bar or the Ubuntu GNOME top panel and
shows, at a glance, whether the paired phone can reach this computer. It is a companion
and supervisor for the [gateway](../../services/gateway/README.md); the daemon remains the
gateway, and this app never handles task text, audio, or bearer tokens.

## What it shows

| Menu line | Source |
| --- | --- |
| Phone: `<name>` connected / away / not paired yet | `clients` and `phone_connected` from `GET /local/status` |
| Gateway: running (pid, started here) / listening (started elsewhere) / not running | child process state, a loopback TCP probe, and a proven `GET /local/status` (see below) |
| Port 8765 is in use by something that isn't Chintu | the port accepts connections but did not pass the local proof |
| Active tasks: N (total M) | `active_tasks`, `total_tasks` |
| Tailnet URL: `https://<machine>.<tailnet>.ts.net` | `tailscale.gateway_url` |
| Tailscale Serve: on (`url`) / off | `tailscale serve status`, refreshed every 30 s |

The icon is a purple orb drawn procedurally with Pillow: a lit ring when the phone is
connected, a dim orb when it is paired but away, and a slashed orb when no gateway
answers on the loopback port. `--mono` switches to black-on-transparent shapes for a
light macOS menu bar. Status is polled every 5 s (`--interval`) from
`http://127.0.0.1:<port>/local/status` and `/local/clients`, which the gateway only
answers for direct loopback requests that carry the local proof.

Menu actions:

- Show pairing QR: opens an in-app window (see below). Default action on a left click.
- Open pairing page in browser: `http://127.0.0.1:<port>/pair?key=<secret>`, the fallback.
- Open console: the same page for now; the gateway has no separate console yet.
- Paired phones ▸: one entry per phone (`<name>: connected|away, expires in N days`),
  each with "Revoke <name>…", plus "Revoke all phones…". See "Revoking a phone".
- Tailscale line: "connected", "installed but not signed in", "installed but stopped", or
  "Tailscale is not installed" with an "Install Tailscale…" item that opens the download page.
  The CLI is re-discovered on every refresh (`tailscale status --json`, read-only), so installing
  it while the tray runs is noticed. The Serve controls appear only once Tailscale answers.
- Enable Tailscale Serve…: asks for confirmation first, then runs `tailscale serve --bg <port>`.
- Start daemon / Stop daemon and Open daemon log (`desktop-gateway.log` in the Chintu folder).
  In the bundle and installed layouts the tray starts the daemon itself at launch when nothing
  listens on the port (`--no-daemon` turns that off); a repository checkout never auto-starts.
- Open Chintu folder: reveals the folder holding the config, database, secret and logs
  (`open` on macOS, `xdg-open` on Linux); see "Where Chintu keeps its files".
- Quit: stops a daemon this app started, closes the QR window, removes the icon.

## Where Chintu keeps its files

`layout.py` works out one of three layouts at start-up and every other module takes
that `Layout` instead of a repository path:

| Layout | When | Gateway command | Contract | Chintu folder (data) |
| --- | --- | --- | --- | --- |
| `repo` | `--repo`, `CHINTU_REPO`, or the package is imported from a checkout that has `contracts/openapi.json` and `.venv/bin/chintu-gateway` | `<repo>/.venv/bin/chintu-gateway` | `<repo>/contracts/openapi.json` | `<repo>/var/` |
| `bundle` | a PyInstaller build (`sys.frozen`), the macOS `.app` | `<executable> gateway …` (the same binary, see below) | `contracts/openapi.json` inside the bundle (`sys._MEIPASS`) | per-user folder |
| `installed` | console scripts in a virtualenv, e.g. the Debian package's `/opt/chintu/venv` | `chintu-gateway` next to the running interpreter | first of `$CHINTU_CONTRACT`, `<prefix>/share/chintu/openapi.json`, `<prefix>/../share/chintu/openapi.json` | per-user folder |

The per-user Chintu folder is `--home` or `CHINTU_HOME` when given, otherwise
`~/Library/Application Support/Chintu` on macOS and `$XDG_DATA_HOME/chintu`
(default `~/.local/share/chintu`) elsewhere. It is created owner-only (0700) on first
use and holds:

| File | Written by |
| --- | --- |
| `gateway.local.json` | this app, once, when missing (never in the `repo` layout): see "Configuration" |
| `chintu.sqlite3` | the gateway (tasks, paired phones) |
| `local.secret` | the gateway, fresh on every start (mode 0600) |
| `desktop-gateway.log` | this app, the output of a gateway it started |
| `desktop.json` | this app: the gateway port it settled on (`{"port": 8766}`) when 8765 was taken by another program; delete it to go back to 8765 |
| `desktop.log`, `desktop.err.log` | launchd, when the login item is installed |

Port choice: an explicit `--port` wins, then the port saved in `desktop.json`, then 8765. When
the tray starts (bundle and installed layouts, no `--port`, no `--no-daemon`) and the port answers
but is not this folder's gateway, it moves to the first free port in 8766-8799, saves it and says
so in the menu. Tailscale Serve, pairing and `chintu-desktop status` then use that port too.

Notifications on Linux go straight to the freedesktop notification service with the app name
"Chintu", the `chintu` theme icon and the `ai.chintu` desktop entry, so GNOME shows who sent them;
pystray's own notifier is used on macOS.

`chintu-desktop gateway …` runs the gateway CLI in-process with the remaining
arguments; the bundle starts its daemon that way because it ships no separate
`chintu-gateway` script. `chintu-desktop status` prints the detected layout under
`layout` (`kind`, `home`, `config`, `contract`, `gateway_command`).

## Local proof: how the tray talks to the gateway

The gateway writes a fresh random secret to `local.secret` beside its database
(mode 0600) every time it starts; in the Chintu folder, or `<repo>/var/` in a
checkout. Every `/local/*` request from this app carries it:

- header `X-Chintu-Local: <secret>`, read from the file again for every request
  (never cached, so a daemon restart needs no tray restart);
- a `Host` header of exactly `127.0.0.1:<port>`, set explicitly;
- no `Origin` header.

The gateway answers 403 to anything else. Requests go through `http.client`
straight to the loopback address, so proxy environment variables never apply.
The file path comes from the layout, which is why the tray and the daemon must use
the same Chintu folder. `local.py` holds this client; nothing else in the app makes
HTTP requests.

The browser pairing page takes the same secret as `?key=`; the URL is always built
here from the port and the file. A `pair_url` field in a server response is
ignored on purpose: a process squatting on the port could otherwise point the
browser anywhere.

### Never trusting an unverified listener

A port that accepts TCP connections is not assumed to be the gateway. It counts
as the gateway only when `GET /local/status` with the proof returns 200 and
`service == "chintu"`. Otherwise the menu shows
"Port 8765 is in use by something that isn't Chintu" with the reason underneath
(a 403, a different service, no HTTP at all) and offers nothing but Retry and
Quit: no pairing, no Start daemon, no Serve. If `local.secret` is missing while
the port is busy, the line reads "Gateway: not running, or not started from this
Chintu folder" instead, since the tray cannot tell a foreign daemon from a squatter
without the file. The pairing window and the `status` subcommand report the same
reasons in words.

## Revoking a phone

"Paired phones ▸" lists what `GET /local/clients` returns. Revoking is a two-click
action so a slip on a tray menu cannot sign a phone out: "Revoke <name>…" records
the intent and posts a notice; opening the submenu again within 60 s shows
"Confirm revoke <name>" and "Cancel". Confirm sends
`POST /local/clients/<client_id>/revoke` (with the proof) on a worker thread and
polls the gateway again. "Revoke all phones…" works the same way, confirming as
"Confirm revoke all N phones", and posts one revoke per client. The outcome, or
the first failure, is shown as the notice line.

"Enable Tailscale Serve…" uses the same pattern because Serve exposes the gateway
to every device on the tailnet: the first click explains that and the menu then
shows "Confirm: expose the gateway to the whole tailnet" next to "Cancel".

## How pairing works

"Show pairing QR" opens a dark window that calls `POST http://127.0.0.1:<port>/local/pairing-code`
once. The gateway mints a fresh one-time enrollment secret, and the window renders the
returned payload as a QR with `segno`, next to the machine name, the gateway URL and a
countdown. On the phone, open Chintu, tap Add a computer, then Scan the QR code. The
code works once and expires after ten minutes; Refresh mints another. The secret is
only ever shown on this screen: the endpoint needs the local proof above (403
without it), and the window reports plainly when Tailscale is not connected
(503), when `local.secret` is missing, or when the gateway is down.

The window is a separate `chintu-desktop qr-window` process. pystray requires its
`run()` loop on the main thread, and `run_detached()` on macOS expects an `NSApplication`
handed over by the host toolkit, which tkinter does not provide; on GTK nothing would
pump the indicator's GLib loop. A child process gives Tk its own main thread on both
platforms, so the tray never freezes while the window is open. Picking the menu item
again raises the existing window instead of minting another code.

## Install on macOS

From the repository root, after `make bootstrap`:

```sh
uv pip install --python .venv/bin/python -e apps/desktop
.venv/bin/chintu-desktop --help
.venv/bin/chintu-desktop
```

pystray uses AppKit through pyobjc, which it installs itself. The Tailscale CLI is found
on `PATH` or inside `/Applications/Tailscale.app/Contents/MacOS/Tailscale`. To start at
login, `chintu-desktop install-autostart` copies
[`ai.chintu.desktop.plist`](src/chintu_desktop/autostart/ai.chintu.desktop.plist) into
`~/Library/LaunchAgents` with the program (`__EXEC__`: the `.app` executable,
`<venv>/bin/chintu-desktop`, or `<repo>/.venv/bin/chintu-desktop`) and the Chintu folder
(`__DATA__`, passed as `--home`) filled in; it never changes into a repository. Load it
immediately with `launchctl load ~/Library/LaunchAgents/ai.chintu.desktop.plist`.
`uninstall-autostart` removes the file. The templates are package data in
`chintu_desktop/autostart/`, so they travel inside
the bundle and the wheel.

## macOS app bundle

`scripts/package/build-dmg.sh` turns the tray, the gateway and the adapters into one
`Chintu.app` and packs it into `dist/Chintu-<version>-<arch>.dmg` (`<arch>` is `uname -m`,
so `arm64` on Apple silicon). It is what the `dmg` job in `.github/workflows/release.yml`
runs on a `macos-15` runner; locally it needs only macOS, Python 3.12 (uv's managed CPython
or python.org's, something with a shared `libpython`) and the Xcode command-line tools.
No sudo, no Docker.

```sh
scripts/package/build-dmg.sh                       # build/macos/ scratch, dist/Chintu-0.5.0-arm64.dmg
scripts/package/smoke-macos.sh build/macos/dist/Chintu.app   # re-run the checks on a built app
```

What the build does, in order:

1. A fresh virtualenv at `build/macos/venv` with the pins in
   [`packaging/requirements-runtime.txt`](../../packaging/requirements-runtime.txt) (the
   versions from `requirements-dev.lock`), the three Chintu packages non-editable, and
   PyInstaller from [`packaging/requirements-build.txt`](../../packaging/requirements-build.txt).
2. `packaging/macos/make_icns.py` renders the app icon from the tray orb (`icons.py`) with
   Pillow and `iconutil`.
3. PyInstaller with [`packaging/macos/chintu.spec`](../../packaging/macos/chintu.spec): a
   one-folder build wrapped as `Chintu.app`. The executable is
   `Contents/MacOS/chintu-desktop` and `packaging/macos/entry.py` just calls
   `chintu_desktop.app.main()`, so every subcommand works from the bundle:
   `chintu-desktop status`, `chintu-desktop gateway serve ...`, `... qr-window`. The
   bundle carries `contracts/openapi.json`, the autostart templates and the Claude Code CLI
   from the `claude-agent-sdk` package (`claude_agent_sdk/_bundled/claude`, about 240 MB),
   which the adapter uses when no `claude` is on `PATH`; the script restores its executable
   bit if PyInstaller drops it. `Info.plist` sets `CFBundleIdentifier ai.chintu.desktop`,
   the version from the root `VERSION` file, `LSUIElement` (menu bar only, no Dock icon)
   and `LSMinimumSystemVersion 13.0`.
4. Ad-hoc code signing (`codesign --force --deep --sign -`) and `codesign --verify --deep
   --strict`, then `scripts/package/smoke-macos.sh` against the bundle: `--version`,
   `status` reporting `layout.kind == "bundle"`, `gateway --help`, `init` writing the
   default `gateway.local.json` into a throwaway `CHINTU_HOME`, `check-config`, the gateway
   serving with `--provider claude-agent` on a free port in 8782-8789, `/local/status` with
   the local proof (the provider must be `claude-agent`, which proves the bundled CLI was
   found: `claude` is removed from the gateway's `PATH` for the test), and `/pair`. Outside
   CI it also pairs a client and runs one real inspect task ("list the files in my Documents
   folder") through the bundled CLI with the owner's Claude Code login, and checks the
   `claude` process it observed is the one inside the bundle.
5. `hdiutil` packs `Chintu.app` and an `Applications` symlink into a compressed disk image.

Installing: open the DMG, drag Chintu to Applications, then launch it once with
right-click, Open. The bundle is ad-hoc signed, not notarized, so Gatekeeper refuses a plain
double-click on the first launch; the right-click, Open dialog allows it, or clear the
quarantine flag from a terminal:

```sh
xattr -dr com.apple.quarantine /Applications/Chintu.app
```

The app shows only a menu-bar icon (no Dock icon). Its data lives in
`~/Library/Application Support/Chintu` (see "Where Chintu keeps its files"); the first launch
writes `gateway.local.json` there and starts the gateway from the bundle as
`Chintu.app/Contents/MacOS/chintu-desktop gateway ...` (also behind "Start daemon"). `chintu-desktop install-autostart`
from inside the bundle writes a LaunchAgent that points at that executable. The headless
`init` subcommand does the same folder and config preparation without the tray and prints
the config path.

Caveats:

- Tailscale is not included. Install the Tailscale app separately and sign in; the tray
  finds its CLI in `/Applications/Tailscale.app/Contents/MacOS/Tailscale` or on `PATH`.
- The app is signed ad hoc and not notarized (see above). A notarized build needs an Apple
  Developer ID; nothing in the scripts assumes one. `codesign --deep` also re-signs the
  bundled `claude` binary ad hoc (Anthropic's signature and hardened runtime are replaced);
  it still runs, and the smoke test's real task is the check for that.
- The bundled Claude Code CLI uses the login in `~/.claude`; log in once with any `claude`
  (`claude login`) before starting a real task. A `claude` on `PATH` is preferred over the
  bundled one, exactly as in the other layouts.
- The build is native to the machine that runs it (arm64 or x86_64); there is no universal
  binary.

## Install on Ubuntu 24.04 / 26.04 (GNOME)

GNOME needs the AppIndicator bindings and an extension that displays indicators:

```sh
sudo apt install gir1.2-ayatanaappindicator3-0.1 python3-gi gnome-shell-extension-appindicator python3-tk
```

Then enable the extension "AppIndicator and KStatusNotifierItem Support" in the
Extensions app (or `gnome-extensions enable ubuntu-appindicators@ubuntu.com`) and log
out and in again. The `gi` module comes from the system Python, so create the virtualenv
with system packages visible, for example `uv venv --python 3.12 --system-site-packages`,
or symlink `gi` into `.venv/lib/python3.12/site-packages/`. Install the package the same
way as on macOS. `python3-tk` provides the pairing window.

When the backend is missing the app prints the apt line above and exits with status 2;
without `DISPLAY`/`WAYLAND_DISPLAY` it exits the same way instead of raising. The
`install-autostart` subcommand writes
[`chintu-desktop.desktop`](src/chintu_desktop/autostart/chintu-desktop.desktop) to
`~/.config/autostart/`.

## Configuration

| Option | Default | Purpose |
| --- | --- | --- |
| `--port` | `8765` | loopback port of the gateway |
| `--repo` | `CHINTU_REPO`, else detected | force the `repo` layout on this checkout |
| `--home` | `CHINTU_HOME`, else the platform folder | the Chintu folder (ignored in the `repo` layout, which uses `<repo>/var`) |
| `--gateway-args` | see below | arguments passed to `chintu-gateway` when starting it |
| `--interval` | `5` | status poll interval in seconds |
| `--mono` | off | monochrome icon |

`--port`, `--repo`, `--home` and `--gateway-args` may also follow the subcommand
(`chintu-desktop status --port 8781`).

Without `--gateway-args`, "Start daemon" runs
`chintu-gateway --config <folder>/gateway.local.json serve --provider claude-agent --port <port>`
when that config exists, otherwise the scripted fixture
`chintu-gateway --database <folder>/chintu.sqlite3 serve --scripted --contract <contract> --port <port>`.
In the `bundle` and `installed` layouts the app first writes a default
`gateway.local.json` (mode 0600) when there is none, so a fresh install goes straight to
the real provider: one project `home` (the user's home folder, profiles `inspect` and
`develop`), the `claude-agent` runtime enabled with model `claude-opus-5-5` and
`executable: "claude"`, the `claude` and `codex` CLI runtimes disabled, and `notes_dir`
`~/Documents/Chintu Notes`. It matches `config/gateway.example.json` and passes
`chintu-gateway --config <folder>/gateway.local.json check-config`; edit it to add
workspaces. `executable: "claude"` is looked up on `PATH` and otherwise falls back to the
Claude Code CLI bundled inside the `claude-agent-sdk` package, so no separate `claude`
install is needed (the stored login in `~/.claude` is shared either way). A repository
checkout never gets a generated config: copy the example into `var/` as the deploy
notes describe. If anything already listens on the port, the app starts nothing; whether
it then treats the listener as the gateway depends on the proof check above. The
`status` subcommand prints one JSON snapshot (state, `fault_kind`, layout, secret file
path, gateway command, Serve status) without a tray, which is the headless way to check
the configuration.

## Checks

```sh
.venv/bin/python -m unittest discover -s apps/desktop/tests -v
```

The tests cover layout detection for the three layouts and the default folder per
platform, the generated config (validated with the gateway's own `load_config`), state
derivation from sample payloads, argument selection and the "already listening" probe
against a real ephemeral socket, a fresh-install start that writes the config and picks
the real provider, icon rendering for every state and size, `tailscale serve status`
parsing from captured output, pairing-code parsing and QR PNG rendering, autostart file
rendering for each layout, the QR child command, the `gateway` dispatch, and the menu
with a stand-in for pystray. `tests/fake_gateway.py` is an HTTP server on an ephemeral port that
enforces the same proof as the gateway; against it the tests check secret loading
and header injection, the explicit `Host`, 403 handling, listener verification with
the right and wrong secret and with a foreign service, the locally built pair URL,
the two-click revoke flow, and the Serve confirmation. They need no display, never
touch port 8765, and never mint a real pairing secret.

## Limits

- It is a companion: it reads presence and supervises the process. Task execution,
  approvals, and history stay in the gateway and the phone app.
- A daemon started elsewhere (launchd, systemd, a terminal) is only observed; Stop
  only applies to a child this app started, and Quit stops that child.
- pystray cannot mark macOS images as template images, so `--mono` suits light menu
  bars; the colour orb works on both.
- pystray's AppKit backend mutates the status item on the calling thread, and current
  macOS traps (`SIGTRAP` in `-[NSStatusItem setMenu:]`) when that is not the main
  thread, so every icon update is queued through pyobjc's `AppHelper.callAfter`. The
  GTK backend already does the equivalent with `GLib.idle_add`.
- Verified so far: the unit tests, the `status` subcommand against a live gateway and
  Tailscale Serve, the pairing window with a stubbed code, and a four-second run of the
  real tray on macOS 26 (icon shown, state refreshed, clean exit). Menu interaction on
  macOS and the GNOME indicator on Ubuntu have not been exercised yet, and the local
  proof, Paired phones submenu and confirmations have only run against the fake
  gateway in the tests, not against the real daemon.
