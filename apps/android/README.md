# Chintu for Android

Chintu: **C**onversational **H**ands-free **I**nterface to **N**etworked **T**erminals & **U**tilities.

Chintu is a native voice companion for computers on your Tailscale network. Home is a near-black screen with a purple animated orb, greeting, one speak/stop control and small computer/audio selectors. Typing, transcript review, pairing and preferences open only when needed in sheets. See [the experience guide](../../docs/10-chintu-experience.md).

## Build and install

Use JDK 17/21, Gradle 8.11.1, Android SDK Platform 35, Build Tools 35.0.0 and platform-tools. Minimum Android is 12/API31. AGP is 8.9.2; Kotlin is 2.2.20. No Gradle wrapper is checked in. From the repository root:

```sh
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
export ANDROID_HOME="$PWD/apps/android/.tooling/sdk"
python3 apps/android/tools/android.py fetch
python3 apps/android/tools/android.py licenses
python3 apps/android/tools/android.py sdk
python3 apps/android/tools/android.py build
"$ANDROID_HOME/platform-tools/adb" -d install -r apps/android/app/build/outputs/apk/debug/app-debug.apk
"$ANDROID_HOME/platform-tools/adb" -d shell am start -n ai.chintu/.MainActivity
```

Use your installed JDK path on other hosts. The first three helper commands are only needed to provision tools/SDK; license review is interactive. Existing `ANDROID_HOME`/`ANDROID_SDK_ROOT` and pinned Gradle installations are supported. The helper keeps its caches under ignored `.tooling/`, verifies official Gradle SHA-256 and archived Google tools SHA-1, and never automatically accepts terms. `python3 apps/android/tools/android.py check` shows setup state.

Build defaults are `:app:assembleDebug :app:lintDebug`. Tool compatibility: [AGP](https://developer.android.com/build/releases/agp-8-9-0-release-notes), [Kotlin](https://kotlinlang.org/docs/gradle-configure-project.html).

## First conversation

1. Tap the orb or **Tap to speak**, grant microphone access, speak, and tap **Finish speaking** if needed. Final text opens the review sheet; partial text never sends a task. Long-press the mic to open the conversation sheet for typing or full replies; **Read aloud** there tests Android playback without a gateway.
2. Allow the first-use Nearby devices permission to detect connected Bluetooth devices automatically. Chintu prefers a communication headset, or uses phone speaker/microphone without one. The small audio button opens a call-style route picker that updates live as devices connect/disconnect. A manually chosen phone route stays selected while available. Pair new headsets in Android Bluetooth settings; Chintu does not scan or pair them itself.
3. Tap the small computer selector and **Add a computer**, then **Scan the QR code shown by the computer**: open `http://127.0.0.1:8765/pair` on that machine (or use the Chintu Desktop menu) and the form fills in the URL, name and one-time secret. You can also type the private `https://machine.tailnet.ts.net` URL, friendly name, aliases, and enrollment secret by hand. A saved computer can be removed from its edit sheet; removal revokes the phone on that gateway when reachable and deletes its local credential, choices and pending instruction. Each machine runs a gateway; see [gateway setup](../../services/gateway/README.md). Credentials are encrypted with Android Keystore and bound to their exact canonical host. System TLS is enforced, redirects rejected, and tokens never forwarded between machines.
4. Once one computer is paired, the computer sheet lists the other Linux/macOS/Windows machines visible to that gateway's Tailscale daemon automatically ("Also on your Tailscale network"), refreshed after each connection and when the sheet opens; **Refresh network** forces a reload. The phone itself cannot read the tailnet inventory, so the first computer is always added by URL. Each discovered machine still needs its own gateway and pairing; tap it to open the pairing form prefilled.
5. In Settings, explicitly choose workspace, provider, and permission profile. Use `fixture` / `Scripted demo` / `inspect` for the controlled integration test. Real providers require separate runtime validation.
6. Say “Chintu, connect me to Cobra Ubuntu” or “Chintu, switch to my Mac,” using saved names or aliases. Connection confirmation follows authenticated catalog loading. Subsequent instructions go to that host; prior tasks remain where they started. Ambiguous or unknown names never select a guessed target.

## Conversation and routing behavior

Hands-free is an explicit foreground toggle using bounded Android recognition sessions. It pauses for TTS, editing, review, and connection operations. Automatic sending is on by default and remembered; turn it off in Settings to review each transcript before it is sent. Leaving the foreground disables hands-free. On first connection the app picks the home workspace, the available agent and the most capable permission profile that computer offers (desktop when the operator enabled it, otherwise develop, then inspect); change them in Settings. Dedicated background wake detection and full duplex are not implemented. On-device recognition is preferred; an installed online service is only allowed by the Settings opt-in. TTS selects an installed offline voice for the selected language.

The procedural purple orb reflects actual recognition/TTS state inside a reactive halo: while listening, the halo bars, ripples and inward-streaming motes follow microphone RMS; while speaking, a synthetic speech rhythm drives the halo, an inner pulse and outward-streaming motes; thinking shows a scanner sweep. The speaking movement is a visual animation, not an output audio meter. When Android's "Remove animations" setting is on, decorative motion is dropped and only the level-driven halo remains. Replies arriving during capture are held until recognition ends. The main control becomes **Stop speaking** during playback and controls phone audio only; remote jobs continue. Forms, history and preferences are kept off the home screen.

The audio list shows only devices that carry both a microphone and a speaker: the phone (earpiece folded into the speaker) and call-capable headsets. Media-only outputs such as A2DP speakers or headphones without a microphone are not offered. `setCommunicationDevice` accepting a request is not proof of activation: capture waits for the platform's active route. On disconnect, the current utterance stops before fallback. A communication callback still does not prove the Android recognition service uses the selected physical microphone—verify headset input/output on the Pixel. Media-only speakers may require Android settings and do not offer a headset microphone.

Each computer has independent drafts, credentials, catalog choices, cursor, pending payload/idempotency key, and accepted task identity. Acceptance and clearing a matching submitted draft are persisted together, even if the UI has backgrounded. A newer draft is preserved. Reconnect events are silent; only live terminal events for the selected task can speak. Responses are capped at 1 MiB, with 10-task/20-event pages.

While a task runs, the orb stays in its working state and the status line mirrors the daemon's progress events ("Reading: …", "Running: …", first words of output). If the app leaves the foreground mid-task, a foreground service keeps polling that task's snapshot (not the event cursor) for up to 45 minutes, shows an ongoing "working" notification, posts a high-priority result notification on completion, and the next app open speaks that result once. **Note mode** (✎ on the home screen): Chintu listens in a loop and streams every finished phrase to the daemon as a chunk of one note; the status line shows the running word count. Text is flushed every six seconds from the live transcript, not only at the end of a phrase, and the open note survives an app restart. Tap ✎ again to finish: the daemon writes `YYYY-MM-DD-HHMM-<id>.md` into its configured notes folder (no model involved) and Chintu confirms the word count aloud. Asking "summarize my notes from today" is a normal instruction the agent answers by reading that folder. Notes are never parsed as commands. **Quiet mode** (Settings) mutes all spoken output and simply queues instructions on the daemon; results are read in the **Activity** sheet (☰ on the home screen), which lists final outcomes only, never intermediate progress. The phone keeps the last seven days of outcomes per computer and prunes older entries automatically; **Load older from the computer** pages through the daemon's task history (10 per page) without caching it. The ☰ icon lights up when new results arrived. Follow-up, cancellation, approval, artifact and revocation UI remain later work. The [API contract](../../docs/08-api-contract.md) documents server capabilities.

## Verification

Local JVM checks:

```sh
JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home make test-android-core
```

Device tests use Android's platform instrumentation libraries and isolated files/preferences. They exercise real Keystore, cross-host token isolation, atomic acceptance/draft state, failed storage, and retry persistence:

```sh
python3 apps/android/tools/android.py build :app:assembleDebug :app:lintDebug :app:assembleDebugAndroidTest
"$ANDROID_HOME/platform-tools/adb" -d install -r apps/android/app/build/outputs/apk/debug/app-debug.apk
"$ANDROID_HOME/platform-tools/adb" -d install -r apps/android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
"$ANDROID_HOME/platform-tools/adb" -d shell am instrument -w ai.chintu.test/android.test.InstrumentationTestRunner
```

The authoring environment can compile Kotlin/Java directly against SDK35 but blocks Gradle and ADB listener sockets. Direct compilation is not an APK build, lint pass, or device-test pass. See [current evidence](../../docs/validation/2026-10-10-experience-redesign.md) and [physical acceptance scenarios](../../docs/10-chintu-experience.md#device-acceptance).

Platform references: [Android communication devices](https://developer.android.com/develop/connectivity/bluetooth/ble-audio/audio-manager), [SpeechRecognizer](https://developer.android.com/reference/android/speech/SpeechRecognizer), [TextToSpeech](https://developer.android.com/reference/android/speech/tts/TextToSpeech).
