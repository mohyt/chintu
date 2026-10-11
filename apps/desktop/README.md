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
- Enable Tailscale Serve…: asks for confirmation first, then runs `tailscale serve --bg <port>`.
- Start daemon / Stop daemon and Open daemon log (`var/desktop-gateway.log`).
- Quit: stops a daemon this app started, closes the QR window, removes the icon.

## Local proof: how the tray talks to the gateway

The gateway writes a fresh random secret to `<repo>/var/local.secret` (mode 0600)
every time it starts. Every `/local/*` request from this app carries it:

- header `X-Chintu-Local: <secret>`, read from the file again for every request
  (never cached, so a daemon restart needs no tray restart);
- a `Host` header of exactly `127.0.0.1:<port>`, set explicitly;
- no `Origin` header.

The gateway answers 403 to anything else. Requests go through `http.client`
straight to the loopback address, so proxy environment variables never apply.
The file path is derived from `--repo`, which is why the tray and the daemon must
be started from the same checkout. `local.py` holds this client; nothing else in
the app makes HTTP requests.

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
Quit: no pairing, no Start daemon, no Serve. If `var/local.secret` is missing while
the port is busy, the line reads "Gateway: not running, or not started from this
repository" instead, since the tray cannot tell a foreign daemon from a squatter
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
(503), when `var/local.secret` is missing, or when the gateway is down.

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
[`autostart/ai.chintu.desktop.plist`](autostart/ai.chintu.desktop.plist) into
`~/Library/LaunchAgents` with the repository path filled in; load it immediately with
`launchctl load ~/Library/LaunchAgents/ai.chintu.desktop.plist`. `uninstall-autostart`
removes the file.

## Install on Ubuntu 22.04 / 24.04 (GNOME)

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
`install-autostart` subcommand writes [`autostart/chintu-desktop.desktop`](autostart/chintu-desktop.desktop)
to `~/.config/autostart/`.

## Configuration

| Option | Default | Purpose |
| --- | --- | --- |
| `--port` | `8765` | loopback port of the gateway |
| `--repo` | `CHINTU_REPO` or resolved from the package | root holding `.venv/bin/chintu-gateway`, `var/local.secret`, `var/` and `contracts/` |
| `--gateway-args` | see below | arguments passed to `chintu-gateway` when starting it |
| `--interval` | `5` | status poll interval in seconds |
| `--mono` | off | monochrome icon |

Without `--gateway-args`, "Start daemon" runs
`chintu-gateway --config var/gateway.local.json serve --provider claude-agent --port <port>`
when `var/gateway.local.json` exists, otherwise the scripted fixture
`chintu-gateway --database var/chintu.sqlite3 serve --scripted --contract contracts/openapi.json --port <port>`.
If anything already listens on the port, the app starts nothing; whether it then treats
the listener as the gateway depends on the proof check above. The `status` subcommand
prints one JSON snapshot (state, `fault_kind`, secret file path, gateway command, Serve
status) without a tray, which is the headless way to check the configuration.

## Checks

```sh
.venv/bin/python -m unittest discover -s apps/desktop/tests -v
```

The tests cover state derivation from sample payloads, argument selection and the
"already listening" probe against a real ephemeral socket, icon rendering for every
state and size, `tailscale serve status` parsing from captured output, pairing-code
parsing and QR PNG rendering, autostart file rendering, and the menu with a stand-in
for pystray. `tests/fake_gateway.py` is an HTTP server on an ephemeral port that
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
