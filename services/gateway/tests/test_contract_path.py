"""Where the gateway finds contracts/openapi.json in a checkout, a PyInstaller bundle and an installed venv."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chintu_gateway import contract


class ContractResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _file(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
        return path

    def test_repo_checkout_is_the_default(self):
        self.assertEqual(contract.REPO_CONTRACT, Path(__file__).resolve().parents[3] / "contracts" / "openapi.json")
        self.assertTrue(contract.REPO_CONTRACT.is_file())
        self.assertEqual(contract.DEFAULT_CONTRACT, contract.REPO_CONTRACT)
        self.assertEqual(contract.resolve_contract(env={}, frozen=False, prefix=self.root), contract.REPO_CONTRACT)

    def test_order_env_bundle_repo_prefix(self):
        env_file = self._file("custom/openapi.json")
        bundle = self._file("bundle/contracts/openapi.json")
        venv_share = self._file("venv/share/chintu/openapi.json")
        beside = self._file("share/chintu/openapi.json")
        prefix = self.root / "venv"
        env = {"CHINTU_CONTRACT": str(env_file)}
        self.assertEqual(contract.resolve_contract(env, True, self.root / "bundle", prefix), env_file)
        self.assertEqual(contract.resolve_contract({}, True, self.root / "bundle", prefix), bundle)
        with patch.object(contract, "REPO_CONTRACT", self.root / "missing" / "openapi.json"):
            self.assertEqual(contract.resolve_contract({}, False, None, prefix), venv_share)
            venv_share.unlink()
            self.assertEqual(contract.resolve_contract({}, False, None, prefix), beside)
            beside.unlink()
            # Nothing exists: the repository path is kept so error messages name a sensible file.
            self.assertEqual(contract.resolve_contract({}, False, None, prefix), self.root / "missing" / "openapi.json")

    def test_env_override_is_skipped_when_missing_and_frozen_reads_meipass(self):
        bundle = self._file("Frameworks/contracts/openapi.json")
        env = {"CHINTU_CONTRACT": str(self.root / "nope.json")}
        with patch.object(sys, "_MEIPASS", str(self.root / "Frameworks"), create=True):
            self.assertEqual(contract.resolve_contract(env, True, None, self.root), bundle)
        candidates = contract.contract_candidates(env, True, self.root / "Frameworks", self.root / "venv")
        self.assertEqual(candidates, [self.root / "nope.json", bundle, contract.REPO_CONTRACT,
                                      self.root / "venv" / "share" / "chintu" / "openapi.json",
                                      self.root / "share" / "chintu" / "openapi.json"])


if __name__ == "__main__":
    unittest.main()
