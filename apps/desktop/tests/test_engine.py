import hashlib
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from chintu_desktop import engine


class EngineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        home = Path(temp.name)
        self.layout = SimpleNamespace(home=home, config=home / "gateway.local.json",
                                      desktop_executable=home / "chintu-desktop")
        self.layout.config.write_text(json.dumps({"version": 1, "database": "g.sqlite3", "projects": [], "runtimes": {}}))
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("ATARIA_PROFILE", None)

    def write_profile(self, **engine_settings):
        (self.layout.home / "ataria.json").write_text(json.dumps({"schema": 1, "vault": "/v", "owner": {"name": "Sam"},
                                                                 **({"engine": engine_settings} if engine_settings else {})}))

    def test_no_profile_no_engine(self):
        self.assertIsNone(engine.prepare(self.layout))
        supervisor = engine.AtariaSupervisor(self.layout, listening=lambda port: False)
        self.assertIsNone(supervisor.describe())
        self.assertIn("not set up", supervisor.start())

    def test_prepare_wires_gateway_and_engine(self):
        self.write_profile()
        changes = engine.prepare(self.layout)
        self.assertEqual(len(changes), 3)
        token = (self.layout.home / "ataria.token").read_text().strip()
        self.assertEqual(stat.S_IMODE((self.layout.home / "ataria.token").stat().st_mode), 0o600)
        hashed = (self.layout.home / "ataria" / "engine-token.sha256").read_text().strip()
        self.assertEqual(hashed, hashlib.sha256(token.encode()).hexdigest())
        config = json.loads(self.layout.config.read_text())
        self.assertEqual(config["ataria"], {"url": "http://127.0.0.1:8787/ataria/api/v1", "token_file": "ataria.token"})
        self.assertEqual(engine.prepare(self.layout), [])        # nothing more to do

    def test_existing_wiring_is_kept(self):
        token_file = self.layout.home / "elsewhere.token"
        token_file.write_text("k" * 40)
        config = json.loads(self.layout.config.read_text())
        config["ataria"] = {"url": "http://127.0.0.1:9000/x/api/v1", "token_file": str(token_file)}
        self.layout.config.write_text(json.dumps(config))
        hash_file = self.layout.home / "custom.sha256"
        hash_file.write_text("0" * 64)
        self.write_profile(token_sha256_file=str(hash_file), port=9000, base="/x")
        self.assertEqual(engine.prepare(self.layout), ["wrote the engine's token hash"])
        self.assertEqual(hash_file.read_text().strip(), hashlib.sha256(b"k" * 40).hexdigest())
        self.assertEqual(json.loads(self.layout.config.read_text())["ataria"]["url"], "http://127.0.0.1:9000/x/api/v1")
        self.assertEqual(token_file.read_text(), "k" * 40)

    def test_supervisor_starts_and_stops(self):
        self.write_profile()
        exe = self.layout.desktop_executable
        exe.write_text("#!/bin/sh\nexec sleep 30\n")
        exe.chmod(0o755)
        supervisor = engine.AtariaSupervisor(self.layout, listening=lambda port: False)
        self.assertEqual(supervisor.command(), [str(exe), "ataria", "serve"])
        self.assertIn("Started the Ataria engine", supervisor.start())
        self.assertTrue(supervisor.child_running())
        self.assertIn("running", supervisor.describe())
        self.assertIn("already running", supervisor.start())
        self.assertIn("Stopped", supervisor.stop(grace_seconds=5))
        self.assertFalse(supervisor.child_running())

    def test_a_busy_port_is_left_alone(self):
        self.write_profile()
        supervisor = engine.AtariaSupervisor(self.layout, listening=lambda port: True)
        self.assertIn("already in use", supervisor.start())
        self.assertIn("started elsewhere", supervisor.describe())
        self.assertFalse((self.layout.home / "ataria.token").exists())


if __name__ == "__main__":
    unittest.main()
