#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export CARGO_TARGET_DIR="$PWD/.build/rust-target"
if command -v cargo >/dev/null 2>&1; then
    exec cargo "$@"
fi
# Reuse the existing personal Rust installation; do not install another toolchain.
voice_app_tools="$HOME/Documents/Repositories/workspace/.tools"
if [ -x "$voice_app_tools/cargo/bin/cargo" ]; then
    export CARGO_HOME="$voice_app_tools/cargo"
    export RUSTUP_HOME="$voice_app_tools/rustup"
    export PATH="$CARGO_HOME/bin:$PATH"
    exec "$CARGO_HOME/bin/cargo" "$@"
fi
echo 'Rust cargo was not found. Add your installed Rust toolchain to PATH.' >&2
exit 1
