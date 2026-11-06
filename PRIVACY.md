# Privacy and desktop authority

## What leaves the Mac

The default speech path runs Apple speech recognition on device. Voice App does
not save microphone recordings or send microphone audio through a separate STT API.
Recognized requests, conversation context, on-demand display screenshots,
accessibility labels and action results are sent to the separately installed Codex
runtime, which connects to OpenAI under your signed-in ChatGPT account.
The explicit native speech mode also sends audio through Codex's native voice transport.
OpenAI's account and service policies govern that processing; local recognition
is not an offline assistant.

Captures cover the selected display, including other visible apps. Secure AX fields
are excluded from accessibility text, but screenshots are **not pixel-redacted**.
Never expose passwords, customer data, confidential work, or other people's private
content. Use a clean desktop or disposable macOS user for experiments.

## Local storage

- Settings: `~/Library/Application Support/Voice App/settings.json`.
- Bridge diagnostics: `validation/voice-*.jsonl` beside the app; closed-schema
  timing, event names, counters, coordinates and status flags, not transcript text,
  audio, screenshots, account email or arbitrary protocol payloads. Per-file size capped at 2 MiB.
- Native gate diagnostics: `validation/session-*.jsonl` beside the app; dispatch,
  Stop, epoch/revision, timing and fixed rejection messages. These files are not
  automatically rotated or capped. Treat them as private diagnostics.
- Codex may separately retain conversation/session data in its own storage. Voice
  App's diagnostic exclusions do not control Codex retention.

There is no automatic diagnostic uploader. Quit before manually deleting local
logs. Deleting Voice App diagnostics does not delete Codex-side records.

## Controls and limits

Opening the app starts a session. Mute stops listening but desktop work continues.
Stop closes the local action gate before requesting model cancellation. The OS can
finish applying an already dispatched event. Stop cannot undo completed work.

The worker has a bounded GUI tool set, not arbitrary shell/file/API tools. Screen
content is treated as untrusted in its instructions. It is told to ask before
third-party submissions, purchases, deletion, credential changes or publishing.
**That approval rule is model-mediated; there is no deterministic local approval
binding for every destination or payload.** Do not use this preview for sensitive
or irreversible workflows or leave it operating unattended.

The desktop helper is not a security sandbox. Screen Recording and Accessibility
are broad macOS permissions. A compromised process running as your user remains
outside the protection offered by the local action gate.
