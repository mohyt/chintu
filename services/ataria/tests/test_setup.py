import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chintu_ataria import profile, setup, templates


def first_run_answers(vault, work=True, finance=True):
    answers = [
        # A. About you: name, full name, city, timezone, locale, vault, create it?
        "Sam", "Sam Example", "Singapore", "Asia/Singapore", "en-GB", str(vault), "y",
        # B. Personal: email; add Robin, stop; interests
        "sam@example.com", "y", "Robin", "child", "Example Primary School", "-", "n", "AI agents, running",
        # personal sources: gmail, calendar, feeds (+ packs), voice notes
        "y", "y", "y", "world, tech, ai", "y",
    ]
    answers += ["y", "SGD", "Example Bank", "~/Documents/Statements"] if finance else ["n"]
    # personal notes: family routines, personal judging, style
    answers += ["School fee mails are todos.", "-", "Short sentences."]
    if work:
        answers += [
            # C. Work: follow? company, role, work email, clients, products, team
            "y", "Example Systems", "Head of Delivery", "sam@work.example.com", "Northwind Bank, Contoso Telecom",
            "Ledgerline", "Priya, Tom",
            # work sources: microsoft365, teams, jira, github, meetings, whatsapp groups; then GitHub orgs
            "y", "y", "n", "y", "y", "n", "example-systems",
            # work notes: judging, alert feeds
            "Escalations first.", "Build Results bot channel",
            # D. jobs (work area): work, meetings, interviews: run? agent? model?
            "", "", "",  "", "", "",  "y", "", "",
        ]
    else:
        answers += ["n"]
    # D. jobs (personal area): google (codex: run, agent, model, web?), personal, notes (claude: run, agent, model)
    answers += ["", "", "", "",  "", "", "", "",  "", "", ""]
    if finance:
        answers += ["", "", "", "",  "", "", "", ""]          # finance, ledger (codex)
    answers += ["", "", "-", ""]                              # review: codex, no model, no web
    answers += [
        # schedule: a bad time is asked again
        "7am", "07:00", "", "n",
        # delivery: phone; add Alex on WhatsApp for family, evening only (an unknown digest is dropped)
        "y", "y", "Alex", "whatsapp", "-", "family", "evening, weekly", "n",
        # secrets: one file reference, stop
        "y", "statements", "file", "~/statement-keys.txt", "n",
        # privacy
        "-",
        # save
        "y",
    ]
    return answers


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.vault = self.base / "vault"
        self.path = self.base / "chintu" / "ataria.json"
        self.out = io.StringIO()
        env = mock.patch.dict(os.environ, {"HOME": str(self.base / "home")})
        env.start()
        self.addCleanup(env.stop)

    def run_init(self, answers):
        return setup.init(self.path, setup.Prompter(stdout=self.out, answers=answers))

    def test_first_run_separates_personal_work_and_jobs(self):
        value = self.run_init(first_run_answers(self.vault))
        self.assertTrue(self.vault.is_dir())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        saved = profile.load(self.path)
        self.assertEqual(saved, value)
        self.assertEqual(saved["owner"], {"name": "Sam", "full_name": "Sam Example", "city": "Singapore",
                                          "timezone": "Asia/Singapore", "locale": "en-GB"})
        personal, work, jobs = saved["personal"], saved["work"], saved["jobs"]
        self.assertEqual(personal["email"], "sam@example.com")
        self.assertEqual(personal["family"], [{"name": "Robin", "relation": "child", "school": "Example Primary School"}])
        self.assertEqual(personal["interests"], ["AI agents", "running"])
        self.assertEqual(personal["sources"]["feeds"], {"enabled": True, "packs": ["world", "tech", "ai"]})
        self.assertEqual(personal["finance"], {"enabled": True, "currency": "SGD", "banks": ["Example Bank"],
                                               "statements_folder": "~/Documents/Statements"})
        self.assertEqual(personal["context"], {"family": "School fee mails are todos.", "style": "Short sentences."})
        self.assertEqual(work["email"], "sam@work.example.com")
        self.assertEqual(work["clients"], ["Northwind Bank", "Contoso Telecom"])
        self.assertFalse(work["sources"]["jira"]["enabled"])
        self.assertEqual(work["github_orgs"], ["example-systems"])
        self.assertEqual(work["context"]["alert_feeds"], "Build Results bot channel")
        # Each job keeps its own agent and model; the suggestions follow the opinionated defaults.
        self.assertEqual(jobs["work"], {"enabled": True, "agent": "claude", "model": "claude-opus-5-5"})
        self.assertEqual(jobs["interviews"]["enabled"], True)
        self.assertEqual(jobs["personal"], {"enabled": True, "agent": "codex", "model": "gpt-6.1-sol", "web_search": True})
        self.assertEqual(jobs["google"]["web_search"], False)
        self.assertEqual(jobs["review"], {"enabled": True, "agent": "codex", "model": None, "web_search": False})
        self.assertEqual(saved["schedule"], {"morning": "07:00", "midday": "13:30", "catch_up": False})
        self.assertEqual(saved["delivery"]["recipients"],
                         [{"name": "Alex", "channel": "whatsapp", "areas": ["family"], "digests": ["evening"]}])
        text = self.out.getvalue()
        for heading in ("A. About you", "B. Personal life", "C. Work", "D. Jobs and how Ataria runs", "Job: personal"):
            self.assertIn(heading, text)
        self.assertIn("Use 24-hour HH:MM", text)
        self.assertIn("personal   codex · gpt-6.1-sol · web", text)

    def test_without_work_or_finance_their_jobs_are_off_and_not_asked(self):
        value = self.run_init(first_run_answers(self.vault, work=False, finance=False))
        self.assertEqual(value["work"], {"enabled": False})
        for job in ("work", "meetings", "interviews", "finance", "ledger"):
            self.assertFalse(value["jobs"][job]["enabled"], job)
        self.assertNotIn("Job: work", self.out.getvalue())
        self.assertTrue(value["jobs"]["personal"]["enabled"])
        # The shared prompt still renders without any work details.
        templates.render_file(templates.defaults_dir() / "agents/common.md", value, base=templates.defaults_dir())

    def test_rerun_edits_and_keeps_answers_and_unknown_keys(self):
        self.run_init(first_run_answers(self.vault))
        data = json.loads(self.path.read_text())
        data["future_feature"] = {"x": 1}
        profile.save(data, self.path)
        before = profile.load(self.path)
        value = self.run_init([""] * 300)
        self.assertEqual(value, before)
        self.assertEqual(len(list(self.path.parent.glob("ataria.json.*.bak"))), 1)
        answers = [""] * 300
        answers[0] = "Samantha"
        self.assertEqual(self.run_init(answers)["owner"]["name"], "Samantha")

    def test_an_old_flat_profile_is_moved_to_the_new_layout(self):
        old = {"schema": 1, "owner": {"name": "Sam", "timezone": "UTC"}, "vault": str(self.vault),
               "agents": {"default": "codex"}, "sources": {"feeds": {"enabled": True}}, "context": {"style": "x"}}
        self.vault.mkdir()
        profile.save(old, self.path)
        value = self.run_init([""] * 300)
        for key in ("agents", "sources", "context"):
            self.assertNotIn(key, value)
        self.assertIn("jobs", value)

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
        code, results = setup.doctor(self.path, out, which, home)
        levels = {check: (level, detail) for level, check, detail in results}
        self.assertEqual(levels["profile"][0], "ok")
        self.assertEqual(levels["templates"][0], "ok")
        self.assertEqual(levels["vault"][0], "ok")
        self.assertEqual(levels["codex"][0], "ok")
        self.assertIn("used by: finance, google, ledger, personal, review", levels["codex"][1])
        self.assertEqual(levels["claude"][0], "warn", "installed but no sign-in folder")
        self.assertEqual(levels["statements"][0], "warn", "statements folder missing")
        self.assertEqual(levels["secret statements"][0], "fail", "the referenced file is missing")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out.getvalue())

        secret = home / "statement-keys.txt"
        secret.write_text("x")
        secret.chmod(0o600)
        (home / ".claude").mkdir()
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
