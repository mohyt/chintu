"""Explicit operator-only M3 trials; never enables gateway production execution."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

from .local import ClaudeAdapter, CodexAdapter, ProtocolError, RuntimeEvidence
from .types import AdapterCapabilities, TaskCommand

READ_PROMPT = "Read only fixture.txt in the current workspace. Reply with its token exactly. Do not modify files, access other files, or make external requests."
CONTINUE_PROMPT = "Return the exact token from your previous reply, without reading files or using tools."
CANCEL_PROMPT = "Read only fixture.txt, then wait sixty seconds before replying with its token. Do not modify files, access other files, or make external requests."
LOGIN_METHODS = {"codex": ("chatgpt", "api_key", "access_token"),
                 "claude": ("claude.ai", "oauth_token", "api_key", "api_key_helper", "third_party")}


class ReadinessError(Exception):
    """Messages contain fixed text, never provider output or credential values."""


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def workspace_identity(workspace: Path) -> str:
    return _hash(str(workspace.resolve()).encode())


def _load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ReadinessError("Evidence file missing, unsafe, or too large.")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ReadinessError("Evidence must be an object.")
    return value


def host_os() -> str:
    if sys.platform != "linux":
        return "other"
    try:
        lines = Path("/etc/os-release").read_text().splitlines()
        return "ubuntu" if 'ID=ubuntu' in lines or 'ID="ubuntu"' in lines else "other"
    except OSError:
        return "other"


def prepare(workspace: Path) -> dict:
    """Creates a fresh disposable fixture, never writes an existing workspace."""
    workspace.mkdir(parents=False, exist_ok=False, mode=0o700)
    token = "CHINTU-" + uuid.uuid4().hex
    (workspace / "fixture.txt").write_text(token + "\n")
    manifest = {"schema_version": "1.0.0", "workspace_sha256": workspace_identity(workspace),
                "fixture_sha256": _hash((token + "\n").encode())}
    (workspace / "fixture.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _snapshot(workspace: Path) -> dict:
    entries = list(workspace.iterdir())
    if len(entries) > 20 or any(p.is_symlink() or not p.is_file() or p.stat().st_size > 65536 for p in entries):
        raise ReadinessError("Fixture workspace changed or contains unsafe entries.")
    return {p.name: _hash(p.read_bytes()) for p in entries}


def reviewed_boundary(path: Path, provider: str, method: str, version: str, workspace: Path) -> dict:
    """Validate explicit reviewed records; their provenance remains operator-owned."""
    report = _load(path)
    expected = {"schema_version": "1.0.0", "provider": provider,
                "login_method": method, "runtime_version": version,
                "workspace_sha256": workspace_identity(workspace), "target_os": "ubuntu"}
    if any(report.get(k) != v for k, v in expected.items()):
        raise ReadinessError("Reviewed boundary does not match this provider, account method, runtime, and workspace.")
    if report.get("scope") != "fixture_inspect_only" or report.get("operator_reviewed") is not True:
        raise ReadinessError("Explicit operator review for this read-only fixture trial required.")
    if report.get("boundary_status") != "reviewed":
        raise ReadinessError("Boundary is unreviewed or known unsafe/failed.")
    containment = report.get("enforced_containment")
    if not isinstance(containment, str) or not 1 <= len(containment.strip()) <= 2000:
        raise ReadinessError("Describe the actual enforced containment for this trial.")
    for field in ("measured_evidence", "known_gaps", "known_failures"):
        values = report.get(field)
        if not isinstance(values, list) or len(values) > 20 or any(
            not isinstance(value, str) or not 1 <= len(value.strip()) <= 1000 for value in values
        ):
            raise ReadinessError("Bounded measurement, gap and failure descriptions required.")
    if report["known_failures"]:
        raise ReadinessError("Known failed boundary controls must be resolved before this trial.")
    return {
        "report_sha256": _hash(path.read_bytes()),
        "provenance": "operator_reviewed_boundary_record",
        "confinement_assessment": "not_independently_verified_by_harness",
        "measured_evidence_count": len(report["measured_evidence"]),
        "pending_controls": list(report["known_gaps"]),
    }


async def _probe(executable: str, args: tuple[str, ...]) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(executable, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    async def bounded_read(stream):
        chunks = []
        total = 0
        while chunk := await stream.read(8192):
            total += len(chunk)
            if total > 65536:
                raise ReadinessError("Status probe output exceeded its limit.")
            chunks.append(chunk)
        return b"".join(chunks)
    readers = [asyncio.create_task(bounded_read(process.stdout)), asyncio.create_task(bounded_read(process.stderr))]
    try:
        async with asyncio.timeout(5):
            out, err = await asyncio.gather(*readers)
            await process.wait()
            return process.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")
    finally:
        await CodexAdapter(executable)._stop(process)
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


async def inspect(provider: str, executable: str) -> dict:
    if provider not in LOGIN_METHODS:
        raise ReadinessError("Explicit supported provider required.")
    code, version_text, _ = await _probe(executable, ("--version",))
    match = re.search(r"\b\d+\.\d+\.\d+\b", version_text)
    if code or match is None:
        raise ReadinessError("Runtime version probe failed.")
    code, auth_text, auth_stderr = await _probe(executable, ("login", "status") if provider == "codex" else ("auth", "status"))
    method = "unknown"
    authenticated = False
    if provider == "codex":
        lowered = (auth_text + "\n" + auth_stderr).lower()
        # Recognize fixed status markers; never persist the returned status text.
        if "logged in using chatgpt" in lowered:
            method = "chatgpt"
        elif "logged in using api key" in lowered:
            method = "api_key"
        elif "logged in using access token" in lowered:
            method = "access_token"
        authenticated = code == 0 and method != "unknown"
    else:
        try:
            value = json.loads(auth_text)
            if value.get("authMethod") in LOGIN_METHODS[provider]:
                method = value["authMethod"]
            authenticated = code == 0 and value.get("loggedIn") is True and method != "unknown"
        except (ValueError, AttributeError):
            pass
    return {"provider": provider, "runtime_version": match.group(), "login_method": method,
            "authenticated_status_observed": authenticated,
            "provider_execution": "not_started", "capabilities": "unverified"}


def _check_auth_environment(provider: str, method: str) -> None:
    # Presence can reveal ambiguity; it never proves an account or authorizes fallback.
    variables = ("CODEX_API_KEY", "OPENAI_API_KEY", "CODEX_ACCESS_TOKEN") if provider == "codex" else (
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
    if any(name in os.environ for name in variables):
        raise ReadinessError("Authentication environment overrides require separate review; this harness will not select or fall back between accounts.")


class _TrialMixin:
    """Candidate control is confined to this harness; no production readiness claim."""
    def capabilities(self):
        return AdapterCapabilities(self.provider, True, ("inspect",), True, True, False, False, False,
                                   "Operator-authorized trial only; capabilities under test.")

    async def _validate_runtime(self):
        await super()._validate_runtime()
        if self.provider == "codex":
            help_text = await self._capture_probe("exec", "resume", "--help")
            if "SESSION_ID" not in help_text or "--json" not in help_text:
                raise ProtocolError("Candidate continuation flags unavailable.")


class _CodexTrial(_TrialMixin, CodexAdapter):
    def argv(self, command):
        args = super().argv(command)
        # Disposable fixture has no Git repository; this does not bypass sandboxing.
        args.insert(-1, "--skip-git-repo-check")
        return args


class _ClaudeTrial(_TrialMixin, ClaudeAdapter):
    pass


async def validate(*, provider: str, login_method: str, executable: str,
                   workspace: Path, boundary: Path, allow_live: bool = False) -> dict:
    if not allow_live or provider not in LOGIN_METHODS or login_method not in LOGIN_METHODS[provider]:
        raise ReadinessError("Live validation requires an explicit provider, account method, reviewed boundary and --allow-live.")
    if host_os() != "ubuntu":
        raise ReadinessError("Run live validation inside the reviewed Ubuntu execution environment.")
    _check_auth_environment(provider, login_method)
    manifest = _load(workspace / "fixture.json")
    original = _snapshot(workspace)
    token = (workspace / "fixture.txt").read_text().strip()
    if not re.fullmatch(r"CHINTU-[a-f0-9]{32}", token) or manifest.get("workspace_sha256") != workspace_identity(workspace):
        raise ReadinessError("Fresh prepared fixture required.")
    if original.get("fixture.txt") != manifest.get("fixture_sha256"):
        raise ReadinessError("Fixture digest mismatch.")
    # Boundary validation precedes even auth probes, using the reviewed pinned version.
    pinned = _load(boundary).get("runtime_version")
    external = reviewed_boundary(boundary, provider, login_method, pinned, workspace)
    observed = await inspect(provider, executable)
    if observed["runtime_version"] != pinned or not observed["authenticated_status_observed"] or observed["login_method"] != login_method:
        raise ReadinessError("Observed runtime/account method differs from the reviewed choice.")
    evidence = RuntimeEvidence(pinned, ("inspect",), record="operator-authorized compatibility trial")
    # No verification boolean is manufactured to bootstrap the first trial.
    candidate = (_CodexTrial if provider == "codex" else _ClaudeTrial)(
        executable, evidence=evidence, timeout_seconds=90, stop_seconds=2)
    gates = {}

    async def exercise(prompt, session=None, cancel=False):
        command = TaskCommand(str(uuid.uuid4()), str(uuid.uuid4()), str(workspace), prompt,
                              provider_session_id=session)
        kinds, reply, returned_session = [], "", None
        async for event in candidate.run(command):
            kinds.append(event.kind)
            if event.provider_session_id:
                returned_session = event.provider_session_id
            if event.kind == "output":
                reply = (reply + event.payload.get("text", ""))[-8000:]
            if event.kind == "succeeded" and not reply:
                reply += event.payload.get("summary", "")
            if cancel and event.kind == "started":
                await candidate.cancel(command.attempt_id)
        return kinds, reply, returned_session

    kinds, reply, session = await exercise(READ_PROMPT)
    def unchanged_fixture():
        try:
            return _snapshot(workspace) == original
        except ReadinessError:
            return False

    read_passed = bool(kinds and kinds[-1] == "succeeded" and session and reply.strip() == token and unchanged_fixture())
    gates["read_only_task"] = {"status": "passed" if read_passed else "failed", "event_kinds": kinds,
                               "known_fixture_token_observed": reply.strip() == token}
    if read_passed:
        kinds, reply, resumed = await exercise(CONTINUE_PROMPT, session)
        gates["session_continuation"] = {"status": "passed" if kinds and kinds[-1] == "succeeded" and resumed == session and reply.strip() == token and unchanged_fixture() else "failed",
                                         "event_kinds": kinds, "same_session_observed": resumed == session}
        if unchanged_fixture():
            kinds, _, _ = await exercise(CANCEL_PROMPT, cancel=True)
            gates["cancellation"] = {"status": "passed" if kinds and kinds[-1] == "cancelled" else "not_observed",
                                     "event_kinds": kinds}
        else:
            gates["cancellation"] = {"status": "not_run"}
    else:
        gates["session_continuation"] = {"status": "not_run"}
        gates["cancellation"] = {"status": "not_run"}
    gates["fixture_unchanged"] = {"status": "passed" if unchanged_fixture() else "failed"}
    return {"schema_version": "1.0.0", "provider": provider, "login_method": login_method,
            "runtime_version": pinned, "observed_at": datetime.now(timezone.utc).isoformat(),
            "target_os": "ubuntu", "workspace_sha256": workspace_identity(workspace),
            "external_boundary_review": external, "measured_gates": gates,
            "production_activation": "not_performed", "execution_profile_under_test": "inspect",
            "unfinished": ["develop_profile", "live_approvals", "active_steering", "session_recovery",
                           "production_workspace_confinement", "gateway_enablement_review"]}


def write_report(path: Path, report: dict) -> None:
    # Exclusive/private creation avoids replacing an earlier operator record.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare", help="Create a fresh disposable fixture; no provider runs.")
    prepare_parser.add_argument("--workspace", type=Path, required=True)
    inspect_parser = sub.add_parser("inspect", help="Read version/login status only; never starts a model task.")
    inspect_parser.add_argument("--provider", choices=LOGIN_METHODS, required=True)
    inspect_parser.add_argument("--executable", required=True)
    live = sub.add_parser("validate", help="Explicit operator-only live task, continuation, cancellation trial.")
    live.add_argument("--provider", choices=LOGIN_METHODS, required=True)
    live.add_argument("--login-method", required=True)
    live.add_argument("--executable", required=True)
    live.add_argument("--workspace", type=Path, required=True)
    live.add_argument("--boundary", type=Path, required=True)
    live.add_argument("--output", type=Path, required=True)
    live.add_argument("--allow-live", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            print(json.dumps(prepare(args.workspace)))
        elif args.command == "inspect":
            print(json.dumps(asyncio.run(inspect(args.provider, args.executable))))
        else:
            if not args.allow_live:
                raise ReadinessError("Explicit live authorization required.")
            if args.output.exists() or args.output.is_symlink() or args.workspace.resolve() in args.output.resolve().parents:
                raise ReadinessError("Choose a new evidence output file outside the fixture workspace.")
            # Reserve usable/private storage before any possible inference. A
            # crash or refused prerequisite leaves an explicit pending record.
            descriptor = os.open(args.output, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w+") as stream:
                json.dump({"schema_version": "1.0.0", "provider": args.provider,
                           "trial_status": "pending", "production_activation": "not_performed"}, stream)
                stream.flush()
                os.fsync(stream.fileno())
                report = asyncio.run(validate(provider=args.provider, login_method=args.login_method,
                    executable=args.executable, workspace=args.workspace, boundary=args.boundary, allow_live=args.allow_live))
                stream.seek(0)
                stream.truncate()
                json.dump(report, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            print(json.dumps({"production_activation": "not_performed", "measured_gates": report["measured_gates"]}))
            return 0 if all(g["status"] == "passed" for g in report["measured_gates"].values()) else 1
        return 0
    except (ReadinessError, OSError, ValueError, KeyError, TypeError, asyncio.TimeoutError):
        print("Readiness check refused or failed; review explicit account, boundary and fixture prerequisites. No production activation occurred.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
