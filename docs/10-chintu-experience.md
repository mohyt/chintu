# Chintu: voice, audio devices, and computers

10 October 2026 · owner-requested Android redesign · implementation v0.3

## Name and persona

**Chintu** stands for **C**onversational **H**ands-free **I**nterface to **N**etworked **T**erminals & **U**tilities; the name is also an affectionate Hindi word for a small, eager kid. It is a calm, capable computer companion. Its promise is “Your voice. Your computers.” The full form is shown when the wordmark on the home screen is tapped and in the Settings footer. It uses short confirmations, names the selected computer, and never says a connection succeeded before authenticated gateway requests succeed. Android generates speech; there is no OpenAI speech dependency.

A large, gently moving purple orb anchors the near-black home screen, following the owner's visual reference. A short greeting, question, spaced CHINTU wordmark and one speak/stop control keep the screen quiet. A thin HUD-style halo of rotating arcs, level bars, ripples and streaming motes surrounds the orb so voice visibly flows in while listening and out while speaking. The halo follows recognition volume while listening and a synthetic speech rhythm while speaking; it is a procedural animation, not a measured output waveform, and it respects the system reduced-motion setting. A keyboard button opens readable instructions and replies.

## One home screen, controls on demand

- **Home:** greeting, animated orb, short status, small computer selector, and speak/finish/stop control. There are no dashboard cards, forms or bottom tabs.
- **Audio button:** call-style device picker, showing the selected route and connected communication devices. It refreshes automatically while open.
- **Computer selector:** saved computers, aliases, pairing and Tailscale discovery in a bottom sheet.
- **Settings gear:** voice preferences, foreground hands-free, automatic sending, workspace/agent/profile and task history in a bottom sheet.
- **Chat row:** under the computer selector, the current chat's title (tap to open it) and **＋ New chat**. Holding the mic opens the chat too. A final transcript opens it automatically when review is required.

The launcher uses the same purple orb and the name Chintu. No fake computers or fabricated connected states are preloaded.

## Audio and destination are independent

On first use, Android asks for Nearby devices access. Once granted, Chintu detects connected communication headsets automatically and prefers Bluetooth. No headset uses the phone speaker/microphone. The call-style audio button changes this choice; an explicit phone selection is retained while available. If the headset disappears, capture is stopped before phone fallback and the interrupted utterance must be repeated. Automatic fallback does not prevent a reconnected headset from becoming preferred again. Denying Bluetooth access leaves phone audio available.

Request acceptance is different from activation: capture waits for Android to report the requested communication route. Only devices with both a microphone and a speaker are offered, so the picker typically shows "Phone" and the connected headset; media-only Bluetooth speakers are not listed. Android `SpeechRecognizer` still needs a physical microphone-routing test: a communication-device callback cannot independently prove that a particular recognition service uses that microphone.

Changing computers never changes the chosen audio device.

## Connect and speak

Run a Chintu gateway with private Tailscale HTTPS on every execution computer. Using the computer selector, add its URL, a friendly name, optional comma-separated voice aliases, and its enrollment secret. Each host has its own encrypted credential. After the first pairing, the computer sheet automatically asks the paired gateway for its visible Tailscale peers and lists the unpaired ones; online status is discovery evidence, not proof that Chintu is installed or reachable. Pair each additional host separately.

Examples:

- Name **Cobra Ubuntu**, aliases **cobra, ubuntu**: “Chintu, connect me to Cobra Ubuntu.”
- Name **My Mac**, aliases **mac, laptop**: “Chintu, switch to my Mac.”

After authenticated catalogs load, Chintu says: “Cobra Ubuntu is connected. All new instructions will go there now.” The selected host persists. Unavailable, unpaired, unknown, or ambiguous destinations never silently fall back to a different machine. Exact normalized names/aliases are used; no fuzzy matching guesses a target. Unknown commands beginning with Chintu never become executable instructions.

Choose workspace, provider, and permission profile separately for each computer. Switching does not move an existing task. Drafts, credentials, event cursors, pending request bytes/keys, and accepted identities are scoped to the host. An uncertain submission can be retried on its original host after switching back.

## Foreground conversation

Tap the microphone, or explicitly enable **Hands-free while the app is open**. Hands-free uses successive bounded Android recognition turns, pausing for replies, connection operations, editing, and review. It is a foreground convenience, not a background wake-word engine. The app does not capture while speaking. A reply arriving during capture is held until capture ends rather than discarded or spoken over the user.

Final ordinary speech becomes an editable draft. **Send finished voice instructions automatically** is a separate opt-in and resets when the app leaves the foreground. Partial results never submit. Typing stops capture so recognition cannot overwrite an edit. Both automatic sending and hands-free stop when the app closes, locks, or backgrounds; results remain on the computer. A local wake word ("Chintu", on-device Vosk) is available as an opt-in in Settings and works with the screen off; full duplex remains M8. See [the wake-word validation](validation/2026-10-17-wake-word.md).

## Chats and sessions

Voice and typing share one conversation per computer (D22). Each instruction is a turn of the current chat, and the agent resumes the same session, so "and what about the second one?" refers to the earlier answer. The chat sheet reads like a messaging app: your words on the right, replies on the left with live progress ("Reading release notes…") while a turn runs, a **Stop** link on a running turn, tap a reply to hear it, hold it to copy. The composer has its own mic; speaking while the chat is open adds to it, and voice keeps working there because the chat is a conversation surface, not a form.

- **＋ New chat** starts a fresh agent session. Its sheet browses the workspace's folders; the chosen folder is where the agent works and, for `inspect` and `develop`, the only place it can reach. The folder stays fixed for the chat.
- **Chats** lists the computer's conversations, most recent first, with their folder, profile and last line. Opening one also selects its workspace, agent and profile so the next message continues it. Hold a chat to archive it.
- **Tools** lists what the agent can use in this chat: the fixed tool list of `inspect`/`develop`, or for `desktop` the built-in tools, each MCP server with its connection status and tools, and the skills Claude Code reports.
- **By voice:** "Chintu, new chat" (also "new conversation", "new session", "start over") starts a fresh chat in the current folder; "Chintu, new chat in Downloads" picks a folder by its exact name at the top of the workspace or inside the current folder.

Changing the workspace, agent or profile in Settings starts a new chat with the next message, because a session cannot change its authority or folder midway.

## Device acceptance

1. Confirm orb animation during actual recognition/TTS, readable text at larger font sizes, system-bar/keyboard insets, and sheet open/close behavior. Home must remain free of forms/tabs; typing and transcript review belong in the conversation sheet.
2. Use phone audio, grant Nearby devices access, select a real headset, and test its physical mic/output separately. Disconnect/reconnect mid-utterance; verify repeat prompt, phone fallback, and no partial instruction submission.
3. Pair two fixture gateways. Send a distinct instruction to each. Use both example voice commands and check the selected name, correct reply, and unchanged audio route.
4. Test duplicate aliases, unknown names, offline host, missing pairing, and an old gateway without discovery. The prior destination/draft stays intact and manual URL pairing remains usable.
5. Lose a submission acknowledgment, switch hosts, restart the app, and retry on the original host. Verify the original task/key and silent event catch-up. Background just before acceptance; reopening must not resurrect a cleared submitted draft.
6. Deliver a terminal reply while listening, edit a draft during listening, and leave/lock the app during a connection request. Verify no lost queued reply, overwritten edit, stale-host response, or background microphone.

See [validation evidence](validation/2026-10-10-experience-redesign.md) for checks completed versus pending hardware tests.
