# Delivery plan and validation

Design v0.2 · 10 October 2026

**Current phase: parallel implementation authorized on 10 October 2026.** The owner accepted Android speech recognition, text-only communication with Ubuntu, and Android speech playback. Android (M1/client side of M2), gateway/task handling (M2/M4), and adapters (M3) work from the shared [API contract](08-api-contract.md). Device and real-provider acceptance tests remain pending; local checks are reported separately.

The [build readiness record](validation/2026-10-10-build-readiness.md) covers fresh Python installation, full specification validation, operator configuration, trial tooling, and Android build restrictions. Follow [the concrete setup sequence](09-setup-and-first-demo.md) for the next device gates.

The owner subsequently requested the [Chintu experience redesign](10-chintu-experience.md): animated persona, selectable Bluetooth/phone audio, multiple paired Tailscale computers, and spoken destination switching. This extends M1/M2 and foreground conversation without claiming completion of M8 background wake detection. See [redesign verification](validation/2026-10-10-experience-redesign.md); basic playback in the earlier app passed by owner report, while redesigned-device acceptance remains pending.

## Delivery approach

Test each boundary with a controlled substitute before connecting real components. Android speech can use an echo reply; transport can use typed text; adapters can use a local client; the task manager can use a scripted adapter. An isolated pass proves that component only. M5 connects the pieces, and M7 establishes V1 readiness.

| Phase | Deliverable | Depends on | Independent demonstration |
| --- | --- | --- | --- |
| M0 — Setup inventory | Named devices, accounts, workspace, and test fixtures | None | Record prerequisites and unresolved decisions |
| M1 — Android speech | Push-to-talk, transcript, Android TTS, audio routes | Phone/language details from M0 | Speak → see text → hear an echo; no Ubuntu or agent |
| M2 — Private text transport | Pairing, authenticated text requests and events | Phone/Ubuntu/Tailscale details from M0 | Type on phone → receive a scripted reply from Ubuntu; no speech or agent |
| M3 — Local agent adapters | First agent, then second, behind a common contract | Account/workspace choices from M0 | Local text client → real agent task and follow-up; no phone |
| M4 — Durable task manager | SQLite, queue, attempts, event replay, reconciliation | Agreed adapter/event contract | Local client + scripted adapter; no real provider or phone |
| M5 — First voice task | M1–M4 connected with one real agent | M1, M2, first passing M3 adapter, M4 | Speak → real read-only task → hear recorded result |
| M6 — Both agents and controls | Provider selection, clarification, approvals, cancellation | M5 and both M3 adapters | Repeat the same control scenarios separately with each provider |
| M7 — V1 reliability | Physical-device faults, supervision, limits, restore | M6 | Daily-use exercise and recovery evidence |
| M8 — Optional hands-free | Wake word; full duplex only if separately validated | M7 and a confirmed listening requirement | Local wake detector first; integrate bounded recognition later |

M1, M2, and M3 can be developed and tested separately after their own prerequisites are known. M4 can start once the contract is agreed, using fixtures even if a real adapter is unfinished. No calendar estimate is committed before the relevant compatibility results.

## Common test contract and evidence

Use the behavioral interfaces in [04-agents-and-contracts.md](04-agents-and-contracts.md), concrete [HTTP contract](08-api-contract.md), and [local adapter protocol](../contracts/adapter-protocol.md) across real and scripted adapters. Scripted fixtures exercise available lifecycle events without inference or real workspace effects; unimplemented question/approval support must remain explicitly unavailable.

For each phase, record the date, device/OS/runtime versions, selected engines/languages, inputs, expected versus observed results, pass/fail, and known gaps. Store written results under `docs/validation/`, for example `m1-android-speech.md`. The [local implementation baseline](validation/2026-10-10-local-baseline.md) records fixture checks separately from pending device/provider gates. Keep secrets and raw audio out of ordinary evidence. Record timings separately for recognition, transport, durable acceptance, execution, and TTS startup. Agree on the recognition corpus and accuracy target before M1 testing; publish failures as well as successes.

## M0 — Setup inventory

Record the phone/Android version, Bluetooth device, Ubuntu version/hardware, Tailscale availability, first workspace, selected language, and allowed execution profile. Confirm supported provider sign-in and billing choices and whether dedicated agent sessions satisfy the requirement. Keep unanswered questions visible in [07-decisions-and-sources.md](07-decisions-and-sources.md).

**Independent check:** review the inventory without installing or running the application. Each later phase may proceed when its own prerequisites are known; an agent account decision does not block a phone speech experiment.

**Pass:** a named target setup and harmless fixture workspace exist for the next authorized test. No credential export or unsupported desktop-chat protocol is planned.

## M1 — Android speech, without Ubuntu

Build a small native screen with push-to-talk, final editable transcript, type fallback, an echo/speak control, stop playback, and visible speech-service status. Use `SpeechRecognizer` and `TextToSpeech`; prefer available on-device recognition and offline voices. Make network-backed Android recognition an explicit choice. No OpenAI speech API is used.

**Independent checks:**

- Speak the agreed corpus; compare final text and recognition timing. Exercise correction and repeat recording; partial text never submits work.
- Speak fixed replies through Bluetooth and phone output, then interrupt playback with a control or new push-to-talk turn.
- Test actual recognition-service input, Bluetooth loss, a phone call, microphone denial, missing engines/languages, and offline behavior. Airplane-mode testing should re-enable Bluetooth if needed.

**Pass:** the agreed corpus meets its recorded target, route/fallback behavior is demonstrated on the real devices, and unsupported offline behavior is accurately reported. Missing speech support leaves typing and readable replies usable. Foreground-service setup alone does not prove screen-off recognition.

## M2 — Private text transport, without speech or agents

Build the loopback gateway, Tailscale access, local enrollment, revocable phone credential, typed request screen, and bounded scripted text/event replies. Validate the protocol version and authorization for requests and subscriptions.

**Independent checks:** type on the phone and receive an echo on Wi-Fi and cellular; drop/reconnect the connection; reject wrong/revoked credentials; inspect payloads for text and task metadata only. Revocation closes live subscriptions. Label echo responses as test replies, not accepted agent jobs.

**Pass:** an enrolled phone reaches Ubuntu privately and reliably, unauthorized access is rejected, and no audio is transmitted. This phase proves transport, not durable execution or guaranteed background notifications.

## M3 — Agent adapters, without the phone

Use a local text client and a harmless workspace fixture. Validate the chosen first provider, then the second independently. Pin each runtime/schema and record its supported authentication, session, progress, permission, continuation, and interruption capabilities.

**Independent checks:** summarize a known fixture, ask a follow-up in the same provider session, observe a failure, request cancellation, and exercise a real runtime approval when supported. Attempt out-of-scope access to test the intended inspect profile. Pass spoken-looking text and shell metacharacters as structured instruction data, never launcher shell syntax.

**Pass:** the adapter reports traceable results and real session identity, respects the tested execution boundary, and explicitly rejects unsupported operations. M5 may use the first passing adapter; full support for both providers waits for M6. Never substitute API billing or a global permission bypass silently.

## M4 — Durable task manager, with a scripted adapter

Deliver SQLite task/event storage, registered projects, a bounded queue, workspace serialization, submission/message deduplication, attempts, approval records, and recovery reconciliation. Exercise the manager through a local client before integrating Android.

**Independent checks:** retry a submission after losing its response; conflict on the same key with changed content; replay events after a cursor; cancel queued/running work; expire an approval; race completion with cancellation; terminate/restart the manager; simulate unknown worker ownership and a failed database commit.

**Pass:** acceptance follows a durable commit; duplicates do not launch another attempt; history survives restart; unsupported profiles fail before launch; uncertain work is marked interrupted rather than silently relaunched. Expiry/replay cannot grant permission. Scripted passes must later be repeated with real process behavior in M6–M7.

## M5 — First end-to-end voice task

Connect Android speech, private transport, the durable manager, and one passing agent adapter. Use a known read-only fixture and visible project/provider/task selection. Route a small command vocabulary to explicit operations; unclear project/task references require clarification. Supply concise speech-ready result text from the adapter, grounded in the recorded outcome.

**Independent integration checks:** say “Summarize this project,” observe one task ID, hear acceptance after commit and the result after completion, ask one follow-up, and compare spoken messages with the task view. Disconnect the phone after acceptance; confirm work continues and the result is available after reconnecting. Inspect transport payloads and speech/event deduplication.

**Pass:** one real voice request produces one useful traceable task and spoken result. Old replayed progress is not automatically spoken. Stopping playback leaves the job running. Failure is spoken/displayed as failure. This is the first useful demo, not the V1 release.

## M6 — Both providers and complete task controls

Run the same fixture/control scenarios separately for Claude and Codex. Deliver provider selection, same-session follow-ups, ordinary clarifications, task-scoped approvals, artifact retrieval, and reconciled cancellation. Use an explicitly permitted fixture edit only after the develop profile is validated.

**Independent checks:** change the default provider without changing an existing task; answer a named question; allow/deny/expire/replay an actual approval; cancel queued and running jobs; resolve completion races; verify artifact authorization and visible known partial effects. New privilege grants use the unlocked authenticated UI; speech alone does not elevate permissions. Test unsupported active steering explicitly.

**Pass:** both adapters satisfy their advertised V1 contract, with accurate UI/speech outcomes and no approval bypass or competing runner. A missing required capability blocks that provider's V1 sign-off rather than being hidden by a fixture pass.

## M7 — Reliability and daily-use release

Deliver supervision, tested worker ownership/restart handling, bounded output/queue/runtime, retention, operational configuration, and consistent backup/restore. Complete the validation matrix below and verify every V1 requirement.

**Independent checks:** repeat network loss, Bluetooth loss, calls, app suspension, speech-engine failure, provider outage, host suspend, logout/reboot, disk failure, oversized output, and database/artifact restore. Measure NFR-04 through NFR-06 on documented network conditions. Validate supported background playback/listening on the actual phone separately from foreground operation.

**Pass:** ten representative daily-use tasks include a deliberate disconnect and clarification, with no unexplained duplicate execution or false success. Recovery restores state without replaying external effects. Report missed latency targets and unsupported background behavior explicitly. Owner stop-all and client revocation work. Reliable offline-phone alerts remain a separate push-delivery feature.

## M8 — Optional hands-free behavior

Choose and test a local wake-word engine independently of Ubuntu and the standard Android recognizer. After validation, use it to activate bounded recognition turns. Full duplex, echo control, and spoken barge-in require a separate acoustic test; repeatedly restarting `SpeechRecognizer` is not an always-listening design.

**Independent checks:** agree on a corpus, noise conditions, observation period, false-wake/miss targets, and battery budget; then measure on the target phone. Test lock, calls, Bluetooth reconnect, process termination, reboot behavior, and self-triggering from TTS.

**Pass:** the agreed detection/battery targets and explicit session controls work on the selected hardware. Keep push-to-talk available. No promise of hidden listening, automatic reboot recovery, or suspended-phone delivery follows from this phase.

## Validation matrix

| Scenario | Expected result | Requirements |
| --- | --- | --- |
| Duplicate submission with lost first response | One task ID and one attempt; no duplicate execution | FR-08, NFR-02 |
| Same key with changed instruction | Conflict and original task unchanged | NFR-02 |
| Disconnect before server acceptance | No success claim; explicit retry can establish acceptance | FR-12, NFR-03 |
| Disconnect after acceptance | Work continues; ordered events replay on reconnect | FR-07–08 |
| Event cursor older than retained history | Current snapshot plus a new valid cursor | FR-15 |
| Gateway dies after worker starts | Attempt reconciled or marked interrupted; no blind relaunch | NFR-03 |
| Provider completes during cancellation | One reconciled terminal state with accurate outcome | FR-09, FR-12 |
| Approval expires, is replayed, or changes target | No new action is authorized | FR-10 |
| Permission already granted for this task | Routine authorized steps proceed without repeated prompts | FR-10 |
| Prompt contains shell metacharacters | Treated as instruction data, not launcher shell syntax | NFR-10 |
| Project includes an out-of-scope symlink or hook | Runner policy enforced; no ungranted access/execution | NFR-10 |
| `inspect`/`develop` task reads or edits outside the workspace (absolute path, `~`, `..`, symlink) | PreToolUse hook denies; task reports the refusal | NFR-10 |
| Local process or rebinding page calls `/pair` or `/local/*` without the local secret | Generic 403; no enrollment secret minted | NFR-10 |
| Second paired phone reads or cancels the first phone's task | 404; task unchanged | FR-04 |
| Phone credential older than `token_ttl_days` | 401; pairing again restores access | FR-04 |
| Eleven pairing attempts within a minute | 429 with `Retry-After`; secrets unaffected | NFR-10 |
| Agent claims success but execution failed | UI and speech report recorded failure | FR-07, FR-12 |
| Provider rate limit or expired login | Clear blocked/failure state and bounded recovery | FR-12 |
| Disk full while submitting | No acknowledgment of an unrecorded task | NFR-03 |
| Artifact ID/path tampering | Denial without reading arbitrary host files | FR-04 |
| Oversized worker output | Bounded memory and preserved lifecycle events | NFR-07 |
| Bluetooth route lost or phone call arrives | Audio pauses safely and shows the reason | FR-01–03, FR-12 |
| Headset disconnects during a turn | Discard the interrupted capture, confirm phone fallback, and require repetition | FR-03, FR-16 |
| Voice switches between two paired computers | Confirm authenticated target, retain audio route, and route only subsequent tasks to the selected host | FR-17–18 |
| Unknown/ambiguous/offline destination | Preserve previous destination and draft; no command becomes an agent task | FR-12, FR-18 |
| Acceptance arrives after app backgrounds | Persist accepted identity and clear only the matching submitted draft; no stale draft resend on reopen | FR-08, FR-17, NFR-02 |
| Result arrives while hands-free is listening | Hold reply until recognition ends; never speak over capture or replay old catch-up events | FR-07, FR-19 |
| Stop-playback control or new push-to-talk turn during TTS | Speech queue clears locally; job continues; spoken cancel while listening is a separate request | FR-11 |
| Desktop asleep | Host unavailable; no false “started” response | FR-12 |
| Phone app suspended | No guarantee of live delivery; results retained for reconnect | FR-07–08 |
| Partial recognizer result changes before final text | No task is submitted from partial text | FR-01, NFR-02 |
| Recognition unavailable or selected language missing | Visible error and editable/typed input; no silent online fallback | FR-01, FR-12 |
| Android TTS unavailable or selected voice missing | Result remains visible as text; job state unchanged | FR-02, FR-07, FR-12 |
| Assistant playback enters the microphone | V1 stops playback before listening; no task from the assistant's own speech | FR-01, FR-11 |
| Phone reconnects and receives old events | History restored without narrating stale progress or duplicating speech | FR-07–08 |
| Inspect phone-to-Ubuntu request/event payloads | Text/task data only; no microphone audio | FR-01, NFR-08 |
| Claude Agent SDK task per profile (`inspect`, `develop`, `desktop`), follow-up, cancel, SDK error, stream loss | Fake-SDK tests pass; `desktop` refused unless listed; one real inspect call recorded; permission prompts denied visibly | FR-05–06, FR-08–10, NFR-07 |
| Two turns in one conversation, by voice and by chat | Second turn resumes the first turn's session in the conversation's folder; gateway tests pass; real two-turn recall on the phone pending | FR-20, FR-21 |
| Event stream: replay from `Last-Event-ID`, live push, heartbeat, revocation, per-client and total limits, shutdown with an open stream | Gateway stream tests pass; on a real socket the first event arrived 35 ms after submission and shutdown finished in 3.2 s; parser tests pass; phone reconnect on network change pending | FR-07, FR-21, NFR-06 |
| Stream unavailable (older gateway, `401`, stream drops, app backgrounded) | Phone keeps polling with its persisted cursor; no event lost or spoken twice | FR-07–08, NFR-02 |
| Long reply streamed into the chat | Steps and reply text appear while the turn runs; streamed text equals the full reply even past the 2,000-character `output`; backfilled and live events rebuild the same transcript; adapter, gateway and transcript tests pass; device check pending | FR-21 |
| New chat by button, by “Chintu, new chat” and after a Settings change | Fresh conversation and session; old chat stays listed; voice parser tests pass; device acceptance pending | FR-20, FR-22 |
| Working directory outside the workspace (`..`, absolute, `~`, escaping symlink), missing, or deleted after creation | `422`/`404` at creation; `409` on the next turn; worker never runs outside the registered workspace | FR-22, NFR-10 |
| Quarantined folder and a conversation inside or above it | Turn refused until reconciliation; sibling folders still run | FR-22, NFR-03 |
| Tools sheet for `inspect`, `develop` and `desktop` | Fixed lists for confined profiles; `desktop` probe lists MCP servers, tools and skills without sending a prompt; fallback states its reason | FR-23 |

## Requirement coverage

| Requirement group | Isolated checks | Integration / release evidence |
| --- | --- | --- |
| FR-01–03 | M1 Android speech and routing | M5, M7 |
| FR-04 | M2 authentication/transport | M5–M7 |
| FR-05–06 | M3 provider/fixture behavior; M4 project validation | M5 first provider; M6 both |
| FR-07, FR-11 | M1 speech interruption; M4 outcome/event contracts | M5–M7 |
| FR-08, FR-15 | M3 session identity; M4 durability/replay | M5–M7 |
| FR-20–23 | Conversation, folder and tool-catalog gateway tests; voice command parser tests | Phone two-turn recall, folder picker and tools sheet on a real computer |
| FR-09–10 | M3 runtime controls; M4 lifecycle/approval fixtures | M6–M7 real adapters |
| FR-12 | Failure cases in M1–M4 | M5–M7 |
| NFR-01–03 | M2 private access; M4 acceptance/deduplication/recovery | M5–M7 |
| NFR-04–06 | Segment timings in M1–M4 | M7 end-to-end latency evidence |
| NFR-07–10 | M1 speech privacy; M3 capabilities/confinement; M4 resource bounds | M6–M7 |
| FR-13–14 | M8 wake detector and acoustic tests: Vosk wake word on the Pixel 9 Pro, 14/15 caught and 0/15 false alarms offline, device scenarios W1–W9 pass ([2026-10-17](validation/2026-10-17-wake-word.md)) | Owner-voice, battery and headset measurements; FR-14 full duplex |

## Deferred capabilities

Multiple Ubuntu hosts, additional users, autonomous schedules, external messaging, unrestricted desktop UI control, guaranteed offline-phone notifications, and cross-provider context handoff need separate requirements. They should reuse the task/authentication contracts rather than be added as special cases in speech handling.
