import unittest

from chintu_desktop import notify


class _Icon:
    HAS_NOTIFICATION = True

    def __init__(self):
        self.sent = []

    def notify(self, message, title):
        self.sent.append((title, message))


class NotifyTests(unittest.TestCase):
    def test_linux_call_names_the_app_icon_and_desktop_entry(self):
        app, replaces, icon, summary, body, actions, hints, timeout = notify.notify_args("Chintu", "Paired", 7)
        self.assertEqual((app, replaces, icon, summary, body), ("Chintu", 7, "chintu", "Chintu", "Paired"))
        self.assertEqual(hints, {"desktop-entry": "ai.chintu"})
        self.assertEqual((actions, timeout), ([], -1))

    def test_other_platforms_use_the_tray_icon(self):
        icon = _Icon()
        send = notify.notifier_for(icon, platform="darwin")
        send("Tailscale Serve enabled")
        self.assertEqual(icon.sent, [("Chintu", "Tailscale Serve enabled")])

    def test_no_notifications_available(self):
        class Silent:
            HAS_NOTIFICATION = False
        self.assertIsNone(notify.notifier_for(Silent(), platform="darwin"))


if __name__ == "__main__":
    unittest.main()
