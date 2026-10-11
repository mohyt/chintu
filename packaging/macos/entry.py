"""PyInstaller entry point for the macOS app: the tray by default, the gateway and the
QR window when the bundle re-executes itself with those subcommands (see `app.main`)."""

import sys

from chintu_desktop.app import main

if __name__ == "__main__":
    sys.exit(main())
