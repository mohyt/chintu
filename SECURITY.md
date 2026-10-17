# Security policy

Chintu lets a phone run tasks on your own computers through an agent runtime. Its security rests on three things: the tailnet (only your devices can reach the gateway), pairing (a phone needs a QR shown on the computer), and execution profiles (`inspect` and `develop` are confined to a workspace; `desktop` is not). `docs/05-security-and-operations.md` describes the model and its limits.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

- Preferred: GitHub's private reporting, "Report a vulnerability" under the Security tab of this repository.
- Otherwise: email srivastavamohit91@gmail.com with "Chintu security" in the subject.

Include the affected version (the `VERSION` file or the release tag), the component (Android app, gateway, adapters, desktop tray, packaging), steps to reproduce, and what an attacker gains. You will get an acknowledgement within seven days. Fixes ship as a new tagged release; credit is given in the release notes unless you prefer otherwise.

## Scope

In scope: anything that lets a device outside the tailnet reach the gateway, a phone act without pairing, a paired phone read another phone's tasks, the `inspect` or `develop` profiles escape their workspace, the local-proof endpoints answer a browser or another user on the same machine, or secrets and dictated notes leak through logs, packages or the repository.

Out of scope: what the `desktop` profile can do once the owner enables it (it is documented as unconfined), the security of Tailscale, Claude Code or the operating system themselves, and attacks that need the owner's own credentials.

## Supported versions

Only the latest release on the Releases page receives fixes.

## Release signing

The Android APK is signed with a long-lived key held by the maintainer; an update that is not signed with it will not install over an existing Chintu. The macOS app bundle is ad-hoc signed and not notarized, so macOS asks before the first launch; the Debian packages are unsigned. Verify downloads against the checksums GitHub shows on each release asset.
