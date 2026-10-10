# Setup and first device demo

10 October 2026 · execution checklist for M0–M5

## Current verified boundary

Fresh Python packages and local editable builds are verified on macOS. Full OpenAPI validation passes. Android tool archives are downloaded and checked. The hosted [build workflow](../.github/workflows/verify.yml) is configured but has not run; this directory has no Git repository or remote. Local Gradle startup is blocked by the execution sandbox's socket restriction. Phone audio, Ubuntu private transport, and live provider tasks remain unverified.

## 1. Build on an unrestricted development host

From the project root, with Python 3.12, `uv`, JDK 17/21, and package-network access:

```sh
make bootstrap
make PYTHON=.venv/bin/python test demo
python3 apps/android/tools/android.py fetch
python3 apps/android/tools/android.py licenses
python3 apps/android/tools/android.py sdk
make android-build
```

SDK license review is interactive and belongs to the operator; the tooling never accepts terms automatically. Existing SDK/Gradle installations can be used as described in [the Android README](../apps/android/README.md). The debug APK is `apps/android/app/build/outputs/apk/debug/app-debug.apk`. Install with `adb install -r <apk-path>` on the selected phone. A build pass does not prove physical audio behavior.

## 2. Verify speech without a gateway

The owner-reported target is **Google Pixel 9 Pro**. Its installed Android version, language/models, and physical audio routes remain to be checked; see [the target inventory](validation/m0-target-setup.md).

Record phone/Android version, language, recognition/TTS engines, and Bluetooth model. Use the keyboard button to type text and **Read aloud**. Then use **Tap to speak**, **Finish speaking**, review the final transcript, and read it back. The audio button lists connected call-capable devices with automatic phone fallback. Verify denied permission, missing models, internet loss, stop playback, and actual microphone/output routing using the [device acceptance checks](10-chintu-experience.md#device-acceptance). No task should be sent from partial recognition.

## 3. Connect to Ubuntu with the scripted fixture

Run the pinned Python setup on the target Ubuntu host, then:

```sh
.venv/bin/chintu-gateway --database .local/chintu.sqlite3 serve --scripted
```

The service listens only on loopback. Configure Tailscale Serve for private HTTPS to that port on the selected host, and connect the phone to the same tailnet. This step needs the actual host/tailnet; no routing or Tailscale settings were changed in the authoring session.

In a separate Ubuntu terminal, generate the enrollment secret:

```sh
.venv/bin/chintu-gateway --database .local/chintu.sqlite3 enroll --ttl 300
```

Using the computer selector, add the private `https://machine.tailnet.ts.net` URL, friendly name, voice aliases, and secret. Under Settings, use **Test text connection**, then select `fixture`, `Scripted demo`, and `inspect`. In the conversation sheet, submit “Summarize this project,” read its recorded result, and use **Hear reply**. Repeat after disconnect/reconnect and retry a lost acknowledgment; the task ID must stay the same. Label the reply as scripted. Repeat pairing for another host before testing spoken computer switching; see [the experience guide](10-chintu-experience.md).

## 4. Validate one provider before gateway activation

Select the existing supported CLI account and read-only disposable workspace on Ubuntu. Follow [operator validation](../packages/agent-adapters/OPERATOR-VALIDATION.md) to measure a known file read, same-session continuation, cancellation, and fixture integrity. Record the actual execution boundary; installation/login status cannot substitute for those results.

Copy [the disabled configuration](../config/gateway.example.json) into a local configuration and fill its workspace/account/evidence fields after reviewing measured results. Validate it without inference using `chintu-gateway --config <local-config> check-config`. Real execution requires explicit `serve --provider codex` or `serve --provider claude`; neither is selected by default. See [gateway setup](../services/gateway/README.md). Uncertain real execution quarantines the workspace pending local reconciliation.

## 5. Demonstrate the first real voice task

Once speech, private transport, and one provider independently pass, select that provider on the phone, dictate the fixture task, review/send its final transcript, and compare the spoken response with the recorded result. Disconnect after acceptance and retrieve the same task after reconnecting. Stop playback must leave execution running. Follow-up/cancellation UI and other complete controls remain M6 work.
