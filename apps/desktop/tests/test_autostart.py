import plistlib
import tempfile
import unittest
from pathlib import Path

from chintu_desktop import autostart


class AutostartTests(unittest.TestCase):
    def test_templates_exist(self):
        folder = autostart.template_dir()
        self.assertTrue((folder / autostart.PLIST_NAME).is_file())
        self.assertTrue((folder / autostart.DESKTOP_NAME).is_file())

    def test_install_macos_renders_plist(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as repo:
            target, hint = autostart.install(repo, platform="darwin", home=home)
            self.assertEqual(target, Path(home) / "Library" / "LaunchAgents" / autostart.PLIST_NAME)
            data = plistlib.loads(target.read_bytes())
            self.assertEqual(data["Label"], "ai.chintu.desktop")
            self.assertEqual(data["ProgramArguments"][0], str(Path(repo).resolve() / ".venv" / "bin" / "chintu-desktop"))
            self.assertEqual(data["EnvironmentVariables"]["HOME"], home)
            self.assertNotIn("__REPO__", target.read_text())
            self.assertIn("launchctl load", hint)
            removed_target, removed = autostart.uninstall(platform="darwin", home=home)
            self.assertTrue(removed)
            self.assertFalse(removed_target.exists())
            self.assertFalse(autostart.uninstall(platform="darwin", home=home)[1])

    def test_install_linux_renders_desktop_entry(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as repo:
            target, hint = autostart.install(repo, platform="linux", home=home)
            self.assertEqual(target, Path(home) / ".config" / "autostart" / autostart.DESKTOP_NAME)
            text = target.read_text()
            self.assertIn("[Desktop Entry]", text)
            self.assertIn("Exec=%s/.venv/bin/chintu-desktop --repo %s run" % ((str(Path(repo).resolve()),) * 2), text)
            self.assertNotIn("__HOME__", text)
            self.assertIn("GNOME login", hint)


if __name__ == "__main__":
    unittest.main()
