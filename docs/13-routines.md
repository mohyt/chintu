# Routines

Design v0.1 · 22 November 2026 · Decision D25 · Status: proposed plan, not implemented

Routines are scheduled agent jobs that read the owner's sources (mail, calendar, feeds, code hosts, notes), write what they find into the OKF vault, and merge it into one daily board: what needs attention today, open todos, and an outside-world digest. The phone shows the board and the digest and can tick items done.

The behaviour comes from the owner's private prototype, Ataria, which runs on one Linux machine. That prototype is tied to its owner: names and sources are written into prompts, it is scheduled through systemd, and each job runs as a separate Linux account. Routines bring the same ideas into Chintu as an optional feature that anyone can install on macOS or Linux.

## Principles

1. **Public code, opinionated defaults.** The repository ships the scheduler, the job runner, the merge, a default flow, and default prompts, skills and profiles. Running the defaults should produce a useful board on the first morning.
2. **No credentials or personal information in the repository.** Everything about a person is read at run time from a JSON profile that the install step creates on their machine (below). Prompts and skills are templates; values such as the owner's name, timezone, clients or recipients are filled in from the profile and never committed.
3. **Credentials stay where they already live.** Agents use their own logins (Claude Code, Codex and their connectors). Any other secret goes into the operating system's credential store (Keychain on macOS, Secret Service on Linux), and the profile holds only a reference to it. Routines never write a secret to the vault, the profile, a log or the database.
4. **Not tied to one operating system.** The gateway, a single long-running process that already exists, runs the schedule. No systemd timers, launchd jobs, root setup or extra user accounts are required. Starting the gateway at login uses the desktop app's existing launchd and systemd user units.
5. **Off by default.** A Chintu install without a routines profile behaves exactly as it does today.

## The profile

The install step (`chintu routines init` in the desktop app, or the tray's setup sheet) asks a few questions, checks the agent logins and writes `routines.json` with mode `0600`. The file sits beside the gateway's `gateway.json` in Chintu's data directory: `~/Library/Application Support/Chintu` on macOS, `$XDG_DATA_HOME/chintu` on Linux. `chintu routines doctor` checks it later.

```json
{
  "schema": 1,
  "owner": {"name": "Sam", "timezone": "Asia/Singapore", "locale": "en-GB"},
  "vault": "~/vaults/main",
  "schedule": {"morning": "07:00", "midday": "13:30", "catch_up": true},
  "agents": {"default": "codex", "work": "claude"},
  "sources": {
    "feeds": {"enabled": true, "packs": ["world", "tech", "ai"], "extra": []},
    "google": {"enabled": true, "via": "codex-connector"},
    "github": {"enabled": false, "orgs": []},
    "notes": {"enabled": true}
  },
  "work": {"clients": [], "products": []},
  "delivery": {"phone": true, "channels": []},
  "privacy": {"deny_patterns_file": null}
}
```

- Every key is optional except `schema`, `owner.name` and `vault`. Readers ignore unknown keys and keep them when they rewrite the file.
- Lists such as clients, products and recipients start empty. The owner adds them in the setup sheet or by editing the file.
- `privacy.deny_patterns_file` points at a local list of strings (family names, account numbers) that must never appear in anything a routine sends off the machine, such as a delivery channel or an issue. The list itself stays local.

## How a run works

```
gateway scheduler ──(flow step due)──▶ job runner ──▶ agent adapter (Codex / Claude, routine profile)
                                            │                      │
                                            │                      └─ writes OKF items into its vault folder
                                            └─ after the jobs: merge ─▶ board + todos + digest ─▶ phone (SSE)
```

- **Flow.** A bundled `flow.yaml` lists the steps and their times: source jobs, then merge, then delivery. The profile can switch steps off or move the times; it cannot add arbitrary commands.
- **Scheduler.** It runs inside the gateway and keeps its state in the gateway's SQLite database: last run, next run, lock and result. If the machine was asleep at a scheduled time and `catch_up` is on, a missed step runs once on wake, within a bounded window. Runs appear as ordinary tasks in the activity view.
- **Jobs.** Each source job is an agent task. Its prompt is rendered from a template and the profile, and it runs through the existing adapters under a new `routine` execution profile. That profile can write only to the job's own vault folder and can use only the tools and connectors the job declares. The job writes OKF concept files of type Item, Todo or Extract, as the prototype does.
- **Merge.** Deterministic Python, with no model call. It reads the jobs' items, removes duplicates, ranks them P0 to P3, applies the Done ticks, and writes the Board and Todo List concepts into the vault.
- **Delivery.** The phone app gets a Today view built from the board, and the morning and midday summaries arrive as events. More channels can be added later as plugins.

The vault layout follows `docs/12-okf-notes.md`. Routine output goes under each section's own `Routines/` folder, so it sits next to the owner's own notes, and the shared OKF profile governs types and keys.

## Isolation

The prototype isolates jobs with one Linux account per job and file ACLs. Routines use guardrails that work on both operating systems instead:

- the `routine` profile's confinement: the PreToolUse hook for Claude and the sandbox and writable roots for Codex, limited to the job's folder;
- one task per job, so a failing job cannot block the others;
- the owner's private sections (finance, kids, personal) are separate jobs with separate folders, and the work job never gets them as readable roots.

This is weaker than separate accounts. A Linux owner who wants the stronger model can still run the gateway's job runner under extra accounts, but that is an optional hardening guide, not a requirement.

## Sources, in order

| Stage | Sources | Notes |
| --- | --- | --- |
| 1 | Feeds (curated packs: world, regional, tech, AI, cloud, security, regulators), notes from Chintu | No login needed. Feed packs ship in the repo; the owner picks packs in the profile. |
| 1 | Google mail and calendar | Through the agent's own Google connector; Chintu stores no Google token. |
| 1 | Work through Claude | Clients and products come from the profile. |
| 2 | GitHub snapshot, meetings and transcripts | Optional; each has a setup check in `doctor`. |
| 3 | Messaging bridges, finance statements and a ledger | Plugins with their own guides. They are bank- and country-specific, so they are not on by default. |

## Migration from the prototype

1. The prototype keeps running unchanged on its machine until Routines are as good.
2. Routines land in stages (below), and each stage passes CI and runs alongside the prototype.
3. The owner moves one job at a time. That machine's private data and profile stay local, and only generic improvements come back into this repository.
4. The prototype repository is archived once every job has moved.

## Stages

| Stage | Scope | Done when |
| --- | --- | --- |
| R1 | Profile schema, `init`/`doctor`, prompt templating, the `routine` profile, the scheduler with catch-up | A fresh macOS and a fresh Linux install produce a feeds-only board with no manual setup beyond `init` |
| R2 | Google, work and notes jobs; merge; Board and Todo List concepts; done ticks | The owner's morning board from Routines matches the prototype's on the same day |
| R3 | Phone Today view, summaries as events, ticking items done from the phone | The phone shows the board and ticks round-trip into the vault |
| R4 | GitHub and meetings jobs; the plugin interface for messaging and finance | One plugin runs from outside the core package |

Every stage adds requirement rows to `docs/01-requirements.md` and validation rows to `docs/06-delivery-plan.md`.

## Guards in CI

- The existing secret scan runs on every change.
- A **template check** fails if a bundled prompt, skill or flow contains a literal that should be a profile value: an email address, a phone number, a home path, or a name or host from a locally configured deny list. It has no default list; CI checks the patterns only.
- Tests use a fictional profile ("Sam" in Singapore) and fixture sources, never real data.

## Open questions

| ID | Question | Proposed assumption |
| --- | --- | --- |
| RQ1 | Who is the first pilot user, and on which operating system? | One macOS user after R1 |
| RQ2 | Should the phone Today view replace messaging delivery for new users? | Yes. Messaging stays an optional plugin |
| RQ3 | Windows support | Not planned. Nothing in the design should rule it out |
| RQ4 | Where the `routine` profile's connector permissions are declared | In each job's manifest, shown in the setup sheet and the tools list (FR-23) |
