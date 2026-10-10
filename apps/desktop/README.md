# Chintu Desktop: menu bar / top panel companion

One Python codebase that sits in the macOS menu bar or the Ubuntu GNOME top panel and
shows, at a glance, whether the paired phone can reach this computer. It is a companion
and supervisor for the [gateway](../../services/gateway/README.md); the daemon remains the
gateway, and this app never handles task text, audio, or bearer tokens.

## What it shows

| Menu line | Source |
| --- | --- |
| Phone: `<name>` connected / away / not paired yet | `clients` and `phone_connected` from `GET /local/status` |
| Gateway: running (pid, started here) / listening (started elsewhere) / not running | child process state and a loopback TCP probe |
| Active tasks: N (total M) | `active_tasks`, `total_tasks` |
| Tailnet URL: `https://<machine>.<tailnet>.ts.net` | `tailscale.gateway_url` |
| Tailscale Serve: on (`url`) / off | `tailscale serve status`, refreshed every 30 s |

The icon is a purple orb drawn procedurally with Pillow: a lit ring when the phone is
connected, a dim orb when it is paired but away, and a slashed orb when no gateway
answers on the loopback port. `--mono` switches to black-on-transparent shapes for a
light macOS menu bar. Status is polled every 5 s (`--interval`) from
`http://127.0.0.1:<port>/local/status`, which the gateway only answers for direct
loopback requests.

Menu actions:

- Show pairing QR: opens an in-app window (see below). Default action on a left click.
- Open pairing page in browser: `http://127.0.0.1:<port>/pair`, the fallback.
- Open console: the same page for now; the gateway has no separate console yet.
- Enable Tailscale Serve: runs `tailscale serve --bg <port>` when Serve is off.
- Start daemon / Stop daemon and Open daemon log (`var/desktop-gateway.log`).
- Quit: stops a daemon this app started, closes the QR window, removes the icon.

## How pairing works

"Show pairing QR" opens a dark window that calls `POST http://127.0.0.1:<port>/local/pairing-code`
once. The gateway mints a fresh one-time enrollment secret, and the window renders the
returned payload as a QR with `segno`, next to the machine name, the gateway URL and a
countdown. On the phone, open Chintu, tap Add a computer, then Scan the QR code. The
code works once and expires after ten minutes; Refresh mints another. The secret is
only ever shown on this screen: the endpoint refuses proxied requests (403), and the
window reports plainly when Tailscale is not connected (503) or the gateway is down.

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
| `--repo` | `CHINTU_REPO` or resolved from the package | root holding `.venv/bin/chintu-gateway`, `var/` and `contracts/` |
| `--gateway-args` | see below | arguments passed to `chintu-gateway` when starting it |
| `--interval` | `5` | status poll interval in seconds |
| `--mono` | off | monochrome icon |

Without `--gateway-args`, "Start daemon" runs
`chintu-gateway --config var/gateway.local.json serve --provider claude-agent --port <port>`
when `var/gateway.local.json` exists, otherwise the scripted fixture
`chintu-gateway --database var/chintu.sqlite3 serve --scripted --contract contracts/openapi.json --port <port>`.
If anything already listens on the port, the app attaches to it and starts nothing. The
`status` subcommand prints one JSON snapshot (state, gateway command, Serve status)
without a tray, which is the headless way to check the configuration.

## Checks

```sh
.venv/bin/python -m unittest discover -s apps/desktop/tests -v
```

The tests cover state derivation from sample payloads, argument selection and the
"already listening" probe against a real ephemeral socket, icon rendering for every
state and size, `tailscale serve status` parsing from captured output, pairing-code
parsing and QR PNG rendering, autostart file rendering, and the menu with a stand-in
for pystray. They need no display and never mint a pairing secret.

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
  macOS and the GNOME indicator on Ubuntu have not been exercised yet.
