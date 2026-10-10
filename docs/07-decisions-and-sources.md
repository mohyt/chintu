# Decisions, questions, and sources

Design v0.2 · 10 October 2026

## Decision status

“Confirmed” means explicitly provided by the owner. “Proposed” is the design recommendation. A proposed decision is not an implementation approval. Revisit a choice when a compatibility result contradicts it; record the reason rather than editing assumptions silently.

| ID | Decision | Status | Rationale and consequence |
| --- | --- | --- | --- |
| D1 | Ubuntu executes desktop tasks | Confirmed | Local files and tools remain on the designated computer |
| D2 | Phone-to-desktop connectivity uses Tailscale | Confirmed | No custom public ingress is required |
| D3 | Project home is `~/dev/continuum/chintu` | Confirmed | All design files and future project-owned assets have one home |
| D4 | Design precedes implementation; parallel implementation now authorized | Confirmed | Owner requested phase agents on 10 October 2026; device/accounts and deployment remain explicit |
| D5 | Use a native Android companion | Confirmed design direction | Owner accepted the Android speech and text transport design |
| D6 | Separate voice sessions from durable jobs | Proposed | Conversation and mobile connection can end without cancelling work |
| D7 | Start with push-to-talk, then wake word/full duplex | Proposed | Resolve real audio-route behavior before continuous listening |
| D8 | Use local programmatic Claude/Codex sessions | Proposed | Supported integration boundary; existing app-chat handoff stays an explicit question |
| D9 | Use Python/FastAPI, SQLite, and a user service | Proposed | Small operational footprint for one owner and host |
| D10 | Android handles speech; Ubuntu receives and returns text/task data | Confirmed design direction | Replaces the earlier audio-relay proposal; no OpenAI speech APIs; Android offline capabilities need device validation |
| D11 | One active task by default, then a bounded queue | Proposed | Reduces workspace conflicts; separate worktrees can support later concurrency |
| D12 | Use task-scoped grants and precise additional approvals | Proposed | Autonomous authorized work with explicit boundaries on new permissions |
| D13 | Pair the app in addition to private network access | Proposed | Independent client identity and revocation |
| D14 | Keep raw audio transient by default | Proposed | Task history is useful without indefinite recordings |
| D15 | Treat restart uncertainty as interrupted work | Proposed | Prevent duplicate side effects from automatic resubmission |
| D16 | Chintu is a calm, concise companion with an animated presence | Confirmed | Owner requested a name, persona, and open-app speaking UX; text and destination remain visible |
| D17 | Select among named Ubuntu/Mac computers on Tailscale using UI or Chintu voice commands | Confirmed | Pair each host independently; only confirm after authenticated access succeeds; aliases must resolve uniquely |
| D18 | Audio source and execution destination are independent | Confirmed | Owner clarified Bluetooth supplies speech while computer selection routes text; no headset falls back to phone speaker/microphone |
| D19 | Offer explicit foreground hands-free and separate automatic-send controls | Implemented scope | Open-app conversation uses bounded speech turns, not a screen-off wake engine; both controls stop on background; physical acceptance remains pending |
| D20 | Minimal purple-orb home and call-style audio selector | Confirmed | Owner supplied a visual reference and requested simpler UI; move forms/settings to sheets, automatically detect connected Bluetooth and otherwise use phone audio |
| D21 | Add a Claude Agent SDK execution provider (`claude-agent`) beside the restricted CLI adapters | Confirmed | Owner requested tasks that work like Claude Code/Claude Desktop: full built-in tools, the host's MCP servers, same-session follow-ups, and interrupt cancellation. Enabled by explicit config plus a profile list rather than CLI protocol evidence; `desktop` (`bypassPermissions`) is opt-in per runtime and project; no API key is passed and the owner's own CLI login is reused, so entitlement/billing stays Q4; cloud connectors from claude.ai/Claude Desktop remain unreachable |

On 10 October 2026, the owner excluded OpenAI text-to-speech, requested Android speech recognition with text sent to Ubuntu, and accepted the resulting data-flow diagram. D10 records that direction. The owner subsequently requested parallel phase agents with an API contract first. [08-api-contract.md](08-api-contract.md) provides the implementation baseline. Real provider billing, device capability, and existing-chat compatibility questions remain unresolved until explicitly configured and tested.

## Open questions

| ID | Decision needed | Proposed assumption until resolved | Needed by |
| --- | --- | --- | --- |
| Q1 | Google Pixel 9 Pro confirmed by owner; installed Android version, Bluetooth microphone, and offline speech engines/languages still need checking | Prefer on-device recognition and offline TTS; measure actual routing and retain typing if unavailable; see [target setup](validation/m0-target-setup.md) | M0 inventory / M1 speech validation |
| Q2 | Is screen-off wake-word listening eventually required? | Owner requested the open-app agent UX; foreground hands-free is explicit, dedicated background wake word remains V2 | M8 |
| Q3 | Does “Claude Desktop or GPT itself” require continuing an existing app chat, or are dedicated Chintu agent sessions acceptable? | Dedicated local agent sessions | Before choosing adapters |
| Q4 | Reuse supported agent CLI subscriptions, allow agent API billing, or both? What is the agent budget? | No entitlement or budget assumed; OpenAI speech APIs excluded; Android network speech is a separate explicit choice | M0 / M3 agent validation |
| Q5 | Which Ubuntu release/hardware, first project, and first useful task should be supported? | One registered project and read-only repository investigation first | M0 / M3 |
| Q6 | Can Ubuntu stay awake while away, and should service operation continue after logout/reboot? | Availability is explicit; no automatic power-policy change | M0 / M7 |
| Q7 | English only or Hindi/Hinglish too? | English test baseline; verify each selected recognition/TTS language | M1 |
| Q8 | Are reliable notifications needed when the phone app is suspended or offline? | Results retained for reconnect; no push-delivery guarantee | Before adding push infrastructure |
| Q9 | Which future tools are actually needed: repository work, browser, email, calendar, general desktop apps? | Repository/file tasks first; other tools individually scoped | After V1 workflow selection |
| Q10 | How restrictive should execution isolation be, and which project actions can be pre-authorized? | Tested restricted profile; broader grants explicit | M3 before real agent tasks; M6 for edits |

Q1, Q3, Q4, and Q5 have the greatest architectural impact. The documentation can be reviewed while they are unanswered; implementation must not silently resolve account or permission choices.

## Alternatives considered

| Alternative | Assessment |
| --- | --- |
| Existing Claude Remote Control | Useful standalone path for continuing local Claude Code work from mobile, but its vendor connection does not supply the complete custom Tailscale and multi-provider voice design |
| Automating Claude Desktop/ChatGPT windows | Requires fragile application-specific UI behavior and may not preserve supported session semantics; choose only if exact desktop-chat reuse is a confirmed requirement and supported access is available |
| Phone browser/PWA as the primary client | Useful for a foreground prototype; background microphone and device-routing requirements favor a native app for the intended experience |
| Cloud speech with an Ubuntu relay or direct WebRTC | Superseded by accepted Android speech/text transport design; not part of V1 |
| Fully local speech and agent inference | Android on-device speech is preferred where supported; local agent models remain a separate hardware/capability decision |
| Multiple workflow services or a distributed queue | No current single-user scale requirement; begin with a durable local task manager |

## Official source ledger

The initial source ledger was checked on 8 October 2026. Android speech references S14–S16 were checked during the 10 October 2026 design revision, and S17–S18 while implementing D21. They support capability statements, not a claim that the proposed system has been implemented or tested. Recheck provider behavior against the exact versions selected during M3.

| ID | Official source | Relevant evidence |
| --- | --- | --- |
| S1 | [Android foreground service types](https://developer.android.com/develop/background-work/services/fgs/service-types) | Microphone service permissions and background-start restrictions |
| S2 | [Android Audio Manager communication-device guide](https://developer.android.com/develop/connectivity/bluetooth/ble-audio/audio-manager) | Communication-device selection and callbacks for compatible Bluetooth audio |
| S3 | [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) | Private tailnet service access, HTTPS, access rules, and identity-header considerations |
| S4 | [OpenAI Realtime API](https://developers.openai.com/api/docs/guides/realtime) | Historical source for superseded cloud voice proposal; excluded from V1 |
| S5 | [Run Claude Code programmatically](https://code.claude.com/docs/en/headless) | CLI-based programmatic execution, structured output, continuation, and configuration behavior |
| S6 | [Claude Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) | Agent loop integration, supported SDKs, and authentication guidance |
| S7 | [Claude Agent SDK permissions](https://code.claude.com/docs/en/agent-sdk/permissions) | Permission modes/rules and runtime approval callbacks |
| S8 | [Codex App Server](https://learn.chatgpt.com/docs/app-server) | Local integration, session/event/approval contract, transport maturity, and authentication modes |
| S9 | [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk) | Programmatic local agent control and session continuation |
| S10 | [Claude Code Remote Control](https://code.claude.com/docs/en/remote-control) | Mobile continuation of local work and the vendor-mediated network path |
| S11 | [Ubuntu loginctl manpage](https://manpages.ubuntu.com/manpages/noble/man1/loginctl.1.html) | User-manager lifetime and lingering behavior; actual host configuration still requires verification |
| S12 | [SQLite online backup](https://sqlite.org/backup.html) | Consistent database-backup approach |
| S13 | [Bluetooth Advanced Audio Distribution Profile](https://www.bluetooth.com/specifications/specs/advanced-audio-distribution-profile-1-4/) | Media audio profile reference; device microphone support remains hardware-specific |
| S14 | [Android SpeechRecognizer](https://developer.android.com/reference/android/speech/SpeechRecognizer) | Bounded speech recognition, on-device availability from API 31, and continuous-recognition limitations |
| S15 | [Android TextToSpeech](https://developer.android.com/reference/android/speech/tts/TextToSpeech) | Speech synthesis, voice/language selection, playback queues, and stop behavior |
| S16 | [Android RecognizerIntent](https://developer.android.com/reference/android/speech/RecognizerIntent) | Recognition configuration; offline preference may be ignored by an engine |
| S17 | [Claude Agent SDK Python reference](https://code.claude.com/docs/en/agent-sdk/python) | `ClaudeSDKClient` connect/query/receive_response/interrupt, `ClaudeAgentOptions` (`cwd`, `resume`, `permission_mode`, `tools`, `allowed_tools`, `setting_sources`, `model`, `cli_path`), message types; checked 10 October 2026 against package 0.2.165 |
| S18 | [Claude Agent SDK sessions](https://code.claude.com/docs/en/agent-sdk/sessions) | Session ID in `ResultMessage.session_id`/init `SystemMessage.data`; `resume` semantics and same-machine session files |

## Assumptions explicitly not made

- A Bluetooth speaker necessarily has a usable microphone.
- A normal mobile browser will remain a dependable always-listening background client.
- Tailscale makes provider inference or provider-retained data local.
- Using Android speech APIs guarantees offline recognition or synthesis on every phone and language.
- A ChatGPT or Claude chat subscription automatically pays for a custom voice API or every agent SDK integration.
- Local CLI sessions automatically inherit existing desktop-app conversations.
- Setting an agent's working directory confines its filesystem access.
- A saved task record guarantees that a worker survives Ubuntu reboot.
- Idempotent task submission provides exactly-once semantics for arbitrary external actions.
- Killing a process reverses actions it already completed.
- An offline or suspended phone will receive a completion alert without an additional delivery mechanism.

## Next design review

Record the M0 device/account inventory while authorized implementation proceeds against the shared contract. Validate Android speech in M1 and local agent capabilities in M3. Follow the independently testable phases in [06-delivery-plan.md](06-delivery-plan.md). Material scope changes must update requirement IDs and the validation matrix; fixture passes do not replace real-device/provider acceptance evidence.
