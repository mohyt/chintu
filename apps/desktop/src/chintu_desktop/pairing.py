"""In-app pairing QR: fetch a one-time code from the gateway and show it in a window.

`request_pairing_code`, `parse_pairing_code` and `render_qr_png` are plain functions
with no UI. `show_window` is the tkinter window; it runs in its own process
(`chintu-desktop qr-window`) so the tray's native event loop (AppKit on macOS, GLib
on GNOME) never competes with Tk for the main thread.

Each request mints a fresh secret on the gateway, so the window only asks when it
opens or when the user presses Refresh, never on a timer.
"""

import base64
import io
import json
import signal
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .status import DEFAULT_PORT, local_url

BACKGROUND = "#060316"
PANEL = "#18112C"
TEXT = "#F4F0FF"
MUTED = "#9691AE"
ACCENT = "#C7A9FF"
BORDER = "#48306D"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class PairingCode:
    payload: str      # exact JSON string the app's scanner expects
    url: str          # tailnet gateway URL
    name: str         # machine name shown in the app
    expires_in: int   # seconds


class PairingError(Exception):
    """A readable reason the gateway could not issue a code."""


def parse_pairing_code(body):
    """Validate the JSON body of `POST /local/pairing-code`."""
    try:
        data = json.loads(body)
    except ValueError as error:
        raise PairingError("gateway returned invalid JSON") from error
    if not isinstance(data, dict):
        raise PairingError("gateway returned an unexpected body")
    payload = data.get("payload")
    if not isinstance(payload, str) or not payload:
        raise PairingError("gateway response has no pairing payload")
    try:
        expires_in = int(data.get("expires_in") or 0)
    except (TypeError, ValueError) as error:
        raise PairingError("gateway response has an invalid expiry") from error
    return PairingCode(payload=payload, url=str(data.get("url") or ""), name=str(data.get("name") or "This computer"),
                       expires_in=max(0, expires_in))


def _error_message(raw, fallback):
    """Pull `error.message` from a gateway error body, if there is one."""
    try:
        data = json.loads(raw)
        message = data["error"]["message"]
        return str(message) if message else fallback
    except (ValueError, KeyError, TypeError):
        return fallback


def request_pairing_code(port=DEFAULT_PORT, timeout=5.0, opener=None):
    """POST to the loopback-only endpoint; raises `PairingError` with a readable reason."""
    request = urllib.request.Request(local_url(port, "/local/pairing-code"), method="POST",
                                     headers={"Accept": "application/json"}, data=b"")
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            return parse_pairing_code(response.read())
    except urllib.error.HTTPError as error:
        raw = error.read() if hasattr(error, "read") else b""
        if error.code == 503:
            raise PairingError(_error_message(raw, "Tailscale is not connected on this computer.")) from None
        if error.code == 403:
            raise PairingError("The gateway refused: pairing codes are only issued to this machine.") from None
        raise PairingError(_error_message(raw, "gateway answered HTTP %d" % error.code)) from None
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        reason = getattr(error, "reason", error)
        raise PairingError("The gateway is not running on port %d (%s)." % (port, reason)) from None


def render_qr_png(payload, scale=8):
    """QR for the payload as PNG bytes, in the pairing page's colours."""
    import segno  # pure Python; imported lazily so `--help` and tests without it stay fast

    buffer = io.BytesIO()
    # Dark modules on white: phone scanners do not decode inverted (light-on-dark) codes.
    segno.make(payload, error="m").save(buffer, kind="png", scale=scale, dark="#060316", light="#FFFFFF", border=3)
    return buffer.getvalue()


def countdown_text(seconds_left):
    if seconds_left <= 0:
        return "Expired. Press Refresh for a new code."
    minutes, seconds = divmod(int(seconds_left), 60)
    return "Expires in %d:%02d" % (minutes, seconds)


def show_window(port=DEFAULT_PORT, request=request_pairing_code, render=render_qr_png):
    """Open the pairing window and run its event loop until closed. Blocks."""
    import tkinter as tk
    from tkinter import font as tkfont

    root = tk.Tk()
    root.title("Pair Chintu")
    root.configure(bg=BACKGROUND)
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)  # surfaces the window above the browser/terminal when it opens
        root.after(600, lambda: root.attributes("-topmost", False))
    except tk.TclError:
        pass

    base = tkfont.nametofont("TkDefaultFont").actual()["family"]
    wordmark = tk.Label(root, text="C H I N T U", fg=ACCENT, bg=BACKGROUND, font=(base, 12))
    heading = tk.Label(root, text="Pair this computer", fg=TEXT, bg=BACKGROUND, font=(base, 20, "bold"))
    hint = tk.Label(root, text="In Chintu on your phone: Add a computer → Scan the QR code", fg=MUTED,
                    bg=BACKGROUND, font=(base, 12), wraplength=420, justify="center")
    panel = tk.Frame(root, bg=PANEL, highlightbackground=BORDER, highlightthickness=1, padx=18, pady=18)
    picture = tk.Label(panel, bg=PANEL)
    picture.pack()
    expiry = tk.Label(root, text="", fg=MUTED, bg=BACKGROUND, font=(base, 12))
    address = tk.Label(root, text="", fg=TEXT, bg=BACKGROUND, font=(base, 11), wraplength=440)
    status = tk.Label(root, text="", fg=ACCENT, bg=BACKGROUND, font=(base, 11), wraplength=440, justify="center")
    buttons = tk.Frame(root, bg=BACKGROUND)
    refresh = tk.Button(buttons, text="Refresh", command=lambda: load(), highlightbackground=BACKGROUND)
    close = tk.Button(buttons, text="Close", command=root.destroy, highlightbackground=BACKGROUND)
    refresh.pack(side="left", padx=6)
    close.pack(side="left", padx=6)

    for widget, pady in ((wordmark, (22, 2)), (heading, (0, 10)), (hint, (0, 14)), (panel, (0, 14)),
                         (expiry, (0, 2)), (address, (0, 6)), (status, (0, 6)), (buttons, (0, 20))):
        widget.pack(pady=pady, padx=28)

    state = {"deadline": None, "image": None, "timer": None}

    def tick():
        if state["deadline"] is not None:
            expiry.configure(text=countdown_text(state["deadline"] - time.monotonic()))
        state["timer"] = root.after(1000, tick)

    def load():
        status.configure(text="Requesting a fresh one-time code…")
        root.update_idletasks()
        try:
            code = request(port)
            png = render(code.payload)
        except PairingError as error:
            state["deadline"] = None
            picture.configure(image="", text="No code", fg=MUTED, width=24, height=8)
            expiry.configure(text="")
            status.configure(text=str(error))
            return
        except Exception as error:  # noqa: BLE001 - surface anything in the window, never a traceback
            state["deadline"] = None
            status.configure(text="Could not render the QR code: %s" % error)
            return
        state["image"] = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
        picture.configure(image=state["image"], text="", width=state["image"].width(), height=state["image"].height())
        heading.configure(text="Pair %s" % code.name)
        address.configure(text="Gateway address: %s" % code.url)
        state["deadline"] = time.monotonic() + code.expires_in
        status.configure(text="This code works once. Keep it on this screen.")
        expiry.configure(text=countdown_text(code.expires_in))

    raise_requested = {"flag": False}

    def on_raise(_signum, _frame):
        raise_requested["flag"] = True

    def watch_raise():
        if raise_requested["flag"]:
            raise_requested["flag"] = False
            root.deiconify()
            root.lift()
            root.focus_force()
        root.after(250, watch_raise)

    # The tray sends SIGUSR1 when the user picks "Show pairing QR" while the window is already open.
    try:
        signal.signal(signal.SIGUSR1, on_raise)
    except (ValueError, AttributeError, OSError):
        pass

    root.after(50, load)
    tick()
    watch_raise()
    root.mainloop()
