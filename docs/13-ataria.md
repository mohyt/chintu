# Ataria in Chintu

Design v0.2 · 22 November 2026 · Decision D25 · Status: proposed plan, not implemented

Ataria runs scheduled agent jobs. They read the owner's sources (mail, calendar, feeds, code hosts, notes), write what they find into the OKF vault, and merge it into one daily board: what needs attention today, open todos, and an outside-world digest. Today Ataria is the owner's private prototype on one Linux machine. Its prompts contain names and sources, systemd schedules it, and each job runs as a separate Linux account.

This plan brings Ataria into this repository as an optional, public component that anyone can install on macOS or Linux:

- **One phone app.** The Chintu app gains an Ataria tab (Today board, todos, digest) next to voice and chat.
- **One desktop app, two daemons.** The Chintu tray app installs and supervises both the Chintu gateway, which is unchanged, and the **Ataria engine**, a separate process with its own scheduler, job runner, merge and API.
- **One pairing.** The phone talks only to the gateway. The gateway passes Ataria requests to the engine on loopback, so the engine is never reachable from the network.

## Principles

1. **Public code, opinionated defaults.** The repository ships the engine, a default flow, and default prompts, skills and execution profiles. Running the defaults should produce a useful board on the first morning.
2. **No credentials or personal information in the repository.** Everything about a person is read at run time from a JSON profile that the install step creates on their machine (below). Prompts and skills are templates; values such as the owner's name, timezone, clients or recipients come from the profile and are never committed.
3. **Credentials stay where they already live.** Agents use their own logins (Claude Code, Codex and their connectors). Any other secret goes into the operating system's credential store (Keychain on macOS, Secret Service on Linux), and the profile holds only a reference to it. The engine never writes a secret to the vault, the profile, a log or a database.
4. **Two processes, kept apart.** A long morning run never blocks voice control, and either daemon can restart or update without the other. The engine reuses `packages/agent-adapters` to run agents but does not import the gateway.
5. **Not tied to one operating system.** The engine runs its own schedule. No systemd timers, launchd jobs, root setup or extra user accounts are required. The desktop app starts both daemons, using its existing launchd and systemd user units for login start.
6. **Off by default.** A Chintu install without an Ataria profile behaves exactly as it does today: the engine is not started and the phone shows no Ataria tab.

## Components

```
 phone app ── Tailscale HTTPS, one bearer token ──▶ Chintu gateway ── loopback, per-start secret ──▶ Ataria engine
 (voice, chat, Ataria tab)                          (tasks, pairing, events)                          (scheduler, jobs, merge, API)
                                                                                                         │
 Chintu tray app ── supervises both daemons, runs init/doctor                                            ├─ agent adapters ▶ Codex / Claude
                                                                                                         └─ OKF vault (items, board, todos)
```

| Path | What it holds |
| --- | --- |
| `services/ataria/` | The engine: profile loading, scheduler, job runner, prompt rendering, merge, month close, the local API |
| `services/ataria/defaults/` | The bundled flow, job manifests, prompt and skill templates, and feed packs |
| `services/gateway/` | Adds `/api/v1/ataria/*` routes that pass requests to the engine and relay its events. The routes are defined in the contract first. |
| `apps/desktop/` | An `AtariaSupervisor` beside `GatewaySupervisor`; the setup sheet and `chintu ataria init` / `doctor` |
| `apps/android/` | The Ataria tab |
| `contracts/` | The Ataria API in `openapi.json`, plus examples |

## The profile

The install step writes `ataria.json` with mode `0600`. It runs as `chintu ataria init` or from the tray's setup sheet: it asks a few questions and checks the agent logins. The file sits beside the gateway's `gateway.json` in Chintu's data directory: `~/Library/Application Support/Chintu` on macOS, `$XDG_DATA_HOME/chintu` on Linux. `chintu ataria doctor` re-checks it later.

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
- Lists such as clients, products and recipients start empty. The owner adds to them in the setup sheet or the file.
- `privacy.deny_patterns_file` points at a local list of strings, such as family names or account numbers, that must never appear in anything the engine sends off the machine. The list itself stays local.

## How a run works

- **Flow.** A bundled `flow.yaml` lists the steps and their times: source jobs, then merge, then delivery. The profile can switch steps off or move times; it cannot add arbitrary commands.
- **Scheduler.** It runs inside the engine and keeps its state in the engine's own SQLite database: last run, next run, lock and result. If the machine was asleep at a scheduled time and `catch_up` is on, a missed step runs once on wake, within a bounded window.
- **Jobs.** Each source job is one agent run. Its prompt is rendered from a template and the profile, and it runs through `packages/agent-adapters` under a new `routine` execution profile. That profile can write only to the job's vault folder and can use only the tools and connectors in the job's manifest. A job writes OKF concepts (Item, Todo, Extract) as the prototype does.
- **Merge.** Deterministic Python with no model call. It reads the jobs' items, removes duplicates, ranks them P0 to P3, applies done ticks, and writes the Board and Todo List concepts.
- **Delivery.** The engine emits "board updated" and "summary ready" events. The gateway relays them over its existing event stream, so the phone updates live and can notify. Other channels, such as messaging, are optional plugins.

Ataria's vault folders follow `docs/12-okf-notes.md`. Each section has an `Ataria/` folder next to the owner's own notes, and the shared OKF profile governs types and keys.

## Isolation

The prototype isolates jobs with one Linux account per job plus file ACLs. The engine uses guardrails that work on both operating systems:

- the `routine` profile's confinement: the PreToolUse hook for Claude, and the sandbox and writable roots for Codex, limited to the job's folder;
- one agent run per job, so a failing job cannot block the others;
- private sections (finance, family, personal) run as separate jobs with separate folders, and the work job never gets them as readable roots;
- the engine listens on loopback only and requires the per-start secret, as the gateway's local endpoints do.

This is weaker than separate accounts. A Linux owner who wants the stronger model can follow an optional hardening guide that runs jobs under extra accounts.

## Sources, in order

| Stage | Sources | Notes |
| --- | --- | --- |
| A2 | Feeds (curated packs: world, regional, tech, AI, cloud, security, regulators), notes from Chintu | No login needed. Packs ship in the repository and the owner picks them in the profile. |
| A3 | Google mail and calendar | Through the agent's own Google connector. The engine stores no Google token. |
| A3 | Work through Claude | Clients and products come from the profile. |
| A4 | GitHub snapshot, meetings and transcripts | Optional; each has a check in `doctor`. |
| A4 | Messaging bridges, finance statements and a ledger | Plugins with their own guides. They are bank- and country-specific, so they are not on by default. |

## Migration from the prototype

1. The prototype keeps running unchanged on its machine.
2. **API first.** Stage A1 defines the Ataria API in the contract and builds the phone tab against it. The prototype's existing app server implements the same API, so the owner gets the phone tab before the public engine exists.
3. The public engine reaches parity in stages. The owner then switches that machine to it one job at a time. The machine's private data and profile stay local, and only generic improvements come back here.
4. The prototype repository is archived once every job has moved.

## Stages

| Stage | Scope | Done when |
| --- | --- | --- |
| A1 | Ataria API in the contract; gateway pass-through and event relay; the phone Ataria tab (board, todos, digest, tick done) | The phone shows the prototype's board through the gateway and ticks round-trip |
| A2 | `services/ataria` core: profile, `init`/`doctor`, prompt templating, `routine` profile, scheduler with catch-up, feeds job, merge to a board; the tray's `AtariaSupervisor` | Fresh macOS and Linux installs produce a feeds-only board with no setup beyond `init` |
| A3 | Google, work and notes jobs; todos; done ticks; month close | The owner's morning board from the engine matches the prototype's on the same day |
| A4 | GitHub and meetings jobs; the plugin interface for messaging and finance | One plugin runs from outside the core package |

Every stage adds requirement rows to `docs/01-requirements.md` and validation rows to `docs/06-delivery-plan.md`.

## Guards in CI

- The existing secret scan runs on every change.
- A **template check** fails if a bundled prompt, skill, flow or manifest contains a literal that should come from the profile: an email address, a phone number, a home path, or a name or host on a locally configured deny list. The repository has no default list; CI checks the patterns only.
- Tests use a fictional profile ("Sam" in Singapore) and fixture sources, never real data.

## Open questions

| ID | Question | Proposed assumption |
| --- | --- | --- |
| AQ1 | Who is the first pilot user, and on which operating system? | One macOS user after A2 |
| AQ2 | Should the phone tab replace messaging delivery for new users? | Yes. Messaging stays an optional plugin |
| AQ3 | Windows support | Not planned. Nothing in the design should rule it out |
| AQ4 | Where the `routine` profile's connector permissions are declared | In each job's manifest, shown in the setup sheet and the tools list (FR-23) |
| AQ5 | Does the engine ship in the same desktop packages or as an optional add-on package? | The same packages, started only when `ataria.json` exists |
