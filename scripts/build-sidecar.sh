#!/usr/bin/env bash
# Build udl as a PyInstaller onefile binary named for Tauri externalBin.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
core="$root/core"
outdir="$root/app/src-tauri/binaries"
tmpdir="$root/.temp/pyinstaller"

mkdir -p "$outdir" "$tmpdir"

target="${1:-}"
if [[ -z "$target" ]]; then
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) target="aarch64-apple-darwin" ;;
    Darwin-x86_64) target="x86_64-apple-darwin" ;;
    Linux-x86_64) target="x86_64-unknown-linux-gnu" ;;
    Linux-aarch64) target="aarch64-unknown-linux-gnu" ;;
    MINGW*|MSYS*|CYGWIN*) target="x86_64-pc-windows-msvc" ;;
    *)
      echo "Unknown host; pass target triple explicitly" >&2
      exit 1
      ;;
  esac
fi

export PATH="${HOME}/.local/bin:${PATH}"
cd "$core"
# include optional MCP SDK so packaged `udl mcp` works for AI clients
uv sync --extra dev --extra mcp
uv run pyinstaller \
  --noconfirm \
  --clean \
  --distpath "$tmpdir/dist" \
  --workpath "$tmpdir/build" \
  "$root/packaging/udl.spec"

if [[ "$target" == *windows* ]]; then
  src="$tmpdir/dist/udl.exe"
  dest="$outdir/udl-${target}.exe"
else
  src="$tmpdir/dist/udl"
  dest="$outdir/udl-${target}"
fi

cp -f "$src" "$dest"
chmod +x "$dest" || true
echo "Sidecar ready: $dest"
ls -la "$outdir"
