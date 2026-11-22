# Ataria in Chintu

Design v0.3 · 22 November 2026 · Decision D25 · Status: A1, A2 and the dashboard (A1b) built; A3 in progress (engine core moved); A4 onwards planned

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
| `services/ataria/src/chintu_ataria/defaults/` | The bundled flow, job manifests, prompt and skill templates, and feed packs |
| `services/gateway/` | Adds `/api/v1/ataria/*` routes that pass requests to the engine and relay its events. The routes are defined in the contract first. |
| `apps/desktop/` | An `AtariaSupervisor` beside `GatewaySupervisor`; the setup sheet and `chintu ataria init` / `doctor` |
| `apps/android/` | The Ataria tab |
| `contracts/` | The Ataria API in `openapi.json`, plus examples |

## The engine API (stage A1)

The gateway calls the engine at the base URL from the optional `ataria` block of `gateway.json`:

```json
"ataria": {"url": "http://127.0.0.1:8787/ataria/api/v1", "token_file": "ataria.token"}
```

- The URL must be plain `http` to a loopback address (`127.0.0.1`, `::1` or `localhost`); anything else is refused when the config loads.
- `token_file` (relative paths resolve beside `gateway.json`) holds one token of 32 to 512 characters. The gateway refuses a token file other users can read, and reads it on every call, so rotation needs no restart. The engine may keep only a SHA-256 of the token.
- Every call carries `Authorization: Bearer <token>` and `X-Ataria: 1`.

| Engine route | Answer |
| --- | --- |
| `GET {url}/status` | `{"latest_date": "YYYY-MM-DD" or null}` |
| `GET {url}/today[?date=YYYY-MM-DD]` | A board in the `AtariaBoard` shape; `404` when there is none for that day |
| `POST {url}/done` with `{"done": [uid], "undone": [uid]}` | `{"done", "undone", "done_at"}` |

The phone sees only the gateway's routes, `GET /api/v1/ataria`, `GET /api/v1/ataria/today` and `POST /api/v1/ataria/done`, behind its normal pairing. The gateway keeps only the contract's fields, trims long text, and validates the result before it answers: an engine answer outside the contract is `502`, and an engine that does not answer is `503` and retryable. `GET /api/v1/ataria` always answers, with `enabled: false` when no engine is configured, so the phone shows the ✓ button only where there is a board.

An item and the todo under it may share a uid. A tick applies to the uid, so one tick covers both, and the phone folds such a todo into its item's row.

Push updates (the engine telling the phone that a new board is ready) are not in A1. The phone loads the board when the sheet opens.

## Two parts: the runtime and the dashboard

Ataria has two parts, and a computer can have either or both:

- **The Ataria runtime** runs the jobs: the profile (`ataria.json`), the schedule, the agent runs, the vault and the done ticks. It runs on one computer per person, Linux or macOS. All personal data stays on that computer.
- **The Ataria dashboard** shows the result: the Today checklist, the digest, and the Work, Finance, Kids, Growth, Home and Month views, with past days and the full notes behind each item. Ticking an item anywhere marks it done everywhere, because every tick goes to the one runtime.

**The dashboard always comes from the computer that runs the runtime.** That computer's gateway serves it; every other device only displays it, over the Tailscale connection it is already paired on. Nothing is copied to the other devices.

| Where the runtime runs | That computer's tray | Another computer's tray | Phone |
| --- | --- | --- | --- |
| Computer A | Opens A's dashboard | Opens A's dashboard once paired with A | Shows A's, from the gateway that reports Ataria |
| Moved to computer B | Opens A's dashboard once A is paired with B | Opens its own | Shows B's |

Moving the runtime is a move, not a switch: copy `ataria.json`, the vault and the ticks to the new computer, install there with the runtime, then remove the profile from the old one. Two runtimes at once would build two separate boards.

### The dashboard (stage A1b)

The dashboard is the engine's own web page, served through the gateway:

| Route | Who | What |
| --- | --- | --- |
| `POST /api/v1/ataria/link` | A paired phone or computer (bearer token) | `{"path": "/ataria/open?code=…", "expires_in": 60}`, in the contract as `AtariaDashboardLink` |
| `POST /local/ataria/link` | The tray on this computer (local proof) | The same, for `http://127.0.0.1:<port>` |
| `GET /ataria/open?code=…` | The browser | Uses the code up, sets the session cookie and redirects to `/ataria/` |
| `GET`, `POST`, `DELETE /ataria/<path>` | The browser, with the cookie | Passed to the engine's web root on loopback with the engine token |

- A code works once, for 60 seconds. The session cookie is `HttpOnly`, `SameSite=Strict`, limited to `/ataria/`, `Secure` except on loopback, and lasts 12 hours. Sessions are kept in memory as hashes only, so a gateway restart signs everyone out.
- Revoking the device that opened a session ends that session on its next request.
- Writes must carry `X-Ataria: 1`, which a cross-site form cannot send. Paths are checked segment by segment and cannot leave the engine's web root.
- Every page carries `no-store`, `nosniff`, `X-Frame-Options: DENY` and a content security policy that allows only this origin (plus Google Fonts for styles and fonts). A content type outside a short list is served as `application/octet-stream`.
- The engine's web root defaults to the `url` of the `ataria` block without its `/api/v1`; an optional `web_url` (loopback `http` only) overrides it.

The phone's Today sheet has **Open the full dashboard**, which opens the link in the browser. The tray menu has **Open Ataria dashboard** when this computer runs Ataria, or **Open Ataria dashboard (name)** when it is paired with the computer that does.

### Pairing a computer with the one that runs Ataria

A computer is paired the way a phone is, gets its own token and appears in the other computer's list of paired devices, where it can be revoked:

```
ssh that-computer chintu-desktop pairing-code | chintu-desktop ataria connect -
chintu-desktop ataria dashboard        # or the tray: Open Ataria dashboard (that-computer)
chintu-desktop ataria disconnect       # revoke this computer there and forget it
```

`pairing-code` prints the same one-time code the phone scans as a QR code (10 minutes, one use). `connect` keeps the token in `ataria-remote.json` (mode `0600`) in Chintu's data directory. The installer's `--no-ataria-runtime` option asks no Ataria questions and prints this command instead.

## The profile

Everything personal lives in `ataria.json` on the owner's machine and is read when a run starts; the repository holds only code and templates. The install step writes it with mode `0600` (`chintu ataria init`, or the tray's setup sheet), beside the gateway's `gateway.json` in Chintu's data directory: `~/Library/Application Support/Chintu` on macOS, `$XDG_DATA_HOME/chintu` on Linux. `$ATARIA_PROFILE` overrides the location. The engine refuses a profile other users can read.

The schema is `services/ataria/src/chintu_ataria/schema/profile.schema.json`, and `services/ataria/examples/profile.sam.json` is a complete fictional example. In outline:

| Key | Holds |
| --- | --- |
| `owner` | Shared by every job: `name` (required), `full_name`, `city`, `timezone`, `locale` |
| `vault` | The vault folder (required) |
| `personal` | Personal life, read by the `google`, `personal`, `notes`, `finance` and `ledger` jobs: personal `email`, `family` (name, relation, school, notes), `interests`, `sources` (Gmail, Google Calendar, feed packs, voice notes), `finance` (on or off, currency, banks, statements folder), `growth` (a regional news desk and a course followed lesson by lesson on the dashboard) and free-text `context` notes |
| `work` | Work, read by the `work`, `meetings` and `interviews` jobs, or `{"enabled": false}`: `company`, `role`, work `email`, `clients`, `products`, `team`, `github_orgs`, `sources` (Microsoft 365, Teams, Jira, GitHub, meetings, WhatsApp work groups) and `context` notes (how to judge work items, alert feeds) |
| `jobs` | One entry per job: `enabled`, `agent` (`claude` or `codex`), `model` (or `null` for the agent's default) and, for Codex, `web_search` |
| `schedule` | Morning and midday times; whether a missed step runs on wake |
| `delivery` | `phone`, and `recipients` with name, channel, address, which areas (work, personal, family, finance) and which digests |
| `secrets` | Where each secret lives (`keychain`, `secret-service` or a `file`) and its reference. Never the secret itself |
| `engine` | Not personal; all optional: `state_dir` (run reports, logs, locks, outbox), `layout` (`vault` or `data`), `port`, `base`, `token_sha256_file` (the gateway's engine token, hashed), `allowed_logins`, `dashboard_url` (put in messages) |
| `privacy` | `deny_patterns_file`, a local list of strings that must never leave the machine or reach the repository |

The opinionated job defaults: Claude Code runs `work`, `meetings`, `interviews` and `notes`; Codex runs `google`, `personal` (with web search), `finance`, `ledger` and `review`. Every job can be switched to the other agent or another model during setup.

Unknown keys are allowed and kept, so a newer profile works with older code. Agents keep their own logins (Claude Code, Codex and their connectors); the profile never holds a password or token.

### Setting it up

`chintu-desktop ataria init` asks in four parts (A. about you, B. personal life, C. work, which can be skipped, D. the jobs, one block each for whether it runs, its agent and model, then the schedule, digests, secret references and privacy), says what each answer is used for, and writes the file only after showing a summary. Run again, it edits: each question shows the current answer and Enter keeps it, unknown keys are kept, and the previous file is kept as a dated backup. `chintu-desktop ataria doctor` checks the profile (valid and private), that every bundled template can be filled from it, the timezone, the vault folder, that each chosen agent is installed and looks signed in, the deny list, and secret references stored in files. `install.sh` asks nothing when a profile exists (it runs `doctor`), takes an existing file with `--ataria-profile PATH`, and otherwise offers `init` on a first install. A setup sheet in the tray app, writing the same file through the same code, is planned for a later candidate.

## Templates

Prompts and skills under `services/ataria/src/chintu_ataria/defaults/` are templates, filled from the profile at run time (`chintu_ataria.templates`):

| Syntax | Meaning |
| --- | --- |
| `{{ owner.name }}` | A profile value. A missing one stops the run with a message naming every missing field |
| `{{ owner.role? }}` | Optional: empty when missing |
| `{{ work.clients \| join }}` | A list joined with commas; also `\| names`, `\| bullets`, `\| json` |
| `{{#if family}} … {{else}} … {{/if}}` | A section kept only when the value is present and not empty |
| `{{include skills/work.md}}` | Another template from the same folder, up to three levels deep |
| `{{ATARIA_ROOT}}`, `{{NOW}}` | Upper-case runtime values from the engine (paths, times, the lane), never from the profile |

A test renders every bundled template with the example profile, so a template that refers to a field the schema does not have fails CI.

## Keeping personal information out

- Templates and code never contain a real name, client, host, path, phone number or address; those are profile values.
- `services/ataria/tests` scans the package for email addresses (other than `example.com`), phone numbers, home folder paths and tailnet host names.
- The owner keeps a local deny list of their own names, clients and hosts (`chintu-ataria leaks --deny FILE`, or `ATARIA_DENY_FILE` for the tests) and runs it before pushing. The list is never committed, and a finding names only the list's line number, so a CI log never repeats an entry.

## How a run works

- **Flow.** A bundled `flow.yaml` lists the steps and their times: source jobs, then merge, then delivery. The profile can switch steps off or move times; it cannot add arbitrary commands.
- **Scheduler.** It runs inside the engine and keeps its state in the engine's own SQLite database: last run, next run, lock and result. If the machine was asleep at a scheduled time and `catch_up` is on, a missed step runs once on wake, within a bounded window.
- **Jobs.** Each source job is one agent run. Its prompt is rendered from a template and the profile, and it runs through `packages/agent-adapters` under a new `routine` execution profile. That profile can write only to the job's vault folder and can use only the tools and connectors in the job's manifest. A job writes OKF concepts (Item, Todo, Extract) as the prototype does.
- **Merge.** Deterministic Python with no model call. It reads the jobs' items, removes duplicates, ranks them P0 to P3, applies done ticks, and writes the Board and Todo List concepts.
- **Delivery.** The engine emits "board updated" and "summary ready" events. The gateway relays them over its existing event stream, so the phone updates live and can notify. Other channels, such as messaging, are optional plugins.

Ataria's vault folders follow `docs/12-okf-notes.md`. Each section has an `Ataria/` folder next to the owner's own notes, and the shared OKF profile governs types and keys.

## The engine core (stage A3, first part)

The deterministic parts of the engine are in `services/ataria`; the agent jobs follow lane by lane.

| Module | What it does |
| --- | --- |
| `vault.py` | Logical paths (`work/2026/11/22/items/x.md`) to real files: content in the vault's `Ataria/` folders, operational files in `engine.state_dir`; OKF frontmatter read and atomic write; the owner's timezone |
| `board.py` | The merge: lanes to one board (`app/YYYY/MM/DD/today.md`), done ticks, ranking with at most three P0s, the digest, safe titles outside work, and the outbox (a summary for the owner, a home update for each recipient with the family area and that digest) |
| `server.py` | The engine on loopback: the gateway's contract routes and the dashboard's own API; only the gateway (its token's SHA-256 on file) or a listed Tailscale login gets in |
| `defaults/web/` | The dashboard. Everything personal comes from `GET api/config`: the owner's name, the run times, whether there are children or work, the regional desk and the course |

```
chintu-desktop ataria merge [--date YYYY-MM-DD] [--slot morning|midday] [--no-outbox]
chintu-desktop ataria month-review
chintu-desktop ataria serve [--port N]
```

**The tray runs the engine.** On a computer with an `ataria.json`, the tray starts `chintu-desktop ataria serve` beside the gateway (menu: Ataria engine status, Start or Stop Ataria engine; log `ataria-engine.log`). Before the first start it wires the two together without replacing anything already there: it creates `ataria.token` (mode 0600) when the gateway has none, writes its SHA-256 where the engine looks (`engine.token_sha256_file`, default `engine-token.sha256` in the state folder) whenever it no longer matches, and adds the `ataria` block to `gateway.local.json` when it is missing, read at the gateway's next start. When the engine's port is already taken (for example by the prototype), the tray leaves it alone. A computer installed with `--no-ataria-runtime` has no profile, so the engine never starts there.

On the owner's machine the moved merge built the same board as the prototype from the same files (143 items, no differing field, the same counts and summary), and the moved server served the real board, past days, months and notes.

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

The engine is moved from the prototype, not rewritten, so the owner's board keeps behaving the same:

1. The prototype keeps running unchanged on its machine.
2. **API first.** Stage A1 defines the Ataria API in the contract and builds the phone tab against it. The prototype's app server implements the same API, so the owner gets the phone tab before the move.
3. **Move, don't copy history.** Code comes into `services/ataria` as new files, without the prototype's git history. Each prompt and skill becomes a template: every personal value becomes a profile field or a `context` note, and the leak check must pass with the owner's deny list before each push.
4. The owner's machine switches to the moved engine one lane at a time, with its own `ataria.json`. Its private data stays local.
5. The prototype repository is archived once every lane has moved, or kept only for the owner's private configuration.

## Stages

| Stage | Scope | Done when |
| --- | --- | --- |
| A1 | Ataria API in the contract; gateway pass-through; the phone's Today sheet (board, todos, digest, tick done) | The phone shows the prototype's board through the gateway and ticks round-trip |
| A1b | The full dashboard through the gateway (one-time link, session cookie); the tray's dashboard item; pairing a computer with the one that runs Ataria; `--no-ataria-runtime` | The Mac's tray and the phone open the dashboard of the computer that runs Ataria |
| A2 | Profile schema and loader, template renderer, leak check; the shared lane prompt as the first template | Every bundled template renders for the fictional profile; the package passes the leak check |
| A3 | Move the engine: runner, merge, app server, month close, OKF helpers; lane prompts and skills as templates, one lane per change | The owner's machine runs each moved lane from this repository with the same board as before |
| A4 | Portability: the engine's own scheduler with catch-up, `init`/`doctor`, the tray's `AtariaSupervisor`, macOS | A fresh macOS install produces a feeds-only board with no setup beyond `init` |
| A5 | Plugins for messaging and finance; the optional Linux isolation guide (one account per lane) | One plugin runs from outside the core package |

Every stage adds requirement rows to `docs/01-requirements.md` and validation rows to `docs/06-delivery-plan.md`.

## Guards in CI

- The existing secret scan runs on every change.
- The Ataria tests scan the package for personal values and render every template with the fictional profile (above).
- Tests use the fictional profile ("Sam" in Singapore) and fixture sources, never real data.

## Open questions

| ID | Question | Proposed assumption |
| --- | --- | --- |
| AQ1 | Who is the first pilot user, and on which operating system? | One macOS user after A2 |
| AQ2 | Should the phone tab replace messaging delivery for new users? | Yes. Messaging stays an optional plugin |
| AQ3 | Windows support | Not planned. Nothing in the design should rule it out |
| AQ4 | Where the `routine` profile's connector permissions are declared | In each job's manifest, shown in the setup sheet and the tools list (FR-23) |
| AQ5 | Does the engine ship in the same desktop packages or as an optional add-on package? | The same packages, started only when `ataria.json` exists |
