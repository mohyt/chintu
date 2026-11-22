import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chintu_ataria import profile, setup


def first_run_answers(vault):
    return [
        # 1. You
        "Sam", "Sam Example", "Head of Delivery", "sam@example.com", "Singapore", "Asia/Singapore", "en-GB",
        # 2. Vault, and yes to creating it
        str(vault), "y",
        # 3. Schedule
        "7am", "07:00", "", "n",
        # 4. Agents: default, then work, meetings, personal, finance, notes
        "codex", "claude", "claude", "", "", "",
        # 5. Work
        "y", "Example Systems", "", "Northwind Bank, Contoso Telecom", "Ledgerline", "Priya, Tom", "example-systems",
        # 6. Family: add one, then stop
        "y", "Robin", "child", "Example Primary School", "-", "n",
        # 7. Sources: feeds (+ packs), google, github, notes, meetings
        "y", "world, tech, ai", "y", "n", "y", "n",
        # 8. Delivery: phone, add one recipient, stop
        "y", "y", "Alex", "whatsapp", "-", "morning, evening, weekly", "n",
        # 9. Context: work, family, style, alert feeds
        "Escalations first.", "", "Short sentences.", "-",
        # 10. Secrets: add one, stop
        "y", "statements", "file", "~/statement-keys.txt", "n",
        # 11. Privacy
        "-",
        # Save?
        "y",
    ]


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.vault = self.base / "vault"
        self.path = self.base / "chintu" / "ataria.json"
        self.out = io.StringIO()

    def run_init(self, answers):
        return setup.init(self.path, setup.Prompter(stdout=self.out, answers=answers))

    def test_first_run_asks_everything_and_writes_a_valid_private_profile(self):
        value = self.run_init(first_run_answers(self.vault))
        self.assertTrue(self.vault.is_dir(), "the vault is created when the owner agrees")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        saved = profile.load(self.path)
        self.assertEqual(saved, value)
        self.assertEqual(saved["owner"]["name"], "Sam")
        self.assertEqual(saved["schedule"], {"morning": "07:00", "midday": "13:30", "catch_up": False})
        self.assertEqual(saved["agents"], {"default": "codex", "lanes": {"work": "claude", "meetings": "claude"}})
        self.assertEqual(saved["work"]["clients"], ["Northwind Bank", "Contoso Telecom"])
        self.assertEqual(saved["work"]["role"], "Head of Delivery", "work role defaults to the owner's role")
        self.assertEqual(saved["family"], [{"name": "Robin", "relation": "child", "school": "Example Primary School"}])
        self.assertEqual(saved["sources"]["feeds"], {"enabled": True, "packs": ["world", "tech", "ai"]})
        self.assertEqual(saved["sources"]["google"], {"enabled": True, "via": "codex-connector"})
        self.assertFalse(saved["sources"]["github"]["enabled"])
        self.assertEqual(saved["delivery"]["recipients"],
                         [{"name": "Alex", "channel": "whatsapp", "digests": ["morning", "evening"]}])
        self.assertEqual(saved["context"], {"work": "Escalations first.", "style": "Short sentences."})
        self.assertEqual(saved["secrets"], {"statements": {"store": "file", "ref": "~/statement-keys.txt"}})
        self.assertIsNone(saved["privacy"]["deny_patterns_file"])
        text = self.out.getvalue()
        self.assertIn("Use 24-hour HH:MM", text, "a bad time is asked again")
        self.assertIn("Summary", text)

    def test_rerun_edits_and_keeps_answers_and_unknown_keys(self):
        self.run_init(first_run_answers(self.vault))
        data = json.loads(self.path.read_text())
        data["future_feature"] = {"x": 1}
        profile.save(data, self.path)
        before = profile.load(self.path)
        # Enter everywhere keeps each answer; y/N questions take their defaults; then "y" to save.
        value = self.run_init([""] * 200)
        self.assertEqual(value, before)
        self.assertEqual(len(list(self.path.parent.glob("ataria.json.*.bak"))), 1, "the old file is kept as a backup")
        # Changing one answer changes only that field.
        answers = [""] * 200
        answers[0] = "Samantha"
        self.assertEqual(self.run_init(answers)["owner"]["name"], "Samantha")

    def test_declining_saves_nothing(self):
        answers = first_run_answers(self.vault)
        answers[-1] = "n"
        self.assertIsNone(self.run_init(answers))
        self.assertFalse(self.path.exists())

    def test_doctor(self):
        self.run_init(first_run_answers(self.vault))
        home = self.base / "home"
        (home / ".codex").mkdir(parents=True)
        (home / ".codex" / "auth.json").write_text("{}")
        out = io.StringIO()
        which = lambda name: "/usr/bin/" + name if name in ("codex", "claude") else None
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            code, results = setup.doctor(self.path, out, which, home)
        levels = {check: level for level, check, _ in results}
        self.assertEqual(levels["profile"], "ok")
        self.assertEqual(levels["templates"], "ok")
        self.assertEqual(levels["vault"], "ok")
        self.assertEqual(levels["codex"], "ok")
        self.assertEqual(levels["claude"], "warn", "installed but no sign-in folder")
        self.assertEqual(levels["secret statements"], "fail", "the referenced file is missing")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out.getvalue())

        secret = home / "statement-keys.txt"
        secret.write_text("x")
        secret.chmod(0o600)
        (home / ".claude").mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            code, results = setup.doctor(self.path, io.StringIO(), which, home)
        self.assertEqual(code, 0, results)

    def test_doctor_reports_a_missing_or_open_profile(self):
        code, results = setup.doctor(self.path, io.StringIO())
        self.assertEqual((code, results[0][:2]), (1, ("fail", "profile")))
        self.run_init(first_run_answers(self.vault))
        self.path.chmod(0o644)
        code, results = setup.doctor(self.path, io.StringIO())
        self.assertIn("readable by other users", results[0][2])


if __name__ == "__main__":
    unittest.main()
