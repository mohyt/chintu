# Product requirements

Design v0.3 · 10 October 2026 · Android redesign implemented; device acceptance pending

## Outcome

Chintu lets its owner talk through a phone-connected Bluetooth device, assign useful work to their Ubuntu computer, and receive spoken progress and results. The owner can choose Claude or GPT, continue a task, answer a question, and cancel work without sitting at the computer.

The product has one owner and one enrolled Android phone. The owner has now requested multiple named execution computers on Tailscale, including Ubuntu and Mac, with a persistent selection for subsequent instructions. Each computer needs its own gateway, pairing, and validated runtime; multi-user sharing remains out of scope.

## What is confirmed and what is proposed

| Item | Status | Consequence |
| --- | --- | --- |
| Ubuntu desktop | Confirmed | Target local agent runners and Linux process supervision |
| Phone-connected Bluetooth audio | Confirmed | Audio input route must be established from the actual device |
| Selectable audio devices with phone fallback | Confirmed | Prefer a compatible connected headset; use phone speaker/microphone without one |
| Multiple named computers and spoken destination switching | Confirmed | Saved per-host pairing; confirm reachability before switching; audio selection is independent |
| Animated Chintu persona while the app is open | Confirmed | Clearly distinguish listening, thinking, and speaking, with readable text |
| Tailscale connectivity | Confirmed | Expose the application only within the tailnet |
| Claude and GPT execution | Confirmed | Maintain separate adapters and task sessions |
| Design before coding | Confirmed | Design and the text-only API baseline precede parallel implementation |
| Project home `~/dev/continuum/chintu` | Confirmed | Keep project-owned deliverables together |
| Android native companion | Confirmed design direction | Handles speech recognition, speech playback, and task controls |
| Desktop agent runtimes rather than existing chat handoff | Proposed | Requires agreement on the interpretation of “Claude Desktop or GPT itself” |
| Push-to-talk before wake word | Proposed sequence | Establish a reliable audio and task path first |
| Android speech recognition and text-to-speech | Confirmed design direction | Exchange text and task data with Ubuntu; prefer supported on-device speech engines |
| OpenAI speech APIs | Excluded | No OpenAI transcription, text-to-speech, or Realtime dependency |
| English first | Proposed test baseline | Hindi/Hinglish support can be added to the acceptance corpus if wanted |

## User journeys

### Assign work while away

The user opens a listening session and says, “Ask Claude to explain the authentication module in my project.” Chintu resolves the registered project, submits a task, records it, and confirms the chosen worker. The user can end the conversation while the desktop keeps working. The result appears when the phone is next connected; live spoken delivery requires an active client connection and playback permission.

### Continue a specific task

The user asks, “What did the test investigation find?” The gateway maps the request to a visible task. If more than one task matches, it asks which one. A follow-up reaches the same agent session. “Use GPT for the next task” does not migrate the active Claude session.

### Respond to an agent question

The agent needs a product choice or an additional permission. Chintu distinguishes an ordinary clarification from an approval request. The user sees the task and relevant context. A permission decision is applied only to the identified request and permitted scope.

### Recover from connectivity loss

A cellular handover disconnects the phone. The desktop continues accepted jobs. After reconnection, the phone requests events after its last received event ID and displays the current state. If the original submit response was lost, retrying with the same idempotency key returns the original task.

### Interrupt or cancel

“Stop speaking” stops playback. “Cancel the test investigation” requests task cancellation. Cancellation reaches a confirmed terminal outcome, or Chintu reports that execution is still stopping or needs reconciliation. It does not imply completed external actions were undone.

## Functional requirements

| ID | Requirement | Release | Acceptance evidence |
| --- | --- | --- | --- |
| FR-01 | Convert a deliberate voice turn into text on Android | V1 | Recognized text is visible and reaches Ubuntu as a text instruction; speech-engine online/offline behavior is recorded |
| FR-02 | Play replies through the selected Bluetooth device | V1 | Playback verified on the user's actual device |
| FR-03 | Fall back to the phone microphone when no compatible Bluetooth input exists | V1 | App displays the actual active input route |
| FR-04 | Reach Ubuntu through Tailscale and authenticate the application client | V1 | Authorized phone connects; revoked client is rejected |
| FR-05 | Select a registered project and Claude or GPT for a task | V1 | Stored task records the project and immutable provider choice |
| FR-06 | Execute useful local file/code tasks with both providers | V1 | Each adapter completes a bounded real task on Ubuntu |
| FR-07 | Return task progress, output references, and a spoken final summary | V1 | UI and speech agree with durable task state |
| FR-08 | Preserve task and provider-session identity across phone reconnection | V1 | Same task survives network interruption without a second run |
| FR-09 | Continue, clarify, or cancel an existing task | V1 | Correct provider session receives the message or interrupt |
| FR-10 | Preserve required runtime approval requests and enforce their decisions | V1 | Allow, deny, expire, and revoke cases are verified per adapter |
| FR-11 | Distinguish voice interruption from execution cancellation | V1 | Both behaviors tested independently |
| FR-12 | Report unavailable desktop, provider, or audio routes accurately | V1 | Fault scenarios produce actionable states rather than success claims |
| FR-13 | Detect a local wake word during a user-enabled listening session | V2 | Measured false wakes, misses, and battery use on the target phone |
| FR-14 | Support full-duplex speech with echo control and barge-in | V2 | Device-specific acoustic tests pass |
| FR-15 | Review past tasks and deliberately resume supported agent sessions | V1 | Task history persists across application restart |
| FR-16 | List and select connected communication audio devices independently of the execution computer | V1 | Real headset switching, active-route confirmation and phone fallback pass on Pixel |
| FR-17 | Discover visible Tailscale computers and save per-host pairing and voice aliases | V1 | Discovery is authenticated; each host has isolated credentials, cursor, draft and pending request |
| FR-18 | Handle “Chintu, connect me to NAME” and “Chintu, switch to NAME” locally | V1 | Exact unique name/alias resolves; successful authenticated connection precedes spoken confirmation; failed/ambiguous switches preserve destination |
| FR-19 | Present Chintu as an animated conversational companion | V1 | Orb follows actual recognition/TTS lifecycle, text stays readable, and open-app hands-free pauses for playback/review and stops on background |
| FR-20 | Keep voice and chat turns in one conversation that resumes the same agent session until the owner starts a new one | V1 | Second turn resumes the first turn's provider session; “New chat” or “Chintu, new chat” starts a fresh session; voice and typed turns land in the same thread |
| FR-21 | Offer a chat view of conversations on each computer: list, open, continue, stop a running turn, archive | V1 | Chats list ordered by last activity; thread streams every tool step and the full reply text while a turn runs and keeps them afterwards; archived chats leave the list and refuse new turns |
| FR-22 | Choose a working directory inside a registered workspace for each conversation, by touch or by voice | V1 | Folder picker and “Chintu, new chat in FOLDER” resolve only folders inside the workspace; the agent runs and is confined there; the folder stays fixed for the conversation |
| FR-23 | Show which tools, MCP servers and skills the agent can use in the current conversation | V1 | Confined profiles list their fixed tools; `desktop` reports live MCP server status, their tools and skills, or says why only a fallback is shown |

## Quality requirements

| ID | Proposed target or rule |
| --- | --- |
| NFR-01 | No intentional public ingress; use a private tailnet endpoint |
| NFR-02 | Mobile submission retries must not create duplicate task records or silently rerun a started task |
| NFR-03 | Persist accepted work before acknowledging it; do not promise recovery of actions whose outcome is unknown |
| NFR-04 | Acknowledge a task within 1 second at p95 after the gateway receives a valid submission, under a healthy single-user test load |
| NFR-05 | Target first spoken acknowledgment within 2.5 seconds at p95 after the end of a push-to-talk utterance on the agreed test network; measure all latency segments |
| NFR-06 | Recover current task state within 5 seconds at p95 after an authenticated connection is re-established |
| NFR-07 | Provide bounded queue depth, output size, run duration, and provider spending controls |
| NFR-08 | Retain no raw audio by default; make history and diagnostic retention explicit |
| NFR-09 | A provider adapter must decline unsupported permissions or continuation features instead of guessing |
| NFR-10 | Declare whether a task is read-only or can modify its workspace; enforce that through the runner's actual controls |

Latency targets are design goals, not current measurements or vendor guarantees. Provider inference, Bluetooth buffering, mobile networks, and Tailscale relay paths can materially affect them. Wake-word battery targets will be set after measuring the phone's baseline.

V1 sends no microphone audio between the phone and Ubuntu. Android speech services may themselves use a network connection unless a supported on-device recognizer and offline TTS voice are selected. Agent model inference still uses the chosen provider's connection.

## Initial scope boundaries

V1 includes local repository investigation, file summarization, and explicitly scoped development work. Browser work is available only after a supported browser tool and its profile are configured and tested. Controlling arbitrary Ubuntu GUI applications is a separate capability with its own screen-session and OS-permission requirements.

Native desktop chat import, arbitrary mouse/keyboard automation, purchases, financial trading, telephony, multiple people, and unattended privileged system administration are outside V1. Their omission does not prevent adding new tools behind the same task contract later.

## Open product decisions

The decision register in [07-decisions-and-sources.md](07-decisions-and-sources.md) is authoritative. The immediate questions are the actual audio device, intended listening mode, exact desktop-app handoff requirement, and subscription/API preference. None has been silently accepted on the user's behalf.
