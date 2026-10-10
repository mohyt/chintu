from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chintu_adapters.readiness import (
    ReadinessError, inspect, main, prepare,
    validate, workspace_identity, write_report,
)


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "fixture"
        prepare(self.workspace)
        self.runtime = self.root / "runtime"
        self.runtime.write_text(Path(__file__).with_name("fixture_runtime.py").read_text())
        self.runtime.chmod(0o700)
        self.boundary = self.root / "boundary.json"
        self.os_patch = patch("chintu_adapters.readiness.host_os", return_value="ubuntu")
        self.os_patch.start()
        self.addCleanup(self.os_patch.stop)
        self.env_patch = patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def record_boundary(self, provider="codex", method="chatgpt"):
        identity = {"schema_version": "1.0.0", "provider": provider, "login_method": method,
                    "runtime_version": "1.2.3", "workspace_sha256": workspace_identity(self.workspace)}
        self.boundary.write_text(json.dumps(dict(identity, target_os="ubuntu",
            scope="fixture_inspect_only", operator_reviewed=True, boundary_status="reviewed",
            enforced_containment="Fake subprocess fixture under test; not real confinement evidence.",
            measured_evidence=["Existing fake subprocess tests passed."],
            known_gaps=["Live cancellation is under test, not established beforehand."], known_failures=[])))


    async def run_validation(self, provider="codex", method="chatgpt", **kwargs):
        return await validate(provider=provider, login_method=method, executable=str(self.runtime),
                              workspace=self.workspace, boundary=self.boundary, allow_live=True, **kwargs)

    def test_prepare_refuses_existing_workspace(self):
        original = (self.workspace / "fixture.txt").read_bytes()
        with self.assertRaises(FileExistsError):
            prepare(self.workspace)
        self.assertEqual((self.workspace / "fixture.txt").read_bytes(), original)

    async def test_no_live_flag_refuses_before_provider_probe(self):
        with patch("chintu_adapters.readiness.inspect", side_effect=AssertionError("must not probe")):
            with self.assertRaises(ReadinessError):
                await validate(provider="codex", login_method="chatgpt", executable=str(self.runtime),
                               workspace=self.workspace, boundary=self.boundary)

    async def test_missing_boundary_refuses_before_provider_probe(self):
        with patch("chintu_adapters.readiness.inspect", side_effect=AssertionError("must not probe")):
            with self.assertRaises(ReadinessError):
                await self.run_validation()

    async def test_review_boolean_does_not_substitute_for_boundary_description(self):
        self.record_boundary()
        report = json.loads(self.boundary.read_text())
        report["enforced_containment"] = True
        self.boundary.write_text(json.dumps(report))
        with patch("chintu_adapters.readiness.inspect", side_effect=AssertionError("must not probe")):
            with self.assertRaises(ReadinessError):
                await self.run_validation()

    async def test_auth_probes_never_export_raw_status(self):
        for provider, method in (("codex", "chatgpt"), ("claude", "claude.ai")):
            observed = await inspect(provider, str(self.runtime))
            self.assertEqual(observed["login_method"], method)
            self.assertTrue(observed["authenticated_status_observed"])
            serialized = json.dumps(observed)
            self.assertNotIn("private@example", serialized)
            self.assertNotIn("SECRET_AUTH_VALUE", serialized)
            self.assertEqual(observed["capabilities"], "unverified")

    async def test_complete_candidate_trials_both_providers_no_production_activation(self):
        for provider, method in (("codex", "chatgpt"), ("claude", "claude.ai")):
            self.record_boundary(provider, method)
            report = await self.run_validation(provider, method)
            self.assertEqual(report["production_activation"], "not_performed")
            self.assertTrue(all(gate["status"] == "passed" for gate in report["measured_gates"].values()))
            serialized = json.dumps(report)
            self.assertNotIn(str(self.workspace), serialized)
            self.assertNotIn("SECRET_AUTH_VALUE", serialized)
            self.assertNotIn((self.workspace / "fixture.txt").read_text().strip(), serialized)
            self.assertNotIn("auth_verified", serialized)
            self.assertNotIn("confinement_verified", serialized)
            external = report["external_boundary_review"]
            self.assertEqual(external["provenance"], "operator_reviewed_boundary_record")
            self.assertEqual(external["confinement_assessment"], "not_independently_verified_by_harness")
            self.assertEqual(external["pending_controls"], ["Live cancellation is under test, not established beforehand."])
            self.assertEqual(external["measured_evidence_count"], 1)

    async def test_selected_method_mismatch_no_model_task(self):
        self.record_boundary(method="api_key")
        with patch("chintu_adapters.readiness._CodexTrial.run", side_effect=AssertionError("must not infer")):
            with self.assertRaises(ReadinessError):
                await self.run_validation(method="api_key")

    async def test_auth_environment_override_refused_without_values(self):
        self.record_boundary()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "SECRET_ENV_VALUE"}):
            with self.assertRaises(ReadinessError) as error:
                await self.run_validation()
            self.assertNotIn("SECRET_ENV_VALUE", str(error.exception))

    async def test_non_ubuntu_refuses_before_provider_probe(self):
        self.record_boundary()
        with patch("chintu_adapters.readiness.host_os", return_value="other"):
            with self.assertRaises(ReadinessError):
                await self.run_validation()

    async def test_mismatched_boundary_identity_refuses_before_probe(self):
        self.record_boundary()
        report = json.loads(self.boundary.read_text())
        report["workspace_sha256"] = "different_workspace"
        self.boundary.write_text(json.dumps(report))
        with patch("chintu_adapters.readiness.inspect", side_effect=AssertionError("must not probe")):
            with self.assertRaises(ReadinessError):
                await self.run_validation()

    async def test_known_unsafe_or_failed_boundary_refuses_before_probe(self):
        for status, failures in (("unsafe", []), ("failed", []), ("reviewed", ["Outside write was permitted."])):
            self.record_boundary()
            report = json.loads(self.boundary.read_text())
            report["boundary_status"] = status
            report["known_failures"] = failures
            self.boundary.write_text(json.dumps(report))
            with patch("chintu_adapters.readiness.inspect", side_effect=AssertionError("must not probe")):
                with self.assertRaises(ReadinessError):
                    await self.run_validation()

    async def test_unreviewed_or_wrong_scope_record_refuses_before_probe(self):
        for scope, reviewed in (("develop", True), ("fixture_inspect_only", False)):
            self.record_boundary()
            report = json.loads(self.boundary.read_text())
            report["scope"] = scope
            report["operator_reviewed"] = reviewed
            self.boundary.write_text(json.dumps(report))
            with patch("chintu_adapters.readiness.inspect", side_effect=AssertionError("must not probe")):
                with self.assertRaises(ReadinessError):
                    await self.run_validation()

    async def test_read_failure_does_not_run_followups(self):
        self.record_boundary()
        self.runtime.write_text(self.runtime.read_text().replace('reply = Path("fixture.txt").read_text().strip()', 'reply = "WRONG_TOKEN"'))
        report = await self.run_validation()
        self.assertEqual(report["measured_gates"]["read_only_task"]["status"], "failed")
        self.assertEqual(report["measured_gates"]["session_continuation"]["status"], "not_run")
        self.assertEqual(report["measured_gates"]["cancellation"]["status"], "not_run")

    async def test_workspace_mutation_stops_next_trial(self):
        self.record_boundary()
        self.runtime.write_text(self.runtime.read_text().replace('reply = Path("fixture.txt").read_text().strip()', 'Path("unexpected.txt").write_text("changed")\nreply = Path("fixture.txt").read_text().strip()'))
        report = await self.run_validation()
        self.assertEqual(report["measured_gates"]["fixture_unchanged"]["status"], "failed")
        self.assertEqual(report["measured_gates"]["session_continuation"]["status"], "not_run")

    def test_report_exclusive_private_write(self):
        output = self.root / "observed.json"
        write_report(output, {"provider_execution": "not_started"})
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            write_report(output, {})

    def test_cli_refusal_prints_no_raw_diagnostics(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(["validate", "--provider", "codex", "--login-method", "chatgpt",
                           "--executable", str(self.runtime), "--workspace", str(self.workspace),
                           "--boundary", str(self.boundary), "--output", str(self.root / "output.json")])
        self.assertEqual(result, 2)
        self.assertFalse((self.root / "output.json").exists())
        self.assertNotIn(str(self.runtime), stderr.getvalue())

    def test_cli_cannot_write_evidence_inside_fixture(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        output = self.workspace / "nested" / "output.json"
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(["validate", "--provider", "codex", "--login-method", "chatgpt",
                           "--executable", str(self.runtime), "--workspace", str(self.workspace),
                           "--boundary", str(self.boundary), "--output", str(output), "--allow-live"])
        self.assertEqual(result, 2)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
