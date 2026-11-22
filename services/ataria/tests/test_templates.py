import json
import os
import tempfile
import unittest
from pathlib import Path

from chintu_ataria import leaks, profile, templates

ROOT = Path(__file__).resolve().parents[1]
SAM = json.loads((ROOT / "examples/profile.sam.json").read_text())


class TemplateTests(unittest.TestCase):
    def render(self, text, value=SAM, runtime=None, base=None):
        return templates.render_text(text, value, runtime, base)

    def test_values_filters_and_runtime(self):
        self.assertEqual(self.render("{{ owner.name }} at {{work.company}}"), "Sam at Example Systems")
        self.assertEqual(self.render("{{ work.clients | join }}"), "Northwind Bank, Contoso Telecom")
        self.assertEqual(self.render("{{ family | names }}"), "Alex, Robin")
        self.assertEqual(self.render("{{ work.team | bullets }}"), "- Priya\n- Tom")
        self.assertEqual(self.render("{{ schedule | json }}"), '{"morning": "07:00", "midday": "13:30", "catch_up": true}')
        self.assertEqual(self.render("{{ATARIA_ROOT}}/data and {{UNSET}}", runtime={"ATARIA_ROOT": "/srv/a"}),
                         "/srv/a/data and {{UNSET}}")

    def test_missing_values_stop_the_render(self):
        with self.assertRaises(templates.TemplateError) as caught:
            self.render("{{ owner.name }} {{ owner.nickname }} {{ work.budget | join }}")
        self.assertEqual(caught.exception.missing, ["owner.nickname", "work.budget"])
        self.assertIn("owner.nickname", str(caught.exception))
        self.assertEqual(self.render("[{{ owner.nickname? }}]"), "[]")

    def test_sections(self):
        text = "{{#if family}}Family: {{ family | names }}{{else}}No family listed{{/if}}"
        self.assertEqual(self.render(text), "Family: Alex, Robin")
        self.assertEqual(self.render(text, {**SAM, "family": []}), "No family listed")
        # A missing value inside a section that is left out is not an error.
        self.assertEqual(self.render("{{#if pets}}{{ pets.dog }}{{/if}}ok"), "ok")
        nested = "{{#if work}}A{{#if work.budget}}B{{/if}}C{{/if}}"
        self.assertEqual(self.render(nested), "AC")
        with self.assertRaises(templates.TemplateError):
            self.render("{{#if work}}open")

    def test_lists_need_a_filter_and_filters_must_exist(self):
        with self.assertRaises(templates.TemplateError):
            self.render("{{ work.clients }}")
        with self.assertRaises(templates.TemplateError):
            self.render("{{ work.clients | shout }}")

    def test_includes_stay_inside_the_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "skills").mkdir()
            (base / "skills/a.md").write_text("A for {{ owner.name }} {{include b.md}}")
            (base / "skills/b.md").write_text("B")
            (base / "b.md").write_text("root B")
            self.assertEqual(self.render("{{include skills/a.md}}", base=base), "A for Sam root B")
            with self.assertRaises(templates.TemplateError):
                self.render("{{include ../etc/passwd}}", base=base / "skills")
            (base / "skills/c.md").write_text("{{ owner.missing }}")
            with self.assertRaises(templates.TemplateError) as caught:
                self.render("{{include skills/c.md}}", base=base)
            self.assertEqual(caught.exception.missing, ["owner.missing"])

    def test_every_bundled_template_renders_for_the_example_profile(self):
        defaults = ROOT / "defaults"
        files = sorted(defaults.rglob("*.md"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(template=str(path.relative_to(defaults))):
                text = templates.render_file(path, SAM, base=defaults)
                self.assertNotIn("{{ ", text)
                self.assertNotIn("{{#if", text)

    def test_fields_used(self):
        self.assertEqual(templates.fields_used("{{ owner.name }} {{#if family}}{{ work.clients | join }}{{/if}}"),
                         ["family", "owner.name", "work.clients"])


class ProfileTests(unittest.TestCase):
    def test_example_is_valid_and_unknown_keys_are_kept(self):
        profile.validate(SAM)
        profile.validate({**SAM, "future_feature": {"x": 1}})

    def test_problems_are_named(self):
        bad = {**SAM, "owner": {"role": "x"}, "schedule": {"morning": "7am"},
               "secrets": {"bank": {"store": "keychain", "ref": "a", "password": "nope"}}}
        with self.assertRaises(profile.ProfileError) as caught:
            profile.validate(bad)
        message = str(caught.exception)
        for fragment in ("owner: 'name' is a required property", "schedule.morning", "secrets.bank"):
            self.assertIn(fragment, message)

    def test_load_save_and_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chintu" / "ataria.json"
            with self.assertRaises(profile.ProfileError):
                profile.load(path)
            profile.save(SAM, path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(profile.load(path), SAM)
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(profile.ProfileError, "readable by other users"):
                profile.load(path)
            path.write_text("{not json")
            os.chmod(path, 0o600)
            with self.assertRaisesRegex(profile.ProfileError, "not valid JSON"):
                profile.load(path)

    def test_default_path(self):
        self.assertEqual(profile.default_path({"ATARIA_PROFILE": "/x/p.json"}), Path("/x/p.json"))
        self.assertEqual(profile.default_path({}, "darwin", "/Users/u"),
                         Path("/Users/u/Library/Application Support/Chintu/ataria.json"))
        self.assertEqual(profile.default_path({"XDG_DATA_HOME": "/d"}, "linux", "/h"), Path("/d/chintu/ataria.json"))
        self.assertEqual(profile.default_path({}, "linux", "/h"), Path("/h/.local/share/chintu/ataria.json"))


class LeakTests(unittest.TestCase):
    def kinds(self, text, deny=()):
        return [leak.kind for leak in leaks.scan_text(text, deny=deny)]

    def test_builtin_patterns(self):
        at = "@"
        self.assertEqual(self.kinds("mail me: someone" + at + "realmail.io"), ["email address"])
        self.assertEqual(self.kinds("sam" + at + "example.com is fine"), [])
        self.assertEqual(self.kinds("call +65 9123 4567 or 9876543210"), ["phone number", "phone number"])
        self.assertEqual(self.kinds("dates 2026-11-22 and 2026-11-22T07:00 and ids 12345 are fine"), [])
        self.assertEqual(self.kinds("see " + "/ho" + "me/someone/notes"), ["home folder path"])
        self.assertEqual(self.kinds("https://" + "box.tail1234" + ".ts.net/x"), ["tailnet host name"])
        self.assertEqual(self.kinds("https://machine.tailnet.ts.net"), [])

    def test_deny_list_matches_whole_words_without_echoing_them(self):
        found = leaks.scan_text("Ask Quentin about it.\nQuentinsson is someone else.", deny=["quentin"])
        self.assertEqual([(leak.line, leak.text) for leak in found], [(1, "entry #1")])

    def test_the_package_has_no_personal_values(self):
        deny = leaks.deny_list(os.environ.get("ATARIA_DENY_FILE"))
        found = leaks.scan_tree(ROOT, deny, skip=("tests/",))
        self.assertEqual(found, [], "\n".join(f"{l.path}:{l.line}: {l.kind}: {l.text}" for l in found))


if __name__ == "__main__":
    unittest.main()
