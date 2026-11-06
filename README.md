# Voice App

A small right-side voice assistant that observes your desktop, performs GUI actions,
and lets you interrupt or correct it. Built with Rust/GPUI, Swift and a Python bridge.

**Experimental developer preview.** Use disposable data and reversible tasks.
Human speech reliability and end-to-end latency remain under active development.
This is an independent project, not an OpenAI or Zed product.

## How it works

```text
Microphone → Apple on-device speech → captions → Codex desktop worker
                                                  ↓
Right sidebar ← results + fresh observation ← local action gate → macOS GUI
      Stop ────────────────────────────────────────────┘
```

- Expanded sidebar, compact rail and hidden drawer; transient caption bubbles.
- One serial desktop worker; fresh screenshot and accessibility readback after actions.
- Stop closes the local action gate independently of the model, invalidates queued
  work and requests cancellation. Already dispatched OS events can still take effect.
- Mute controls hearing; it does not stop desktop work. Unmute preserves the worker session.
- Corrections pause dispatch, settle in-flight work and re-observe before replanning.
- Screenshots are requested on demand, not continuously streamed to native voice.
- Accessibility Press avoids moving your pointer when supported. Other clicks and
  scrolling can share the real pointer; there is no independent virtual OS cursor.

## Requirements

- Apple Silicon Mac. Windows and Linux desktop control are not implemented.
- macOS 26 or later for the default Apple SpeechTranscriber path, with installed
  English (`en_US`) speech assets. Current hardware validation used macOS 27.0.1.
  The UI bundle targets macOS 14, but this does not make local speech work on 14.
- Xcode command-line tools with a macOS 26+ SDK, Rust supporting edition 2024,
  and `/usr/bin/python3` (tested with Python 3.9.6; runtime uses the standard library).
- A separately installed **standalone Codex package**, a ChatGPT login, and access
  to `gpt-6-luna`. Last tested with Codex **0.165.0**. The bridge checks package
  layout version 1 and version >=0.159.0; newer versions are not guaranteed compatible.
  It currently requires `codex-package.json` and
  `codex-resources/voice/bin/codex-voice-host` beside the standalone installation,
  including when Apple speech is selected. A bare binary/npm layout may not work.
- Microphone, Screen Recording and Accessibility permissions for the installed app.

See the [official Codex installation instructions](https://github.com/openai/codex#quickstart).
Authenticate using your own ChatGPT account; Voice App shows and checks the account.
Compatibility failures are explicit. There is no API-key or separately billed voice fallback.
Codex service availability, account access and usage limits still apply.

## Build and run

```sh
# In a fresh clone. Fetch the locked Rust dependencies explicitly once.
sh scripts/cargo.sh fetch --locked
sh scripts/build.sh
```

The build is offline after fetching dependencies and produces an **unsigned** bundle
at `.build/package/Voice App.app`. It does not change your installed app, credentials,
or permissions. Do not use the unsigned bundle as a stable daily installation.

For a stable local install, create a Code Signing certificate in Keychain Access
(Certificate Assistant → Create a Certificate → Code Signing), or use an existing
local development certificate. Keep its identity across updates to avoid changing
macOS permission identity. Do not export or commit its private key.

Quit Voice App, then run:

```sh
VOICE_APP_SIGNING_IDENTITY='Your Code Signing certificate name' sh scripts/build.sh --install
open '.build/Voice App.app'
```

The default certificate name is `Voice App Local Development`. Installation stays
inside `.build`; the script never creates certificates or resets permissions.
This is a local developer build, not a notarized distribution.

**Opening the app automatically starts a session**, including microphone and desktop
sharing once permissions allow it. Clear sensitive windows first. Stop session
turns them off. Start with an atomic command such as opening one disposable tab.

## Settings and debugging

Settings live in `~/Library/Application Support/Voice App/settings.json`.
Idle reset defaults to five minutes and can be adjusted in the interface.
Apple speech is the default. To explicitly test the older native subscription
voice path, stop/quit, set `"speechBackend": "native"` in that JSON object, then
reopen. Native voice additionally requires account/version V3 voice support.

Local timing diagnostics are written under `validation/` beside the app bundle.
See [privacy and data handling](PRIVACY.md) before sharing evidence. Do not attach
raw Codex session files, screen captures or private transcripts to public issues.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for checks and opt-in live probes,
[SECURITY.md](SECURITY.md) for security reporting, and
[docs/RELEASING.md](docs/RELEASING.md) for the public-release gates.

## License

Project source is [MIT licensed](LICENSE). Dependencies keep their own licenses;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Codex, Apple frameworks and
speech assets are separately installed and are not included or relicensed here.
