# Target setup inventory

10 October 2026 · M0 · owner-reported details and remaining prerequisites

| Item | Current evidence | Remaining check |
| --- | --- | --- |
| Phone | Owner's terminal reports an authorized ADB device with model `Pixel_9_Pro` | Confirm installed Android version |
| Speech language | Unconfirmed | Select initial recognition/TTS language |
| Speech engines | Owner reports hearing spoken text in the app; basic playback passes | Test recognition, selected voice/language, and playback without internet |
| Audio route | Playback is audible according to the owner; route unconfirmed | Confirm phone speaker/microphone and record Bluetooth device if used |
| Ubuntu host | Unconfirmed | Identify host, OS version, first workspace, and access method |
| Private connection | Unverified | Confirm phone/Ubuntu Tailscale connection and private HTTPS endpoint |
| First provider/account | Unselected | Owner chooses the supported existing CLI sign-in; verify it on Ubuntu |
| Execution boundary | Unverified | Validate read-only disposable fixture before production activation |

The local macOS inventory observed Codex logged in using ChatGPT and Claude without an active sign-in; it does not establish the Ubuntu account setup. The authoring sandbox could not build the APK, as recorded in [build readiness](2026-10-10-build-readiness.md). Subsequent owner reports establish authorized USB detection and audible app playback on the Pixel; build/lint output has not been supplied. Speech recognition and offline behavior remain unverified.

Start physical testing with the Pixel's own microphone and speaker, an editable final transcript, and Android speech playback. Confirm offline support for the selected language rather than inferring it from the phone model. Follow [setup and the first device demo](../09-setup-and-first-demo.md).
