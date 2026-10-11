"""Chintu Desktop entry point: the tray icon, its menu, and the CLI subcommands.

Threading model: pystray owns the main thread (`icon.run()` must be called there
on every backend). A daemon thread polls the gateway. The pairing QR window is a
separate `chintu-desktop qr-window` process so tkinter gets a main thread of its
own; see pairing.py.
"""

import argparse
import functools
import json
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import asdict

from . import __version__, autostart, daemon, local, pairing, status, tailscale
from .layout import Layout

SERVE_REFRESH_EVERY_POLLS = 6  # with a 5 s poll that is every 30 s
CONFIRM_WINDOW_SECONDS = 60    # a "Confirm ..." menu item disappears again after this long

SERVE_WARNING = "Tailscale Serve exposes the gateway to every device on your tailnet."


def _tray_backend_missing(error):
    """Print a readable hint instead of a traceback when pystray cannot start."""
    lines = ["chintu-desktop: no tray backend is available (%s)." % error]
    if sys.platform.startswith("linux"):
        lines += ["On Ubuntu/GNOME install the AppIndicator bindings:",
                  "  sudo apt install gir1.2-ayatanaappindicator3-0.1 python3-gi",
                  "and create the virtualenv with --system-site-packages (or symlink gi into it),",
                  "then enable the GNOME extension 'AppIndicator and KStatusNotifierItem Support'."]
    elif sys.platform == "darwin":
        lines += ["pystray needs pyobjc's AppKit on macOS; reinstall with:",
                  "  uv pip install --python .venv/bin/python -e apps/desktop"]
    print("\n".join(lines), file=sys.stderr)


def _display_available():
    if sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _call_directly(function, *args):
    function(*args)


def main_thread_caller():
    """Return `caller(function, *args)` that runs the function on the UI thread.

    pystray's GTK/AppIndicator backend already marshals icon updates onto the GLib
    loop, but its AppKit backend mutates the NSStatusItem on the calling thread and
    recent macOS releases trap (SIGTRAP in `-[NSStatusItem setMenu:]`) when that is
    not the main thread. pyobjc ships with pystray on macOS and `AppHelper.callAfter`
    queues the call onto the running NSApplication loop.
    """
    if sys.platform == "darwin":
        try:
            from PyObjCTools import AppHelper
            return AppHelper.callAfter
        except ImportError:
            pass
    return _call_directly


class DesktopApp:
    """Owns the tray icon, the gateway supervisor, the poller and the QR window process."""

    def __init__(self, pystray, args, call_on_main=None):
        self.pystray = pystray
        self._call_on_main = call_on_main or main_thread_caller()
        self.port = args.port
        self.layout = args.layout
        self.supervisor = daemon.GatewaySupervisor(self.layout, self.port, args.gateway_args)
        self.local = local.LocalClient(self.layout, self.port)
        self.poller = status.Poller(self.port, self.layout, args.interval, on_state=self._on_state)
        self.state = self.poller.state
        self.serve = tailscale.ServeStatus(False, False, None, "not checked yet")
        self.tailscale = tailscale.Installation(False, None, None, "Tailscale: not checked yet", None)
        self.tailscale_binary = tailscale.find_binary()
        self._poll_count = 0
        self._qr_process = None
        self._lock = threading.Lock()
        self.notice = None  # last action result, shown as a menu line
        self.no_daemon = bool(getattr(args, "no_daemon", False))
        self._pending = None  # (kind, target, asked_at): an action awaiting its "Confirm" click

        from .icons import icon_set
        self.images = icon_set(size=64, mono=args.mono)
        self.icon = pystray.Icon("chintu-desktop", self.images[status.Presence.DOWN], status.tooltip(self.state),
                                 menu=pystray.Menu(self._menu_items))

    # --- polling -------------------------------------------------------------------

    def _on_state(self, new_state):
        """Runs on the poller thread: do the slow probe here, touch the icon on the UI thread."""
        self._poll_count += 1
        if self._poll_count % SERVE_REFRESH_EVERY_POLLS == 1:
            self._refresh_tailscale()
        self._call_on_main(self._apply_state, new_state)

    def _refresh_tailscale(self):
        """Re-discover the CLI each time: installing Tailscale while the tray runs must be noticed."""
        self.tailscale_binary = tailscale.find_binary()
        self.tailscale = tailscale.installation(self.tailscale_binary)
        if self.tailscale.installed:
            self.serve = tailscale.serve_status(self.port, self.tailscale_binary)
        else:
            self.serve = tailscale.ServeStatus(False, False, None, "Tailscale is not installed")
            if self.notice is None:
                self.notice = self.tailscale.hint

    def _apply_state(self, new_state):
        previous = self.state
        self.state = new_state
        if previous.presence is not new_state.presence:
            self.icon.icon = self.images[new_state.presence]
        self.icon.title = status.tooltip(new_state)
        self.icon.update_menu()

    # --- menu -----------------------------------------------------------------------

    def _menu_items(self):
        MenuItem, Menu = self.pystray.MenuItem, self.pystray.Menu
        state = self.state
        listening = self.supervisor.listening()
        yield MenuItem(lambda item: status.phone_line(self.state), None, enabled=False)
        yield MenuItem(self._gateway_line(listening), None, enabled=False)
        if listening and not state.daemon_running and not self.supervisor.child_running():
            # F8: a listener that did not prove itself gets nothing but Retry and Quit.
            yield MenuItem(self._fault_line(), None, enabled=False)
            yield Menu.SEPARATOR
            yield MenuItem("Retry", self._retry)
            if self.notice:
                yield MenuItem(lambda item: self.notice or "", None, enabled=False)
            yield Menu.SEPARATOR
            yield MenuItem("Quit Chintu Desktop", self._quit)
            return
        yield MenuItem(lambda item: status.tasks_line(self.state), None, enabled=False)
        yield MenuItem(lambda item: status.gateway_url_line(self.state), None, enabled=False)
        yield MenuItem(lambda item: self.tailscale.message, None, enabled=False)
        if not self.tailscale.installed:
            yield MenuItem("Install Tailscale\u2026", self._install_tailscale)
        elif not self.tailscale.connected and self.tailscale.hint:
            yield MenuItem(lambda item: self.tailscale.hint, None, enabled=False)
        if self.tailscale.installed:
            yield MenuItem(lambda item: self._serve_line(), None, enabled=False)
        if self.serve.available and not self.serve.enabled:
            if self._pending_is("serve"):
                yield MenuItem("Confirm: expose the gateway to the whole tailnet", self._enable_serve_confirmed)
                yield MenuItem("Cancel", self._cancel_pending)
            else:
                yield MenuItem("Enable Tailscale Serve\u2026", self._ask_enable_serve)
        yield Menu.SEPARATOR
        yield MenuItem("Show pairing QR", self._show_qr, enabled=state.daemon_running, default=True)
        yield MenuItem("Open pairing page in browser", self._open_pair_page, enabled=state.daemon_running)
        yield MenuItem("Open console", self._open_console, enabled=state.daemon_running)
        if state.daemon_running:
            yield MenuItem(self._phones_title(), Menu(self._phones_items))
        yield Menu.SEPARATOR
        if self.supervisor.child_running():
            yield MenuItem("Stop daemon", self._stop_daemon)
        elif not state.daemon_running and not listening:
            # Offered only when nothing answers on the port, so a stale pre-poll menu cannot start a second gateway.
            yield MenuItem("Start daemon", self._start_daemon)
        yield MenuItem("Open daemon log", self._open_log, enabled=self.supervisor.log_path.exists())
        yield MenuItem("Open Chintu folder", self._open_folder)
        if self.notice:
            yield MenuItem(lambda item: self.notice or "", None, enabled=False)
        yield Menu.SEPARATOR
        yield MenuItem("Quit Chintu Desktop", self._quit)

    def _gateway_line(self, listening):
        state = self.state
        if state.daemon_running or self.supervisor.child_running() or not listening:
            return self.supervisor.describe(verified=state.daemon_running)
        if state.fault_kind == "no_secret":
            return "Gateway: not running, or not started from this Chintu folder"
        return self.supervisor.describe(verified=False)

    def _fault_line(self):
        state = self.state
        if state.fault_kind == "no_secret":
            return "Port %d is in use but %s is missing" % (self.port, self.layout.secret)
        if state.fault_kind == "refused":
            return "It refused the local proof (HTTP 403)"
        return state.error or "It did not answer like the gateway"

    def _serve_line(self):
        if not self.serve.available:
            return "Tailscale Serve: %s" % (self.serve.message or "unavailable")
        if self.serve.enabled:
            return "Tailscale Serve: on (%s)" % self.serve.url
        return "Tailscale Serve: off"

    # --- paired phones submenu ------------------------------------------------------------

    def _phones_title(self):
        count = self.state.paired_count
        if count == 0:
            return "Paired phones: none"
        return "Paired phones (%d)" % count

    def _client_text(self, client, now=None):
        parts = ["connected" if client.connected else "away"]
        expiry = local.expiry_text(client.expires_at, time.time() if now is None else now)
        if expiry:
            parts.append(expiry)
        return "%s: %s" % (client.name, ", ".join(parts))

    def _phones_items(self):
        MenuItem, Menu = self.pystray.MenuItem, self.pystray.Menu
        clients = self.state.clients
        if not clients:
            yield MenuItem("No phone is paired yet", None, enabled=False)
            return
        for client in clients:
            yield MenuItem(self._client_text(client), Menu(functools.partial(self._client_items, client)))
        yield Menu.SEPARATOR
        if self._pending_is("revoke", "*"):
            yield MenuItem("Confirm revoke all %d phone%s" % (len(clients), "" if len(clients) == 1 else "s"),
                           self._revoke_all_confirmed)
            yield MenuItem("Cancel", self._cancel_pending)
        else:
            yield MenuItem("Revoke all phones\u2026", functools.partial(self._ask_revoke, "*", "all phones"))

    def _client_items(self, client):
        MenuItem = self.pystray.MenuItem
        if self._pending_is("revoke", client.client_id):
            yield MenuItem("Confirm revoke %s" % client.name,
                           functools.partial(self._revoke_confirmed, client.client_id, client.name))
            yield MenuItem("Cancel", self._cancel_pending)
        else:
            yield MenuItem("Revoke %s\u2026" % client.name,
                           functools.partial(self._ask_revoke, client.client_id, client.name))

    # --- two-step confirmations -----------------------------------------------------------

    def _pending_is(self, kind, target=None):
        pending = self._pending
        if pending is None:
            return False
        if time.time() - pending[2] > CONFIRM_WINDOW_SECONDS:
            self._pending = None
            return False
        return pending[0] == kind and pending[1] == target

    def _ask(self, kind, target, message):
        self._pending = (kind, target, time.time())
        self._say(message)

    def _cancel_pending(self, icon, item):
        self._pending = None
        self._say("Cancelled.")

    # --- actions (run on the tray's UI thread; anything slow goes to a thread) ----------

    def _say(self, message):
        self.notice = message
        self._call_on_main(self.icon.update_menu)
        if getattr(self.icon, "HAS_NOTIFICATION", False):
            try:
                self.icon.notify(message, "Chintu Desktop")
            except Exception:  # noqa: BLE001 - notifications are best effort
                pass

    def _in_background(self, work):
        threading.Thread(target=work, name="chintu-desktop-action", daemon=True).start()

    def _start_daemon(self, icon, item):
        self._say(self.supervisor.start())
        self.poller.poll_soon()

    def _stop_daemon(self, icon, item):
        def work():
            self._say(self.supervisor.stop())
            self.poller.poll_soon()
        self._in_background(work)

    def _retry(self, icon, item):
        self.notice = None
        self.poller.poll_soon()

    def _ask_enable_serve(self, icon, item):
        self._ask("serve", None, SERVE_WARNING + " Open the menu again and choose Confirm to go ahead.")

    def _enable_serve_confirmed(self, icon, item):
        if not self._pending_is("serve"):
            self._say("That confirmation expired; choose Enable Tailscale Serve again.")
            return
        self._pending = None

        def work():
            ok, message = tailscale.enable_serve(self.port, self.tailscale_binary)
            self.serve = tailscale.serve_status(self.port, self.tailscale_binary)
            self._say(message if ok else "Tailscale Serve: %s" % message)
        self._in_background(work)

    def _ask_revoke(self, client_id, name, icon, item):
        self._ask("revoke", client_id, "Revoking %s signs the phone out of this computer for good. Open Paired "
                                       "phones again and choose Confirm." % name)

    def _revoke_confirmed(self, client_id, name, icon, item):
        if not self._pending_is("revoke", client_id):
            self._say("That confirmation expired; choose Revoke again.")
            return
        self._pending = None
        self._in_background(lambda: self._revoke([(client_id, name)]))

    def _revoke_all_confirmed(self, icon, item):
        if not self._pending_is("revoke", "*"):
            self._say("That confirmation expired; choose Revoke all phones again.")
            return
        self._pending = None
        targets = [(client.client_id, client.name) for client in self.state.clients]
        self._in_background(lambda: self._revoke(targets))

    def _revoke(self, targets):
        """POST the revoke endpoint for each `(client_id, name)`; runs on a worker thread."""
        done, failed = [], []
        for client_id, name in targets:
            try:
                self.local.revoke(client_id)
                done.append(name)
            except (local.LocalError, ValueError) as error:
                failed.append("%s (%s)" % (name, error))
        if failed:
            self._say("Could not revoke %s" % "; ".join(failed))
        elif done:
            self._say("Revoked %s." % ", ".join(done))
        else:
            self._say("No phone to revoke.")
        self.poller.poll_soon()

    def _show_qr(self, icon, item):
        with self._lock:
            if self._qr_process is not None and self._qr_process.poll() is None:
                try:
                    self._qr_process.send_signal(signal.SIGUSR1)  # raise the existing window
                except OSError:
                    pass
                return
            command = qr_window_command(self.layout, self.port)
            try:
                self._qr_process = subprocess.Popen(command, cwd=str(self.layout.home), stdin=subprocess.DEVNULL)
            except OSError as error:
                self._say("Could not open the pairing window: %s" % error)

    def _open_pair_page(self, icon, item):
        # Built here from the port and the layout's local.secret; a URL from the server is never trusted.
        try:
            url = self.local.pair_url()
        except local.SecretMissing as error:
            self._say(str(error))
            return
        self._open(url)

    def _open_console(self, icon, item):
        # The gateway has no separate console yet; the pairing page is the operator page for now.
        self._open_pair_page(icon, item)

    def _open(self, url):
        webbrowser.open(url)

    def _install_tailscale(self, icon, item):
        """Open the download page; the hint stays in the menu until Tailscale answers."""
        self._say(self.tailscale.hint or tailscale.install_hint())
        self._open(tailscale.INSTALL_URL)

    def _open_log(self, icon, item):
        self._open(self.supervisor.log_path.resolve().as_uri())

    def _open_folder(self, icon, item):
        """Reveal the Chintu folder (config, database, secret, logs) in Finder / the file manager."""
        try:
            self.layout.ensure_home()
            self._open_path(self.layout.home)
        except OSError as error:
            self._say("Could not open %s: %s" % (self.layout.home, error))

    def _open_path(self, path):
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)

    def _quit(self, icon, item):
        self.poller.stop()
        if self.supervisor.child_running():
            self.supervisor.stop()
        with self._lock:
            if self._qr_process is not None and self._qr_process.poll() is None:
                self._qr_process.terminate()
        icon.stop()

    # --- lifecycle --------------------------------------------------------------------

    def run(self):
        def show(icon):
            icon.visible = True

        def setup(icon):
            # pystray runs `setup` on a helper thread; showing the item is an AppKit mutation too.
            self._call_on_main(show, icon)
            self.autostart_daemon()
            self.poller.start()
        self.icon.run(setup=setup)

    def autostart_daemon(self):
        """An installed Chintu brings its daemon up with the tray; a checkout leaves that to the developer.

        Nothing is started when something already listens on the port: the poller then proves
        (or refuses) whatever is there, exactly as for a manual start.
        """
        if self.layout.kind == "repo" or getattr(self, "no_daemon", False):
            return False
        if self.supervisor.listening() or self.supervisor.child_running():
            return False
        self._say(self.supervisor.start())
        return True


# --- CLI ----------------------------------------------------------------------------

def is_frozen():
    """True inside a PyInstaller bundle, where this executable is also the gateway."""
    return bool(getattr(sys, "frozen", False))


def qr_window_command(layout, port, frozen=None, executable=None):
    """The child process that shows the pairing window, resolving the same layout as the tray."""
    frozen = is_frozen() if frozen is None else frozen
    executable = executable or sys.executable
    command = [executable] if frozen else [executable, "-m", "chintu_desktop.app"]
    command += ["--port", str(port)]
    if layout.kind == "repo":
        command += ["--repo", str(layout.repo)]
    else:
        command += ["--home", str(layout.home)]
    return command + ["qr-window"]


def _shared_options(parser, defaults):
    """`--port/--repo/--home/--gateway-args`, accepted before or after the subcommand.

    The subparsers get `SUPPRESS` defaults so a value given before the subcommand is kept.
    """
    parser.add_argument("--port", type=int, default=defaults.get("port", argparse.SUPPRESS),
                        help="loopback port of the gateway (default %d)" % status.DEFAULT_PORT)
    parser.add_argument("--repo", type=_path, default=defaults.get("repo", argparse.SUPPRESS),
                        help="force the repository layout: a checkout holding .venv/bin/chintu-gateway, "
                             "contracts/ and var/ (default: CHINTU_REPO, or detected from this package)")
    parser.add_argument("--home", type=_path, default=defaults.get("home", argparse.SUPPRESS),
                        help="Chintu folder for the database, local secret, gateway.local.json and logs "
                             "(default: CHINTU_HOME, ~/Library/Application Support/Chintu on macOS, "
                             "$XDG_DATA_HOME/chintu elsewhere; <repo>/var in the repository layout)")
    parser.add_argument("--gateway-args", default=defaults.get("gateway_args", argparse.SUPPRESS),
                        help="arguments for chintu-gateway when starting it, as one shell-quoted string "
                             "(default: --config <home>/gateway.local.json serve --provider claude-agent, or the "
                             "scripted fixture when that config file does not exist)")


def build_parser():
    parser = argparse.ArgumentParser(prog="chintu-desktop",
                                     description="Menu bar / top panel companion for the local Chintu gateway")
    parser.add_argument("--version", action="version", version="chintu-desktop %s" % __version__)
    _shared_options(parser, dict(port=status.DEFAULT_PORT, repo=None, home=None, gateway_args=None))
    parser.add_argument("--interval", type=float, default=status.POLL_INTERVAL_SECONDS,
                        help="status poll interval in seconds (default %(default)s)")
    parser.add_argument("--mono", action="store_true", help="monochrome icon for light macOS menu bars")
    parser.add_argument("--no-daemon", action="store_true",
                        help="do not start the gateway with the tray (installed layouts start it automatically)")
    commands = parser.add_subparsers(dest="command")
    for name, help_text in (("run", "show the tray icon (default)"),
                            ("status", "print one status snapshot as JSON and exit (no tray)"),
                            ("qr-window", "open the pairing QR window on its own (used by the tray)"),
                            ("init", "create the Chintu folder and its default gateway.local.json when missing, "
                                     "then print the config path (no tray)"),
                            ("install-autostart", "install the login item for this user"),
                            ("uninstall-autostart", "remove the login item for this user")):
        _shared_options(commands.add_parser(name, help=help_text), {})
    return parser


def _path(value):
    from pathlib import Path
    return Path(value).expanduser().resolve()


def _snapshot(args):
    layout = args.layout
    payload, error = status.fetch_status(args.port, layout)
    clients = None
    if payload is not None:
        clients, _ = status.fetch_clients(args.port, layout)
    state = status.derive_state(payload, error, port=args.port, clients=clients)
    install = tailscale.installation()
    serve = tailscale.serve_status(args.port) if install.installed else tailscale.ServeStatus(
        False, False, None, "Tailscale is not installed")
    supervisor = daemon.GatewaySupervisor(layout, args.port, args.gateway_args)
    data = asdict(state)
    data["presence"] = state.presence.value
    data["layout"] = layout.describe()
    data["secret_file"] = str(local.secret_path(layout))
    data["gateway_line"] = supervisor.describe(verified=state.daemon_running)
    data["gateway_command"] = supervisor.command()
    data["tailscale"] = asdict(install)
    data["tailscale_serve"] = asdict(serve)
    return data


def _init(layout):
    """`chintu-desktop init`: what "Start daemon" prepares, headless, for installers and smoke tests.

    Creates the Chintu folder (0700) and the default `gateway.local.json` (0600) when missing;
    a repository checkout never gets a generated config. Returns the config path either way.
    """
    layout.ensure_home()
    layout.ensure_config()
    return layout.config


def _run_gateway(argv):
    """`chintu-desktop gateway ...` is the gateway CLI; the frozen bundle starts its daemon this way."""
    from chintu_gateway.cli import main as gateway_main
    sys.argv = ["chintu-gateway"] + list(argv)
    return gateway_main()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "gateway":
        return _run_gateway(argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    args.layout = Layout.detect(repo=args.repo, home=args.home)
    command = args.command or "run"

    if command == "status":
        print(json.dumps(_snapshot(args), indent=2, default=str))
        return 0
    if command == "init":
        print(_init(args.layout))
        return 0
    if command == "install-autostart":
        target, hint = autostart.install(args.layout)
        print("Installed %s\n%s" % (target, hint))
        return 0
    if command == "uninstall-autostart":
        target, removed = autostart.uninstall()
        print("Removed %s" % target if removed else "Nothing to remove at %s" % target)
        return 0
    if command == "qr-window":
        if not _display_available():
            print("chintu-desktop: no display available for the pairing window.", file=sys.stderr)
            return 2
        pairing.show_window(args.port, args.layout)
        return 0

    if not _display_available():
        print("chintu-desktop: no display found (DISPLAY/WAYLAND_DISPLAY unset); the tray needs a desktop session.",
              file=sys.stderr)
        return 2
    try:
        import pystray
    except ImportError as error:
        _tray_backend_missing(error)
        return 2
    DesktopApp(pystray, args).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
