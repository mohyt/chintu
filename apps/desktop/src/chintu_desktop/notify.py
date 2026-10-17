"""Desktop notifications that carry Chintu's name and icon.

pystray's Linux notifier sends an empty application name and a temporary icon file, so
GNOME shows a nameless, iconless notification. On Linux this module calls the
freedesktop `Notify` method itself with the app name, the `chintu` theme icon installed by
the Debian package and the `desktop-entry` hint GNOME uses to match the notification to
`ai.chintu.desktop`. Other platforms keep pystray's notifier.
"""

import sys

APP_NAME = "Chintu"
ICON_NAME = "chintu"
DESKTOP_ENTRY = "ai.chintu"
TITLE = "Chintu"

DESTINATION = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"


def notify_args(title, message, replaces=0):
    """The `Notify` call arguments: (app_name, replaces_id, app_icon, summary, body, actions, hints, timeout)."""
    return (APP_NAME, replaces, ICON_NAME, title, message, [], {"desktop-entry": DESKTOP_ENTRY}, -1)


class LinuxNotifier:
    """Sends notifications over the session bus; each one replaces the previous so they do not pile up."""

    def __init__(self):
        from gi.repository import Gio, GLib
        self._glib = GLib
        self._gio = Gio
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._proxy = Gio.DBusProxy.new_sync(connection, 0, None, DESTINATION, PATH, DESTINATION, None)
        self._last_id = 0

    def notify(self, message, title=TITLE):
        app, replaces, icon, summary, body, actions, hints, timeout = notify_args(title, message, self._last_id)
        variant = self._glib.Variant("(susssasa{sv}i)", (
            app, replaces, icon, summary, body, actions,
            {key: self._glib.Variant("s", value) for key, value in hints.items()}, timeout))
        self._last_id = self._proxy.call_sync("Notify", variant, self._gio.DBusCallFlags.NONE, -1, None).unpack()[0]


def notifier_for(icon, platform=None):
    """A callable `notify(message)`, or None when this desktop cannot show notifications."""
    platform = platform or sys.platform
    if platform.startswith("linux"):
        try:
            return LinuxNotifier().notify
        except Exception:  # noqa: BLE001 - no session bus or no gi: fall back to pystray
            pass
    if getattr(icon, "HAS_NOTIFICATION", False):
        return lambda message: icon.notify(message, TITLE)
    return None
