"""Layout detection for the three ways the app runs, the default data folder, and the generated config."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chintu_desktop import layout as layout_module
from chintu_desktop.layout import Layout, data_home, default_config

REPO = Path(__file__).resolve().parents[3]


def _fake_venv(root):
    """A pretend virtualenv at <root>/venv with a python and a chintu-gateway script."""
    binary = Path(root) / "venv" / "bin"
    binary.mkdir(parents=True)
    (binary / "python").write_text("")
    (binary / "chintu-gateway").write_text("")
    return binary / "python"


class DetectionTests(unittest.TestCase):
    def test_explicit_repo_argument(self):
        with tempfile.TemporaryDirectory() as root:
            layout = Layout.detect(repo=root, env={})
            repo = Path(root).resolve()
        self.assertEqual(layout.kind, "repo")
        self.assertEqual(layout.repo, repo)
        self.assertEqual(layout.home, repo / "var")
        self.assertEqual(layout.gateway_command, (str(repo / ".venv" / "bin" / "chintu-gateway"),))
        self.assertEqual(layout.contract, repo / "contracts" / "openapi.json")
        self.assertEqual(layout.config, repo / "var" / "gateway.local.json")
        self.assertEqual(layout.database, repo / "var" / "chintu.sqlite3")
        self.assertEqual(layout.secret, repo / "var" / "local.secret")
        self.assertEqual(layout.gateway_log, repo / "var" / "desktop-gateway.log")
        self.assertEqual(layout.desktop_executable, repo / ".venv" / "bin" / "chintu-desktop")
        self.assertEqual(layout.notes_dir, Path.home() / "Documents" / "Chintu Notes")

    def test_repo_env_wins_over_frozen_and_home(self):
        with tempfile.TemporaryDirectory() as root:
            layout = Layout.detect(env={"CHINTU_REPO": root, "CHINTU_HOME": "/elsewhere"}, frozen=True)
        self.assertEqual((layout.kind, layout.home), ("repo", Path(root).resolve() / "var"))

    def test_package_inside_checkout_is_the_repo_layout(self):
        layout = Layout.detect(env={})
        self.assertEqual(layout.kind, "repo")
        self.assertEqual(layout.repo, REPO)
        self.assertEqual(layout.home, REPO / "var")
        self.assertTrue(layout.contract.is_file())

    def test_frozen_bundle(self):
        with tempfile.TemporaryDirectory() as root:
            bundle = Path(root) / "Chintu.app" / "Contents"
            with patch.object(sys, "_MEIPASS", str(bundle / "Frameworks"), create=True):
                layout = Layout.detect(env={"CHINTU_HOME": str(Path(root) / "data")}, frozen=True,
                                       executable=str(bundle / "MacOS" / "Chintu"), platform="darwin")
            self.assertEqual(layout.kind, "bundle")
            self.assertEqual(layout.gateway_command, (str(bundle / "MacOS" / "Chintu"), "gateway"))
            self.assertEqual(layout.contract, bundle / "Frameworks" / "contracts" / "openapi.json")
            self.assertEqual(layout.home, (Path(root) / "data").resolve())
            self.assertEqual(layout.desktop_executable, bundle / "MacOS" / "Chintu")

    def test_frozen_without_meipass_uses_the_executable_folder(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(hasattr(sys, "_MEIPASS"))
            layout = Layout.detect(env={"CHINTU_HOME": root}, frozen=True, executable="/opt/chintu/bin/chintu")
        self.assertEqual(layout.contract, Path("/opt/chintu/bin/contracts/openapi.json"))

    def test_installed_venv(self):
        with tempfile.TemporaryDirectory() as root, patch.object(layout_module, "_package_checkout", lambda: None):
            python = _fake_venv(root)
            share = Path(root) / "share" / "chintu"
            share.mkdir(parents=True)
            (share / "openapi.json").write_text("{}")
            layout = Layout.detect(env={"CHINTU_HOME": str(Path(root) / "data")}, frozen=False, executable=str(python))
            self.assertEqual(layout.kind, "installed")
            self.assertEqual(layout.gateway_command, (str(python.parent / "chintu-gateway"),))
            self.assertEqual(layout.desktop_executable, python.parent / "chintu-desktop")
            self.assertEqual(layout.contract, share / "openapi.json")  # <prefix>/../share/chintu
            venv_share = Path(root) / "venv" / "share" / "chintu"
            venv_share.mkdir(parents=True)
            (venv_share / "openapi.json").write_text("{}")
            layout = Layout.detect(env={"CHINTU_HOME": str(Path(root) / "data")}, frozen=False, executable=str(python))
            self.assertEqual(layout.contract, venv_share / "openapi.json")  # <prefix>/share/chintu wins
            override = Path(root) / "custom.json"
            override.write_text("{}")
            env = {"CHINTU_HOME": str(Path(root) / "data"), "CHINTU_CONTRACT": str(override)}
            layout = Layout.detect(env=env, frozen=False, executable=str(python))
            self.assertEqual(layout.contract, override)
            env["CHINTU_CONTRACT"] = str(Path(root) / "missing.json")
            layout = Layout.detect(env=env, frozen=False, executable=str(python))
            self.assertEqual(layout.contract, venv_share / "openapi.json")

    def test_installed_without_any_contract_reports_none(self):
        with tempfile.TemporaryDirectory() as root, patch.object(layout_module, "_package_checkout", lambda: None):
            python = _fake_venv(root)
            layout = Layout.detect(env={"CHINTU_HOME": root}, frozen=False, executable=str(python))
        self.assertIsNone(layout.contract)
        self.assertIsNone(layout.describe()["contract"])

    def test_describe_is_json_friendly(self):
        with tempfile.TemporaryDirectory() as root:
            layout = Layout.detect(repo=root, env={})
        data = json.loads(json.dumps(layout.describe()))
        self.assertEqual(sorted(data), ["config", "contract", "gateway_command", "home", "kind"])
        self.assertEqual(data["kind"], "repo")
        self.assertEqual(data["gateway_command"], list(layout.gateway_command))


class DataHomeTests(unittest.TestCase):
    def test_explicit_home_then_env_then_platform_default(self):
        env = {"HOME": "/Users/alice", "CHINTU_HOME": "/Users/alice/chintu-data"}
        self.assertEqual(data_home("/tmp/explicit", env, "darwin"), Path("/tmp/explicit").resolve())
        self.assertEqual(data_home(None, env, "darwin"), Path("/Users/alice/chintu-data").resolve())
        self.assertEqual(data_home(None, {"HOME": "/Users/alice"}, "darwin"),
                         Path("/Users/alice/Library/Application Support/Chintu"))
        self.assertEqual(data_home(None, {"HOME": "/home/alice"}, "linux"), Path("/home/alice/.local/share/chintu"))
        self.assertEqual(data_home(None, {"HOME": "/home/alice", "XDG_DATA_HOME": "/data/xdg"}, "linux"),
                         Path("/data/xdg/chintu"))

    def test_detect_uses_data_home_for_non_repo_layouts(self):
        layout = Layout.detect(env={"HOME": "/home/bob"}, frozen=True, executable="/opt/chintu/chintu", platform="linux")
        self.assertEqual(layout.home, Path("/home/bob/.local/share/chintu"))
        layout = Layout.detect(home="/Volumes/Data/Chintu", env={}, frozen=True, executable="/opt/chintu/chintu")
        self.assertEqual(layout.home, Path("/Volumes/Data/Chintu"))

    def test_ensure_home_creates_owner_only_folder_once(self):
        with tempfile.TemporaryDirectory() as root:
            layout = Layout.detect(home=Path(root) / "nested" / "Chintu", env={}, frozen=True, executable="/x/chintu")
            self.assertTrue(layout.ensure_home())
            self.assertEqual(layout.home.stat().st_mode & 0o777, 0o700)
            os.chmod(layout.home, 0o750)
            self.assertFalse(layout.ensure_home())
            self.assertEqual(layout.home.stat().st_mode & 0o777, 0o750)  # an existing folder is left alone


class EnsureConfigTests(unittest.TestCase):
    def test_writes_default_config_once_and_owner_only(self):
        with tempfile.TemporaryDirectory() as root:
            layout = Layout.detect(home=Path(root) / "Chintu", env={}, frozen=True, executable="/x/chintu")
            self.assertTrue(layout.ensure_config())
            self.assertEqual(layout.config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(layout.home.stat().st_mode & 0o777, 0o700)
            written = json.loads(layout.config.read_text())
            self.assertEqual(written, default_config(layout))
            self.assertFalse(layout.ensure_config())  # already there: never overwritten

    def test_default_config_matches_the_gateway_schema(self):
        from chintu_gateway.config import load_config

        with tempfile.TemporaryDirectory() as root:
            layout = Layout.detect(home=Path(root) / "Chintu", env={}, frozen=True, executable="/x/chintu")
            layout.ensure_config()
            config = load_config(layout.config)
        self.assertEqual(config.settings.database, layout.database.resolve())
        self.assertEqual(config.settings.queue_limit, 10)
        self.assertEqual(config.settings.token_ttl_days, 30)
        self.assertEqual(list(config.settings.projects), ["home"])
        self.assertEqual(config.settings.projects["home"]["path"], Path.home().resolve())
        self.assertEqual(config.settings.projects["home"]["execution_profiles"], ["inspect", "develop"])
        self.assertEqual(config.settings.notes_dir, (Path.home() / "Documents" / "Chintu Notes").resolve())
        self.assertEqual([name for name, value in config.runtimes.items() if value["enabled"]], ["claude-agent"])
        agent = config.runtimes["claude-agent"]
        self.assertEqual((agent["executable"], agent["model"], agent["profiles"], agent["load_user_settings"]),
                         ("claude", "claude-opus-5-5", ["inspect", "develop"], True))
        self.assertIsNone(agent["persona"])
        for name in ("claude", "codex"):
            self.assertEqual(config.runtimes[name], {"enabled": False, "executable": name, "login_method": None,
                                                     "evidence": None})

    def test_default_config_mirrors_the_example_shape(self):
        example = json.loads((REPO / "config" / "gateway.example.json").read_text())
        with tempfile.TemporaryDirectory() as root:
            generated = default_config(Layout.detect(home=root, env={}, frozen=True, executable="/x/chintu"))
        self.assertEqual(sorted(generated), sorted(example))
        self.assertEqual(sorted(generated["runtimes"]), sorted(example["runtimes"]))
        for name in example["runtimes"]:
            self.assertEqual(sorted(generated["runtimes"][name]), sorted(example["runtimes"][name]), name)
        self.assertEqual(sorted(generated["projects"][0]), sorted(example["projects"][0]))

    def test_repo_layout_never_gets_a_generated_config(self):
        with tempfile.TemporaryDirectory() as root:
            layout = Layout.detect(repo=root, env={})
            self.assertFalse(layout.ensure_config())
            self.assertFalse(layout.config.exists())
            self.assertFalse(layout.home.exists())


if __name__ == "__main__":
    unittest.main()
