import plistlib
import tempfile
import unittest
from pathlib import Path

from chintu_desktop import autostart
from chintu_desktop.layout import Layout
from fake_gateway import repo_layout


class AutostartTests(unittest.TestCase):
    def test_templates_are_package_data(self):
        folder = autostart.template_dir()
        self.assertTrue((folder / autostart.PLIST_NAME).is_file())
        self.assertTrue((folder / autostart.DESKTOP_NAME).is_file())
        for name in (autostart.PLIST_NAME, autostart.DESKTOP_NAME):
            text = (folder / name).read_text()
            self.assertNotIn("__REPO__", text)
            self.assertNotIn("cd ", text)

    def test_install_macos_renders_plist_for_repo_layout(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as repo:
            layout = repo_layout(repo)
            target, hint = autostart.install(layout, platform="darwin", home=home)
            self.assertEqual(target, Path(home) / "Library" / "LaunchAgents" / autostart.PLIST_NAME)
            data = plistlib.loads(target.read_bytes())
            self.assertEqual(data["Label"], "ai.chintu.desktop")
            self.assertEqual(data["ProgramArguments"],
                             [str(Path(repo).resolve() / ".venv" / "bin" / "chintu-desktop"), "--home",
                              str(layout.home), "run"])
            self.assertNotIn("WorkingDirectory", data)
            self.assertEqual(data["EnvironmentVariables"]["HOME"], home)
            self.assertEqual(data["StandardOutPath"], str(layout.home / "desktop.log"))
            self.assertTrue(layout.home.is_dir())  # launchd needs the log directory
            for placeholder in ("__EXEC__", "__DATA__", "__HOME__"):
                self.assertNotIn(placeholder, target.read_text())
            self.assertIn("launchctl load", hint)
            removed_target, removed = autostart.uninstall(platform="darwin", home=home)
            self.assertTrue(removed)
            self.assertFalse(removed_target.exists())
            self.assertFalse(autostart.uninstall(platform="darwin", home=home)[1])

    def test_install_macos_bundle_launches_the_frozen_executable(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as data:
            layout = Layout("bundle", Path(data) / "Chintu", ("/Applications/Chintu.app/Contents/MacOS/Chintu", "gateway"),
                            Path("/bundle/contracts/openapi.json"))
            target, _ = autostart.install(layout, platform="darwin", home=home)
            arguments = plistlib.loads(target.read_bytes())["ProgramArguments"]
            self.assertEqual(arguments, ["/Applications/Chintu.app/Contents/MacOS/Chintu", "--home",
                                         str(layout.home), "run"])

    def test_install_linux_renders_desktop_entry_for_installed_layout(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as data:
            layout = Layout("installed", Path(data) / "chintu", ("/opt/chintu/venv/bin/chintu-gateway",),
                            Path("/opt/chintu/share/chintu/openapi.json"))
            target, hint = autostart.install(layout, platform="linux", home=home)
            self.assertEqual(target, Path(home) / ".config" / "autostart" / autostart.DESKTOP_NAME)
            text = target.read_text()
            self.assertIn("[Desktop Entry]", text)
            self.assertIn('Exec="/opt/chintu/venv/bin/chintu-desktop" --home "%s" run' % layout.home, text)
            self.assertNotIn("Path=", text)
            self.assertNotIn("__", text)
            self.assertIn("GNOME login", hint)
            self.assertIn("/opt/chintu/venv/bin/chintu-desktop", hint)


if __name__ == "__main__":
    unittest.main()
