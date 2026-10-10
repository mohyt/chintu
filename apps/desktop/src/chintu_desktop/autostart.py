"""Install/uninstall the login-time autostart entry for this user.

macOS: a launchd LaunchAgent in ~/Library/LaunchAgents (loaded at next login, or
`launchctl load` it now). Ubuntu: an XDG autostart .desktop file in ~/.config/autostart.
The templates live in apps/desktop/autostart/ and use `__REPO__` / `__HOME__`
placeholders, like the gateway units under deploy/.
"""

import sys
from pathlib import Path

PLIST_NAME = "ai.chintu.desktop.plist"
DESKTOP_NAME = "chintu-desktop.desktop"


def template_dir(repo=None):
    """apps/desktop/autostart, located from the package (editable install) or the repo."""
    packaged = Path(__file__).resolve().parents[2] / "autostart"
    if packaged.is_dir():
        return packaged
    if repo is not None:
        return Path(repo) / "apps" / "desktop" / "autostart"
    return packaged


def target_path(platform=None, home=None):
    home = Path(home) if home else Path.home()
    if (platform or sys.platform) == "darwin":
        return home / "Library" / "LaunchAgents" / PLIST_NAME
    return home / ".config" / "autostart" / DESKTOP_NAME


def render_template(text, repo, home):
    return text.replace("__REPO__", str(repo)).replace("__HOME__", str(home))


def install(repo, platform=None, home=None):
    """Write the rendered autostart file. Returns the path and a next-step hint."""
    platform = platform or sys.platform
    home = Path(home) if home else Path.home()
    source = template_dir(repo) / (PLIST_NAME if platform == "darwin" else DESKTOP_NAME)
    target = target_path(platform, home)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_template(source.read_text(), Path(repo).resolve(), home))
    if platform == "darwin":
        hint = "Loads at next login; to start now: launchctl load %s" % target
    else:
        hint = "Starts at next GNOME login; to start now: %s/.venv/bin/chintu-desktop" % repo
    return target, hint


def uninstall(platform=None, home=None):
    target = target_path(platform, home)
    if target.exists():
        target.unlink()
        return target, True
    return target, False
