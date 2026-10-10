# Ubuntu adapter validation

This operator-only M3 harness measures candidate adapters. It never activates a
gateway provider or produces `RuntimeEvidence` verification flags. No live trial
has run in this repository. CLI installation, login status and a passing fixture
suite do not prove authentication works, subscription entitlement, billing scope,
tool restrictions or Ubuntu confinement.

## 1. Select the provider and account

Record the owner's explicit provider/account choice before running live tasks.
Use an already supported local login; the harness never logs in/out or extracts
credentials. `codex` methods are `chatgpt`, `api_key` or `access_token`; `claude`
methods are `claude.ai`, `oauth_token`, `api_key`, `api_key_helper` or `third_party`.
Gateway configuration uses `subscription` for Codex `chatgpt` / Claude `claude.ai`,
or `api_key` for `api_key`. Other methods need a separately reviewed gateway path.

An optional non-inference check reads CLI version and status and prints only
the observed method/version, not raw output, emails, paths or credentials:

```sh
PYTHONPATH=packages/agent-adapters/src python3 -m chintu_adapters.readiness inspect \
  --provider codex --executable /absolute/path/to/codex
```

This cannot establish that the next inference is authorized or will use the
intended billing route. The validation command rejects authentication environment
overrides instead of silently switching accounts. Use reviewed supported provider
storage; overrides/helpers and third-party routes may remain unsupported until
their exact invocation/account semantics are independently verified. No model,
billing fallback, credential copying or account reconfiguration is attempted.

## 2. Prepare an empty disposable workspace

Run inside the actual Ubuntu execution environment, with the same runtime,
service user, provider storage and external containment planned for the test.
Create its parent directory beforehand; the workspace itself must not exist.

```sh
PYTHONPATH=packages/agent-adapters/src python3 -m chintu_adapters.readiness prepare \
  --workspace /absolute/disposable/chintu-fixture
```

This creates `fixture.txt` with a unique known token and `fixture.json` with
workspace/content hashes. It does not run a provider. The candidate Codex runner
allows this fixture without Git metadata using `--skip-git-repo-check`; its
sandbox/approval restrictions remain enabled. Do not use a personal repository
or include sensitive material in this first trial.

## 3. Review the actual execution boundary

Create one local `boundary.json` record binding the selected provider/account
method, pinned runtime, Ubuntu environment and prepared workspace hash. Describe
what containment is actually enforced, what has been measured and what remains
untested. Keep descriptions non-secret: do not include credentials, personal
paths, raw provider diagnostics or account identifiers.

The record is an explicit operator review for **this disposable inspect trial**;
it is not automated proof of confinement. Filesystem/tool restrictions, loaded
customizations, credential access and inference/network routes should be understood
for the chosen environment. Record known failed controls separately and resolve
unsafe boundaries before the trial. Unfinished trial controls, such as live
provider cancellation or continuation, belong in `known_gaps` and can be tested
by this harness; they do not require a separate successful trial first.

Example below intentionally lacks review, and **will be rejected** until the
operator reviews and records the actual boundary:

```json
{
  "schema_version": "1.0.0",
  "provider": "codex",
  "login_method": "chatgpt",
  "runtime_version": "0.162.0",
  "workspace_sha256": "<prepared workspace hash>",
  "target_os": "ubuntu",
  "scope": "fixture_inspect_only",
  "operator_reviewed": false,
  "boundary_status": "unreviewed",
  "enforced_containment": "Describe the actual enforced boundary here.",
  "measured_evidence": [],
  "known_gaps": ["Provider continuation and live cancellation are under test."],
  "known_failures": []
}
```

When explicitly reviewed for the intended live scope, set `operator_reviewed` to
`true` and `boundary_status` to `reviewed`. A status of `unsafe`/`failed`, any
`known_failures`, a missing containment description or a mismatched identity
prevents invocation. Those review fields authorize the bounded trial; they never
become production `RuntimeEvidence` flags.

The record is bounded to 64 KiB, containment to 2,000 characters, and each of the
three description lists to 20 entries of 1,000 characters. Evidence may be
summarized or reference separately preserved non-secret records. Review again
when the account, runtime, workspace or execution boundary changes. The harness
records its SHA-256/provenance, marks confinement as not independently verified,
and reports recorded pending controls separately from measured trial results.

## 4. Authorize one bounded live trial

Review the explicit account/billing route, boundary record and disposable
fixture, then invoke the following command **only when live execution is authorized**:

```sh
PYTHONPATH=packages/agent-adapters/src python3 -m chintu_adapters.readiness validate \
  --provider codex --login-method chatgpt --executable /absolute/path/to/codex \
  --workspace /absolute/disposable/chintu-fixture \
  --boundary /absolute/private/boundary.json \
  --output /absolute/private/new-trial-report.json --allow-live
```

Use `--provider claude --login-method claude.ai` for a reviewed Claude subscription
trial. There is no default provider, automatic invocation or provider fallback.
The command refuses non-Ubuntu execution, absent live authorization, missing
reviewed boundary, mismatched version/login, and ambiguous authentication
environment. A failure does not change sign-in or permissions.

The trial can make up to three inference requests: read the known token, continue
the same provider session without rereading it, then interrupt an active read-only
attempt. Each attempt is bounded to 90 seconds; cancellation escalates and confirms
process exit. An immediate provider completion makes cancellation `not_observed`,
not passed. After any fixture mutation, subsequent trials stop. These limits bound
time/process/output, not token charges or account entitlement.

## 5. Interpret and preserve evidence

The output file is created exclusively with mode `0600`, outside the workspace,
before any possible inference. A crash/refused prerequisite can leave an explicit
`trial_status: "pending"` record; that is not measured success and needs review.
It stores provider/method/version, workspace hash, boundary record hash/provenance, recorded pending controls, observed
event kinds and individual `read_only_task`, `session_continuation`, `cancellation`
and `fixture_unchanged` gates. It omits raw output, token contents, session IDs,
credentials and unredacted provider diagnostics. Keep operator descriptions non-secret. Exit codes: `0` means all
trial gates passed, `1` means a measured gate failed/not-observed, `2` means refusal
or an execution prerequisite error. Always read the individual gates.

Every report says `production_activation: "not_performed"`. It identifies the
inspect profile **under test**, not globally validated profiles. Develop access,
live approvals, active steering, recovery and production-workspace confinement
remain unfinished. A trial record is not accepted as automatic production truth.
Gateway enablement requires a separate operator review binding actual account,
project, runtime and execution boundary to the unchanged `RuntimeEvidence` fields
and a local non-secret evidence record. Neither harness nor adapter can infer that
review from presence, flags or a successful file read.

Authentication/status semantics were checked against official
[Codex authentication](https://learn.chatgpt.com/docs/auth),
[Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference), and
[Claude CLI reference](https://code.claude.com/docs/en/cli-reference).
