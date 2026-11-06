# Third-party notices

The MIT license in this repository covers project-authored source and materials.
It does not relicense dependencies, Apple frameworks or Codex resources.

- GPUI (`gpui-pre` and `gpui-pre-platform` 0.3.9) comes from Zed Industries'
  GPUI snapshot. The cached package manifests declare Apache-2.0 and contain the
  Apache license and Zed copyright notice. The versioned dependency is pinned
  in Cargo.toml and Cargo.lock. Upstream: https://github.com/zed-industries/zed
- The remaining direct Rust dependencies are async-channel, base64, libloading,
  raw-window-handle and serde_json. Their transitive dependencies and platform
  variants remain subject to the licenses accompanying each crate.
- `Assets/` contains project-specific logo/icon artwork developed for this app;
  there are no bundled third-party fonts or icon font packages. UI SVG shapes
  are embedded in src/icons.rs.
- Apple frameworks, on-device speech models and the standalone Codex executable
  and native voice helper are external prerequisites. Do not package those
  executables or models into a source release.

[Declared crate licenses](docs/dependency-licenses.tsv) records the license fields
from the cached manifests for every external package in Cargo.lock, including
platform-only dependencies. This is an inventory, not a set of license grants.
It includes MPL-2.0 crates and compound SPDX expressions; do not blanket-label the
dependency tree as MIT. Refresh it whenever Cargo.lock changes.

`Cargo.lock` is the authoritative version inventory. Before distributing compiled
binaries, generate an inventory for the actual target and include the applicable
license texts, copyright notices and any NOTICE files from linked dependencies.
A source-only release does not include those compiled dependencies. No binary
release is approved by this preparation step.
