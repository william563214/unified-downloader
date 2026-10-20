#!/usr/bin/env bash
# Freeze gallery-dl as a standalone executable for mere aggregation bundling.
# GPL-2.0-only — separate program; never import into udl.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
outdir="$root/app/src-tauri/binaries"
tmpdir="$root/.temp/gallery-dl-bundle"
notice_dir="$root/packaging/gallery-dl"

mkdir -p "$outdir" "$tmpdir" "$notice_dir"

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

# pin notice metadata
gdl_version="${GALLERY_DL_VERSION:-1.32.17}"
gdl_commit="${GALLERY_DL_COMMIT:-7f226f74108501c8ac3120548370f68f19e6f9b3}"

cat > "$notice_dir/SOURCE.txt" <<EOF
gallery-dl (bundled as separate program — mere aggregation)
Upstream: https://github.com/mikf/gallery-dl
License: GPL-2.0-only (see LICENSE.gallery-dl)
Version: ${gdl_version}
Source commit: ${gdl_commit}
Source tarball: https://github.com/mikf/gallery-dl/archive/${gdl_commit}.tar.gz
EOF

# fetch upstream LICENSE for redistribution
if [[ ! -f "$notice_dir/LICENSE.gallery-dl" ]]; then
  curl -fsSL \
    "https://raw.githubusercontent.com/mikf/gallery-dl/${gdl_commit}/LICENSE" \
    -o "$notice_dir/LICENSE.gallery-dl" || \
  curl -fsSL \
    "https://raw.githubusercontent.com/mikf/gallery-dl/master/LICENSE" \
    -o "$notice_dir/LICENSE.gallery-dl"
fi

cd "$tmpdir"
uv venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
uv pip install "gallery-dl==${gdl_version}" pyinstaller

entry="$(python -c 'import gallery_dl, os; print(os.path.join(os.path.dirname(gallery_dl.__file__), "__main__.py"))')"
pyinstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name gallery-dl \
  --collect-all gallery_dl \
  --distpath "$tmpdir/dist" \
  --workpath "$tmpdir/build" \
  "$entry"

if [[ "$target" == *windows* ]]; then
  src="$tmpdir/dist/gallery-dl.exe"
  dest="$outdir/gallery-dl-${target}.exe"
else
  src="$tmpdir/dist/gallery-dl"
  dest="$outdir/gallery-dl-${target}"
fi

cp -f "$src" "$dest"
chmod +x "$dest" || true
echo "gallery-dl bundle ready: $dest"
ls -la "$outdir"
