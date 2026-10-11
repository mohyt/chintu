"""Install/uninstall the login-time autostart entry for this user.

macOS: a launchd LaunchAgent in ~/Library/LaunchAgents (loaded at next login, or
`launchctl load` it now). Ubuntu: an XDG autostart .desktop file in ~/.config/autostart.
The templates are package data next to this module (so they travel inside the
PyInstaller bundle and the installed wheel) and use three placeholders:

- `__EXEC__`: the program to launch, from `Layout.desktop_executable` (the frozen
  bundle itself, `<venv>/bin/chintu-desktop`, or `<repo>/.venv/bin/chintu-desktop`);
- `__DATA__`: the Chintu folder (`Layout.home`), passed as `--home` and used for logs;
- `__HOME__`: the user's home directory, for `HOME` and `PATH` in the launchd environment.

The entries never `cd` into a repository; the app resolves its layout from `--home`.
"""

import sys
from importlib import resources
from pathlib import Path

PLIST_NAME = "ai.chintu.desktop.plist"
DESKTOP_NAME = "chintu-desktop.desktop"


def template_dir():
    """The package directory holding the two templates (a `Traversable`; a `Path` outside a bundle)."""
    return resources.files(__name__)


def target_path(platform=None, home=None):
    home = Path(home) if home else Path.home()
    if (platform or sys.platform) == "darwin":
        return home / "Library" / "LaunchAgents" / PLIST_NAME
    return home / ".config" / "autostart" / DESKTOP_NAME


def render_template(text, executable, data, home):
    return (text.replace("__EXEC__", str(executable)).replace("__DATA__", str(data))
            .replace("__HOME__", str(home)))


def install(layout, platform=None, home=None):
    """Write the rendered autostart file for `layout`. Returns the path and a next-step hint."""
    platform = platform or sys.platform
    home = Path(home) if home else Path.home()
    source = template_dir() / (PLIST_NAME if platform == "darwin" else DESKTOP_NAME)
    target = target_path(platform, home)
    target.parent.mkdir(parents=True, exist_ok=True)
    executable = layout.desktop_executable
    layout.ensure_home()  # launchd needs the log directory to exist
    target.write_text(render_template(source.read_text(), executable, layout.home, home))
    if platform == "darwin":
        hint = "Loads at next login; to start now: launchctl load %s" % target
    else:
        hint = "Starts at next GNOME login; to start now: %s" % executable
    return target, hint


def uninstall(platform=None, home=None):
    target = target_path(platform, home)
    if target.exists():
        target.unlink()
        return target, True
    return target, False
