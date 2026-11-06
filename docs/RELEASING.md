# Source release checklist

Maintainer: Kenneth Ezekiel. Recheck this list when dependencies, capture scope,
account transport, packaging or permissions change.

## Before making the repository public

- Review the intended source tree and every reachable commit, branch and tag for
  secrets and private data. Ignore rules do not erase previously committed files.
- Include LICENSE, README.md, PRIVACY.md, SECURITY.md, CONTRIBUTING.md and
  THIRD_PARTY_NOTICES.md, the lockfile, runtime source, tests and required assets.
- Exclude .build, validation, captures, recordings, settings, credentials and
  signing keys. Review Git's exact publication inputs, not the working directory.
- Run the documented checks and build. Confirm setup on a second Mac or clean
  macOS user with its own Codex account. A build in the maintainer's existing
  toolchain does not prove fresh-machine onboarding.
- Re-run realistic reversible tasks with physical speech, a visible correction,
  an in-flight question and Stop. Separate human and synthetic evidence.
- Review the broad screenshot scope and model-mediated approval limitation in
  PRIVACY.md. Do not advertise sandboxing or universal task reliability.
- Verify the signed-in GitHub account and exact repository. Obtain explicit
  approval for the final file set, commit/push and public visibility change.
  Commit approval must name the Markdown files too.

## After approved publication

- Read back the remote tree, license and public visibility. Check CI separately.
- Enable private vulnerability reporting if the repository supports it; verify
  the private reporting entry point before claiming it is available.
- Publish source first. Binary releases require a target-specific dependency
  notice bundle, stable signing, notarization/distribution review and separate
  artifact approval. Never upload a developer's installed bundle or validation logs.
