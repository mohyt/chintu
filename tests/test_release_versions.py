"""The one version in VERSION, spelt the way each package format wants it, and the installer's choices."""

import re
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text().strip()
PYPROJECTS = ("packages/agent-adapters", "services/gateway", "services/ataria", "apps/desktop")


def pep440(version):
    return re.sub(r"-rc([0-9]+)$", r"rc\1", version)


def sh(script):
    """Run bash with install.sh's functions loaded (main is not run)."""
    result = subprocess.run(["bash", "-c", f"set -euo pipefail; CHINTU_INSTALL_SOURCED=1 . ./install.sh; {script}"],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class VersionTests(unittest.TestCase):
    def test_version_file_is_a_release_or_a_candidate(self):
        self.assertRegex(VERSION, r"^[0-9]+\.[0-9]+\.[0-9]+(-rc[0-9]+)?$")

    def test_python_packages_match(self):
        for project in PYPROJECTS:
            with self.subTest(project=project):
                data = tomllib.loads((ROOT / project / "pyproject.toml").read_text())
                self.assertEqual(data["project"]["version"], pep440(VERSION))
        for module in ("apps/desktop/src/chintu_desktop/__init__.py", "services/ataria/src/chintu_ataria/__init__.py"):
            with self.subTest(module=module):
                found = re.search(r'__version__ = "([^"]+)"', (ROOT / module).read_text()).group(1)
                self.assertEqual(found, VERSION, "__version__ uses the VERSION spelling (shown to people)")

    def test_release_notes_exist(self):
        self.assertTrue((ROOT / "docs" / "releases" / f"v{VERSION}.md").is_file())

    def test_debian_spelling_sorts_candidates_first(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("build_deb", ROOT / "scripts/package/build-deb.py")
        build_deb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build_deb)
        self.assertEqual(build_deb.debian_version("0.7.0-rc1"), "0.7.0~rc1")
        self.assertEqual(build_deb.pep440("0.7.0-rc12"), "0.7.0rc12")
        self.assertEqual(build_deb.debian_version("0.7.0"), "0.7.0")
        try:
            older = subprocess.run(["dpkg", "--compare-versions", "0.7.0~rc1", "lt", "0.7.0"]).returncode == 0
            newer = subprocess.run(["dpkg", "--compare-versions", "0.7.0~rc1", "gt", "0.6.90"]).returncode == 0
        except FileNotFoundError:
            self.skipTest("dpkg is not available")
        self.assertTrue(older and newer)


class InstallerTests(unittest.TestCase):
    def test_vercmp(self):
        cases = [("0.6.0", "0.7.0-rc1", "-1"), ("0.7.0-rc1", "0.7.0", "-1"), ("0.7.0", "0.7.0-rc2", "1"),
                 ("0.7.0-rc2", "0.7.0-rc10", "-1"), ("0.7.0-rc1", "0.7.0-rc1", "0"), ("0.10.0", "0.9.9", "1"),
                 ("0.6.90", "0.7.0-rc1", "-1")]
        for a, b, expected in cases:
            with self.subTest(a=a, b=b):
                self.assertEqual(sh(f"vercmp {a} {b}"), expected)

    def test_options(self):
        self.assertEqual(sh("parse_args --pre --force --no-ataria --version 0.7.0-rc1; "
                            "echo $CHINTU_PRE $CHINTU_FORCE $CHINTU_ATARIA $CHINTU_VERSION"), "1 1 0 0.7.0-rc1")
        self.assertEqual(sh("parse_args --file=/x/chintu-desktop_0.7.0-rc1_amd64.deb --ataria; "
                            "echo $CHINTU_PACKAGE $CHINTU_ATARIA"), "/x/chintu-desktop_0.7.0-rc1_amd64.deb 1")
        with self.assertRaises(AssertionError):
            sh("parse_args --bogus")

    def test_asset_names_and_local_files(self):
        self.assertEqual(sh("OS=Linux ARCH=amd64; asset_name 0.7.0-rc1"), "chintu-desktop_0.7.0-rc1_amd64.deb")
        self.assertEqual(sh("OS=Darwin ARCH=arm64; asset_name 0.7.0-rc1"), "Chintu-0.7.0-rc1-arm64.dmg")
        version = sh("OS=Linux ARCH=amd64; WORK=$(mktemp -d); f=$WORK/in/chintu-desktop_0.7.0-rc1_amd64.deb; "
                     "mkdir -p $WORK/in; : > $f; CHINTU_PACKAGE=$f; package_from_file 2>/dev/null; echo $VERSION $TAG")
        self.assertEqual(version, "0.7.0-rc1 v0.7.0-rc1")
        with self.assertRaises(AssertionError):
            sh("OS=Linux ARCH=amd64; WORK=$(mktemp -d); f=$WORK/other.deb; : > $f; CHINTU_PACKAGE=$f; package_from_file")


if __name__ == "__main__":
    unittest.main()
