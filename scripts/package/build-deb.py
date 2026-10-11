#!/usr/bin/env python3
"""Build the `chintu-desktop` Debian packages for amd64 and arm64.

Standard library only (tarfile + a small `ar` writer), so it runs the same way on a
GitHub `ubuntu-24.04` runner and inside `python:3.12-slim` (see build-deb.sh for the
Docker wrapper used on macOS). Steps:

1. Build the three project wheels (chintu_adapters, chintu_gateway, chintu_desktop)
   with `python -m build --wheel` from a copy of each source tree, using a throwaway
   virtualenv under the build directory. They are pure Python, so one build serves both
   architectures.
2. Download the pinned Linux runtime wheels for CPython 3.12 per architecture with
   `pip download --only-binary=:all: --platform manylinux_...` (see
   packaging/debian/requirements-linux.txt). pip evaluates environment markers against
   the host, so this must run on Linux.
3. Render the 256 px icon with `chintu_desktop.icons`.
4. Assemble `dist/chintu-desktop_<version>_<arch>.deb`: `debian-binary`,
   `control.tar.gz` (control, md5sums, postinst, postrm) and `data.tar.gz`.

Set SOURCE_DATE_EPOCH for reproducible timestamps.

The package installs the wheels, the contract and launcher scripts under /opt/chintu;
postinst creates `/opt/chintu/venv` (with system site packages visible, so python3-gi's
`gi` is importable) and installs the wheels into it offline.
"""

import argparse
import gzip
import hashlib
import io
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE = "chintu-desktop"
MAINTAINER = "Mohit Srivastava <srivastavamohit91@gmail.com>"
HOMEPAGE = "https://github.com/mohyt/chintu"
SECTION = "utils"
PRIORITY = "optional"
SYNOPSIS = "Chintu desktop companion and gateway daemon"
LONG_DESCRIPTION = """\
Chintu lets the owner's phone send spoken tasks to this computer over a
private Tailscale network and hear the answers back. This package installs
the desktop companion (a tray icon for the GNOME top panel that shows phone
presence, shows the pairing QR code and supervises the gateway) together
with the gateway daemon, the Claude Agent SDK provider and every Python
dependency as wheels under /opt/chintu. On installation a private virtualenv
is created in /opt/chintu/venv from those wheels, with no network access
needed. The daemon binds to loopback only; use Tailscale Serve to reach it
from the phone."""

DEPENDS = ", ".join([
    "python3 (>= 3.12)",
    "python3-venv",
    "python3-tk",
    "python3-gi",
    "gir1.2-ayatanaappindicator3-0.1 | gir1.2-appindicator3-0.1",
    "libayatana-appindicator3-1 | libappindicator3-1",
])
RECOMMENDS = "tailscale, gnome-shell-extension-appindicator"

# Debian architecture -> (Python machine name used in wheel tags)
ARCHES = {"amd64": "x86_64", "arm64": "aarch64"}
PYTHON_VERSION = "3.12"
ABI = "cp312"
PROJECTS = (REPO / "packages" / "agent-adapters", REPO / "services" / "gateway", REPO / "apps" / "desktop")
PINS_FILE = REPO / "packaging" / "debian" / "requirements-linux.txt"
CONTRACT = REPO / "contracts" / "openapi.json"
OPT = "opt/chintu"
VENV = "/opt/chintu/venv"
WHEELS = "/opt/chintu/wheels"
CONTRACT_INSTALLED = "/opt/chintu/share/chintu/openapi.json"
ICON_SIZE = 256


# --- package contents --------------------------------------------------------------------

@dataclass(frozen=True)
class Entry:
    """One member of data.tar or control.tar. `data` for files, `link` for symlinks, neither for dirs."""

    path: str
    mode: int = 0o644
    data: bytes | None = None
    link: str | None = None

    @property
    def is_dir(self):
        return self.data is None and self.link is None


def wrapper(script):
    return f"""#!/bin/sh
# Launch {script} from the virtualenv the chintu-desktop package created at install time.
export CHINTU_CONTRACT="${{CHINTU_CONTRACT:-{CONTRACT_INSTALLED}}}"
exec {VENV}/bin/{script} "$@"
"""


DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=Chintu
Comment=Phone presence, pairing and gateway supervisor for Chintu
Exec=chintu-desktop run
Icon=chintu
Terminal=false
Categories=Utility;Network;
Keywords=chintu;tailscale;assistant;voice;
"""

POSTINST = f"""#!/bin/sh
# postinst for chintu-desktop: create the private virtualenv from the bundled wheels.
set -e

VENV={VENV}
WHEELS={WHEELS}

tailscale_hint() {{
    if ! command -v tailscale >/dev/null 2>&1; then
        cat <<'MSG'
================================================================================
 chintu-desktop: Tailscale is not installed.

 The phone reaches this computer only over your tailnet. Install it with:

     curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up

 then start Chintu and choose 'Enable Tailscale Serve' in the tray.
================================================================================
MSG
        return 0
    fi
    rc=0
    output="$(tailscale status 2>&1)" || rc=$?
    if [ "$rc" -ne 0 ] || printf '%s' "$output" | grep -qiE 'logged out|stopped'; then
        echo "chintu-desktop: Tailscale is installed but not running or not logged in; run: sudo tailscale up"
    fi
    return 0
}}

case "$1" in
    configure)
        # Recreate the environment from this package's wheels on every install and upgrade.
        rm -rf "$VENV"
        # --system-site-packages makes python3-gi's `gi` importable (pystray's AppIndicator backend).
        python3 -m venv --system-site-packages "$VENV"
        "$VENV/bin/pip" install --quiet --disable-pip-version-check --no-cache-dir \\
            --no-index --no-deps --ignore-installed --find-links "$WHEELS" "$WHEELS"/*.whl
        if command -v gtk-update-icon-cache >/dev/null 2>&1; then
            gtk-update-icon-cache -f -q /usr/share/icons/hicolor || true
        fi
        if command -v update-desktop-database >/dev/null 2>&1; then
            update-desktop-database -q || true
        fi
        tailscale_hint
        ;;
esac

exit 0
"""

POSTRM = f"""#!/bin/sh
# postrm for chintu-desktop: the virtualenv is created by postinst, so dpkg does not know about it.
set -e

case "$1" in
    remove|purge)
        rm -rf {VENV}
        rmdir /opt/chintu 2>/dev/null || true
        if command -v gtk-update-icon-cache >/dev/null 2>&1; then
            gtk-update-icon-cache -f -q /usr/share/icons/hicolor || true
        fi
        if command -v update-desktop-database >/dev/null 2>&1; then
            update-desktop-database -q || true
        fi
        ;;
esac

exit 0
"""

COPYRIGHT = """\
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: chintu
Upstream-Contact: Mohit Srivastava <srivastavamohit91@gmail.com>
Source: https://github.com/mohyt/chintu
Comment: The wheels under /opt/chintu/wheels are third-party Python packages
 (claude-agent-sdk, pystray, Pillow, FastAPI, Starlette, uvicorn, pydantic, mcp,
 jsonschema and their dependencies). Each wheel carries its own license text and
 metadata inside the archive; they are redistributed unmodified. The Claude Code
 CLI bundled inside the claude-agent-sdk wheel is subject to Anthropic's terms.

Files: *
Copyright: 2025-2026 Mohit Srivastava
License: Apache-2.0

License: Apache-2.0
 Licensed under the Apache License, Version 2.0 (the "License"); you may not
 use this file except in compliance with the License. You may obtain a copy of
 the License at
 .
     http://www.apache.org/licenses/LICENSE-2.0
 .
 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
 WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
 License for the specific language governing permissions and limitations under
 the License.
 .
 The full text ships as LICENSE in the source repository. On Debian systems,
 the complete text of the Apache License version 2.0 can be found in
 "/usr/share/common-licenses/Apache-2.0".
"""


def changelog(version, epoch):
    date = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(epoch))
    return (f"{PACKAGE} ({version}) unstable; urgency=medium\n\n"
            "  * Debian package: desktop companion, gateway daemon and bundled wheels\n"
            "    installed into a private virtualenv under /opt/chintu.\n\n"
            f" -- {MAINTAINER}  {date}\n")


def control_file(version, arch, installed_size_kib):
    long_lines = "".join(" " + (line if line.strip() else ".") + "\n" for line in LONG_DESCRIPTION.splitlines())
    return (f"Package: {PACKAGE}\n"
            f"Version: {version}\n"
            f"Architecture: {arch}\n"
            f"Maintainer: {MAINTAINER}\n"
            f"Installed-Size: {installed_size_kib}\n"
            f"Depends: {DEPENDS}\n"
            f"Recommends: {RECOMMENDS}\n"
            f"Section: {SECTION}\n"
            f"Priority: {PRIORITY}\n"
            f"Homepage: {HOMEPAGE}\n"
            f"Description: {SYNOPSIS}\n"
            f"{long_lines}")


def data_entries(version, arch, wheel_files, icon_png, epoch):
    """Everything installed by dpkg, as (path relative to /, no leading ./) entries."""
    entries = []
    for wheel in sorted(wheel_files, key=lambda p: p.name):
        entries.append(Entry(f"{OPT}/wheels/{wheel.name}", 0o644, wheel.read_bytes()))
    entries.append(Entry(f"{OPT}/share/chintu/openapi.json", 0o644, CONTRACT.read_bytes()))
    for script in ("chintu-desktop", "chintu-gateway"):
        entries.append(Entry(f"{OPT}/bin/{script}", 0o755, wrapper(script).encode()))
        entries.append(Entry(f"usr/bin/{script}", 0o777, link=f"/{OPT}/bin/{script}"))
    entries.append(Entry("usr/share/applications/ai.chintu.desktop", 0o644, DESKTOP_ENTRY.encode()))
    entries.append(Entry("usr/share/icons/hicolor/256x256/apps/chintu.png", 0o644, icon_png))
    entries.append(Entry(f"usr/share/doc/{PACKAGE}/copyright", 0o644, COPYRIGHT.encode()))
    # No Debian revision in the version, so dpkg/lintian treat the package as native: changelog.gz.
    entries.append(Entry(f"usr/share/doc/{PACKAGE}/changelog.gz", 0o644,
                         gzip.compress(changelog(version, epoch).encode(), 9, mtime=0)))
    entries.append(Entry(f"usr/share/lintian/overrides/{PACKAGE}", 0o644, LINTIAN_OVERRIDES.encode()))
    return entries


LINTIAN_OVERRIDES = f"""\
# The whole runtime (wheels, launchers and the virtualenv postinst creates) lives under
# /opt/chintu on purpose: it is a self-contained third-party package, not Debian-integrated software.
{PACKAGE}: dir-or-file-in-opt
# The launchers in /usr/bin are symlinks to /opt/chintu/bin; both are documented in
# deploy/README.md and `chintu-desktop --help` rather than manual pages.
{PACKAGE}: no-manual-page
"""


# --- archive writers ----------------------------------------------------------------------

def with_directories(entries):
    """Add a directory entry for every ancestor of every path, root first."""
    dirs = set()
    for entry in entries:
        parent = Path(entry.path).parent
        while str(parent) != ".":
            dirs.add(str(parent))
            parent = parent.parent
    result = [Entry(d, 0o755) for d in sorted(dirs)] + list(entries)
    result.sort(key=lambda e: e.path)
    return result


def tar_gz(entries, epoch):
    """A gzip'd GNU tar of `./`-prefixed entries, owned by root, with fixed timestamps."""
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar:
            root = tarfile.TarInfo("./")
            root.type, root.mode, root.mtime = tarfile.DIRTYPE, 0o755, epoch
            root.uname = root.gname = "root"
            tar.addfile(root)
            for entry in with_directories(entries):
                info = tarfile.TarInfo("./" + entry.path)
                info.mtime, info.uid, info.gid = epoch, 0, 0
                info.uname = info.gname = "root"
                if entry.is_dir:
                    info.type, info.mode = tarfile.DIRTYPE, 0o755
                    tar.addfile(info)
                elif entry.link is not None:
                    info.type, info.mode, info.linkname = tarfile.SYMTYPE, 0o777, entry.link
                    tar.addfile(info)
                else:
                    info.type, info.mode, info.size = tarfile.REGTYPE, entry.mode, len(entry.data)
                    tar.addfile(info, io.BytesIO(entry.data))
    return buffer.getvalue()


def ar_archive(members, epoch):
    """A classic `ar` archive: `!<arch>\\n`, then 60-byte headers and 2-byte aligned members."""
    out = bytearray(b"!<arch>\n")
    for name, data in members:
        header = f"{name:<16}{epoch:<12d}{0:<6d}{0:<6d}{'100644':<8}{len(data):<10d}`\n".encode("ascii")
        assert len(header) == 60, header
        out += header + data
        if len(data) % 2:
            out += b"\n"
    return bytes(out)


def md5sums(entries):
    lines = [f"{hashlib.md5(e.data).hexdigest()}  {e.path}\n" for e in entries if e.data is not None]
    return "".join(lines).encode()


def write_deb(path, version, arch, entries, epoch):
    installed = math.ceil(sum(len(e.data) for e in entries if e.data is not None) / 1024)
    control = [
        Entry("control", 0o644, control_file(version, arch, installed).encode()),
        Entry("md5sums", 0o644, md5sums(entries)),
        Entry("postinst", 0o755, POSTINST.encode()),
        Entry("postrm", 0o755, POSTRM.encode()),
    ]
    deb = ar_archive([("debian-binary", b"2.0\n"),
                      ("control.tar.gz", tar_gz(control, epoch)),
                      ("data.tar.gz", tar_gz(entries, epoch))], epoch)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(deb)
    return path


# --- building the inputs ------------------------------------------------------------------

def run(command, **kwargs):
    print("+ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True, **kwargs)


def read_pins():
    pins = []
    for line in PINS_FILE.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            pins.append(line)
    return pins


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_name_version(path):
    name, version = path.name.split("-")[:2]
    return normalize(name), version


def tool_venv(build_dir, pillow_pin):
    """A throwaway virtualenv that provides pip, `build` and Pillow (for the icon)."""
    venv = build_dir / "venv"
    python = venv / "bin" / "python"
    if not python.exists():
        run([sys.executable, "-m", "venv", venv])
    run([python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "build", pillow_pin])
    return python


def build_project_wheels(python, build_dir, version):
    """Build each project's wheel from a copy of its tree so the checkout stays clean."""
    out = build_dir / "wheels-project"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for project in PROJECTS:
        copy = build_dir / "src" / project.name
        shutil.rmtree(copy, ignore_errors=True)
        shutil.copytree(project, copy, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build", "dist", "tests", ".venv"))
        run([python, "-m", "build", "--wheel", "--outdir", out, copy])
    wheels = sorted(out.glob("*.whl"))
    names = {wheel_name_version(w)[0]: wheel_name_version(w)[1] for w in wheels}
    expected = {"chintu-adapters", "chintu-gateway", "chintu-desktop"}
    if set(names) != expected or any(v != version for v in names.values()):
        sys.exit(f"build-deb: expected wheels {sorted(expected)} at version {version}, got {names}")
    return wheels


def download_wheels(python, build_dir, arch, pins):
    machine = ARCHES[arch]
    out = build_dir / f"wheels-{arch}"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    run([python, "-m", "pip", "download", "--disable-pip-version-check", "--only-binary=:all:",
         "--platform", f"manylinux_2_28_{machine}", "--platform", f"manylinux2014_{machine}",
         "--platform", f"manylinux_2_17_{machine}", "--python-version", PYTHON_VERSION,
         "--implementation", "cp", "--abi", ABI, "--abi", "none", "-d", out, *pins])
    return out


def check_wheels(arch, wheels, pins):
    """Every pin must be present at its version and nothing unpinned may have been pulled in."""
    pinned = {normalize(p.split("==")[0]): p.split("==")[1] for p in pins}
    found = {}
    for wheel in wheels:
        name, version = wheel_name_version(wheel)
        found[name] = version
        tag = wheel.name[:-4].split("-")[-1]
        if tag not in ("any", ARCHES[arch]) and not tag.endswith(ARCHES[arch]):
            sys.exit(f"build-deb: {wheel.name} is not a {arch} wheel")
    missing = {n: v for n, v in pinned.items() if found.get(n) != v}
    extra = sorted(set(found) - set(pinned))
    if missing or extra:
        sys.exit(f"build-deb: {arch} wheel set does not match {PINS_FILE.name}: "
                 f"missing/mismatched {missing}, unpinned {extra}")


def render_icon(python, build_dir):
    out = build_dir / "chintu.png"
    code = (
        "import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]\n"
        "from chintu_desktop.icons import render_icon\n"
        "from chintu_desktop.status import Presence\n"
        f"render_icon(Presence.CONNECTED, size={ICON_SIZE}).save(sys.argv[3], 'PNG')\n"
    )
    run([python, "-c", code, REPO / "apps" / "desktop" / "src", REPO / "packages" / "agent-adapters" / "src", out])
    return out.read_bytes()


def find_wheels(wheels_dir, arch):
    for candidate in (wheels_dir / arch, wheels_dir / ARCHES[arch], wheels_dir):
        wheels = sorted(candidate.glob("*.whl"))
        if wheels:
            return wheels
    sys.exit(f"build-deb: no wheels for {arch} under {wheels_dir}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arch", action="append", choices=sorted(ARCHES), help="repeatable; default: both")
    parser.add_argument("--wheels-dir", type=Path,
                        help="directory holding already downloaded runtime wheels per arch "
                             "(<dir>/amd64, <dir>/x86_64, ...) instead of running pip download")
    parser.add_argument("--out", type=Path, default=REPO / "dist", help="output directory (default dist/)")
    parser.add_argument("--build-dir", type=Path, default=REPO / "build" / "deb")
    args = parser.parse_args(argv)

    if sys.platform == "darwin":
        sys.exit("build-deb: wheel resolution must run on Linux (pip evaluates pystray's platform markers "
                 "against the host). On a Mac run scripts/package/build-deb.sh, which runs this script "
                 "inside python:3.12-slim with Docker.")
    if sys.version_info[:2] != (3, 12):
        print(f"build-deb: warning: running under Python {platform.python_version()}, package targets 3.12",
              file=sys.stderr)

    version = (REPO / "VERSION").read_text().strip()
    arches = args.arch or sorted(ARCHES)
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH") or time.time())
    pins = read_pins()
    pillow_pin = next(p for p in pins if normalize(p.split("==")[0]) == "pillow")

    build_dir = args.build_dir.resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    python = tool_venv(build_dir, pillow_pin)
    project_wheels = build_project_wheels(python, build_dir, version)
    icon_png = render_icon(python, build_dir)

    outputs = []
    for arch in arches:
        if args.wheels_dir:
            runtime = find_wheels(args.wheels_dir.resolve(), arch)
        else:
            runtime = sorted(download_wheels(python, build_dir, arch, pins).glob("*.whl"))
        check_wheels(arch, runtime, pins)
        entries = data_entries(version, arch, project_wheels + runtime, icon_png, epoch)
        deb = write_deb(args.out.resolve() / f"{PACKAGE}_{version}_{arch}.deb", version, arch, entries, epoch)
        outputs.append(deb)
        print(f"built {deb} ({deb.stat().st_size / 1_000_000:.1f} MB, {len(runtime)} runtime wheels)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
