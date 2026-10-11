# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the macOS app bundle: one-folder build wrapped as `Chintu.app`.

Run it through scripts/package/build-dmg.sh, which prepares the build venv this spec
expects (packaging/requirements-runtime.txt plus the three Chintu packages) and then:

    python -m PyInstaller --noconfirm --clean packaging/macos/chintu.spec \
        --distpath build/macos/dist --workpath build/macos/work

The executable inside the bundle is `Contents/MacOS/chintu-desktop`; it is the tray by
default and re-executes itself as `chintu-desktop gateway ...` for the daemon and
`chintu-desktop ... qr-window` for the pairing window (see chintu_desktop.app.main and
chintu_desktop.layout for the "bundle" layout). Everything the gateway needs at run
time travels as data: the shared contract, the autostart templates, jsonschema's
format metadata and the Claude Code CLI the claude-agent-sdk package ships.
"""

import importlib.util
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

if sys.platform != "darwin":
    raise SystemExit("chintu.spec builds the macOS app bundle; run it on macOS")

SPEC_DIR = Path(SPECPATH).resolve()          # packaging/macos (SPECPATH is set by PyInstaller)
ROOT = SPEC_DIR.parents[1]
VERSION = (ROOT / "VERSION").read_text().strip()
APP_NAME = "Chintu"
EXECUTABLE_NAME = "chintu-desktop"
BUNDLE_ID = "ai.chintu.desktop"
ICON = Path(os.environ.get("CHINTU_ICNS") or ROOT / "build" / "macos" / "Chintu.icns")


def package_dir(name):
    found = importlib.util.find_spec(name)
    if found is None or not found.origin:
        raise SystemExit("%s is not installed in the build environment" % name)
    return Path(found.origin).parent


contract = ROOT / "contracts" / "openapi.json"
# The adapter falls back to this CLI when no `claude` is on PATH (ClaudeAgentAdapter.bundled_executable);
# it must keep its path relative to the claude_agent_sdk package and its executable bit.
bundled_cli = package_dir("claude_agent_sdk") / "_bundled" / "claude"
for required in (contract, bundled_cli):
    if not required.is_file():
        raise SystemExit("missing build input: %s" % required)

if not ICON.is_file():
    sys.path.insert(0, str(SPEC_DIR))
    from make_icns import build_icns
    build_icns(ICON)

datas = [
    (str(contract), "contracts"),
    (str(bundled_cli), "claude_agent_sdk/_bundled"),
]
datas += collect_data_files("chintu_desktop")                 # autostart/*.plist, *.desktop
datas += collect_data_files("claude_agent_sdk", excludes=["_bundled/*"])
datas += collect_data_files("jsonschema_specifications")       # format/vocabulary JSON used by FormatChecker
for distribution in ("claude-agent-sdk", "mcp", "fastapi", "starlette", "uvicorn",
                     "chintu-desktop", "chintu-gateway", "chintu-adapters"):
    try:
        datas += copy_metadata(distribution)
    except Exception:  # noqa: BLE001 - metadata is a convenience for importlib.metadata.version() lookups
        pass

hiddenimports = (
    collect_submodules("chintu_desktop")
    + collect_submodules("chintu_gateway")
    + collect_submodules("chintu_adapters")      # agent_sdk is imported by name from the operator config
    + collect_submodules("claude_agent_sdk")     # imported lazily with importlib by the adapter
    + collect_submodules("uvicorn")              # loops/protocols/lifespan are chosen by string at run time
    + [
        "pystray._darwin", "objc", "AppKit", "Foundation", "PyObjCTools.AppHelper",
        "PIL.Image", "PIL.ImageDraw", "PIL.PngImagePlugin",
        "tkinter", "_tkinter",
        "segno",
    ]
)

a = Analysis(
    [str(SPEC_DIR / "entry.py")],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=EXECUTABLE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # a menu-bar app; stdio still works when run from a terminal on macOS
    disable_windowed_traceback=False,
    argv_emulation=False,   # `open Chintu.app --args ...` passes arguments straight through
    target_arch=None,       # native: arm64 on Apple silicon, x86_64 on Intel
    codesign_identity=None, # ad-hoc; build-dmg.sh re-signs the finished bundle
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=EXECUTABLE_NAME,
)
app = BUNDLE(
    coll,
    name="%s.app" % APP_NAME,
    icon=str(ICON),
    bundle_identifier=BUNDLE_ID,
    version=VERSION,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "CFBundlePackageType": "APPL",
        "LSUIElement": True,                     # menu-bar only: no Dock icon, no app switcher entry
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "Copyright 2026 Mohit Srivastava. Apache License 2.0.",
    },
)
