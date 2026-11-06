#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export CARGO_TARGET_DIR="$PWD/.build/rust-target"
if command -v cargo >/dev/null 2>&1; then
    exec cargo "$@"
fi
echo 'Rust cargo was not found. Add your installed Rust toolchain to PATH.' >&2
exit 1
