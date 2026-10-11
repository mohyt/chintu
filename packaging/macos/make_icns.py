"""Render the app icon (`Chintu.icns`) from the procedurally drawn tray orb.

Usage: python packaging/macos/make_icns.py <output.icns>

Pillow draws the "connected" orb (`chintu_desktop.icons.render_icon`) at every size
Apple's iconset wants, then `iconutil` (macOS only) packs the iconset into an .icns.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# (file name, pixel size): the standard iconset members.
ICONSET = [("icon_16x16.png", 16), ("icon_16x16@2x.png", 32), ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
           ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256), ("icon_256x256.png", 256),
           ("icon_256x256@2x.png", 512), ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024)]


def write_iconset(directory):
    """Write the PNG set into `directory` (named `*.iconset`) and return it."""
    from chintu_desktop.icons import render_icon
    from chintu_desktop.status import Presence

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, size in ICONSET:
        render_icon(Presence.CONNECTED, size=size).save(directory / name, "PNG")
    return directory


def build_icns(output):
    output = Path(output).resolve()
    iconutil = shutil.which("iconutil")
    if iconutil is None:
        raise SystemExit("iconutil not found; the .icns can only be built on macOS")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        iconset = write_iconset(Path(scratch) / "Chintu.iconset")
        subprocess.run([iconutil, "-c", "icns", str(iconset), "-o", str(output)], check=True)
    return output


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: make_icns.py <output.icns>", file=sys.stderr)
        return 2
    print(build_icns(argv[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
