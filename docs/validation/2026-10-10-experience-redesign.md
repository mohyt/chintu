# Chintu experience redesign verification

10 October 2026 · Android v0.2.0 · code checks separate from physical acceptance

## Scope

Owner-requested animated Chintu persona, Bluetooth communication-device selection, phone audio fallback, multiple paired execution computers, Tailscale discovery, and spoken destination switching. See [experience and acceptance scenarios](../10-chintu-experience.md).

## Completed checks

- Full OpenAPI 3.1 validation: 29 schemas, five wire examples and five invalid-payload checks.
- Python: **87 tests passed** (41 gateway including 10 discovery tests, 40 adapter/readiness tests, six wire integration tests). Scripted in-process task demo passed. No real provider inference or live Tailscale probe was launched.
- Android core JVM tests passed for durable retry/restart, accepted identity and event replay. Voice parsing/matching passed **101 assertions**, including punctuation/case, unique aliases, ambiguous names, unknown Chintu commands, and ordinary tasks.
- Android main Java/Kotlin and **10 instrumentation test methods compiled** directly against the installed SDK35 using Kotlin2.2.20/JDK21. Deprecation warnings only. Compilation does not run Android APIs or device tests.
- Android resources and manifest compiled/linked with SDK35 AAPT2, including the launcher face and dark theme. This checks resource validity; it is not a complete APK or visual screenshot review.
- Independent code review addressed route confirmation before capture, route identity changes, stale destination/lifecycle callbacks, queued replies during capture, typed-draft protection, and acceptance when the UI is backgrounded. Accepted identity, pending removal and clearing a matching draft share an atomic file replacement; newer edits survive.

Reproduction for Python/JVM checks:

```sh
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home make PYTHON=.venv/bin/python test demo
```

## Environment and pending evidence

The regular Gradle build (`assembleDebug`, `lintDebug`, `assembleDebugAndroidTest`) cannot start its local daemon listener in the authoring sandbox: `java.net.SocketException: Operation not permitted`. ADB likewise cannot start its listener here. Direct source compilation was used as an additional check; **no redesigned APK, lint pass, screenshot review, or device instrumentation pass is claimed**. Build/install and device-test commands are in [the Android README](../../apps/android/README.md).

The owner previously showed an authorized Pixel9Pro ADB connection and reported audible playback in the earlier prototype. That does not validate this redesign's microphone route, headset switching/fallback, offline languages, foreground hands-free behavior, animation/layout, or two-host routing. These require the real Pixel, headset and independently paired gateways. Mac real-provider execution still needs host-specific runtime evidence; listing a Mac in Tailscale does not establish it.

No Git metadata is present, so a Git diff could not be inspected. Existing AGENTS.md was left unchanged. Credentials, raw audio and private Tailscale peer inventories are not included in this record.

## Minimal home revision (v0.3.0)

The owner supplied a near-black/purple-orb reference and requested a much simpler app plus automatic Bluetooth detection and a call-style audio button. Home now contains only greeting/status, animated orb, compact computer selector and voice/keyboard/audio controls. Conversation review and settings use bottom sheets; tabs, cards and forms are absent from home. The first-use Nearby devices prompt enables automatic routing; the audio picker tracks live device changes and supports manual choice.

Focused checks for this UI revision: Android main/instrumentation source compilation passed; new purple theme/orb icon and manifest passed AAPT2 compile/link; existing Android core tests and 101 voice-command assertions passed. No Python behavior changed, so the preceding Python result is retained without rerunning it. APK/lint, visual inspection on the Pixel, sheet keyboard/insets, permission denial, live picker updates and actual microphone routing still require device validation.
