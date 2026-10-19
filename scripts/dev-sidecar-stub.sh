#!/usr/bin/env bash
# Development stub so `tauri dev` can find binaries/udl-<host-triple>.
# Forwards to `uv run --directory <repo>/core udl …`
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="${HOME}/.local/bin:${PATH}"
exec uv run --directory "$root/core" udl "$@"
