# Contributing

Keep changes focused on one observable behavior. Include a reproduction and what
you verified. Preserve local Stop, fresh-observation checks and the separation
between mute and stopping work. Do not introduce an API-key fallback.

## Checks

Fetch locked dependencies once as described in the README. Then run:

```sh
python3 -m unittest discover -s Tests -p test_bridge.py
sh scripts/cargo.sh fmt --check
sh scripts/cargo.sh clippy --offline --locked --all-targets -- -D warnings
sh scripts/build.sh
sh scripts/cargo.sh test --offline --locked
sh scripts/test-swift.sh
```

Swift checks require Apple Silicon macOS and a macOS 26+ SDK. They use synthetic
inputs and do not authorize physical GUI tasks. CI runs Python, Rust, Swift checks and an unsigned build;
signed installation and physical speech still need local verification.

`Tests/live_*` are opt-in probes: they can contact Codex using your authenticated
account and consume account usage. Read each probe before running it, confirm
that account, and use only disposable fixtures. Do not run them as ordinary tests
or add them to unattended CI. `local_speech_probe.swift` is an older experimental
harness; production replay is `LocalSpeechReplayTests.swift`. The Silero VAD
experiment additionally needs separately supplied numpy, onnxruntime and a
hash-checked model; none are runtime dependencies or bundled downloads.

For performance reports distinguish microphone-to-caption, end-of-speech-to-submit,
model time and GUI time. Synthetic tests do not establish human speech reliability.

Never commit credentials, recordings, screenshots, raw session JSONL, personal
settings, signing keys, build caches or private transcripts. Public bug reports
should contain a minimal redacted reproduction and version information. Review
both the current diff and reachable Git history before publication.

Contributions are under the project's MIT license. Preserve third-party notices;
do not copy assets or source with unknown redistribution rights.
