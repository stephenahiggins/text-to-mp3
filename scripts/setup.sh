#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
export UV_CACHE_DIR="$PWD/.data/uv-cache"
if ! command -v uv >/dev/null && [[ ! -x .tools/bin/uv ]]; then
  python3 -m pip install --target .tools uv
fi
if command -v uv >/dev/null; then UV=uv; else UV="$PWD/.tools/bin/uv"; fi
command -v ffmpeg >/dev/null || { echo 'Install FFmpeg first: brew install ffmpeg'; exit 1; }
command -v npm >/dev/null || { echo 'Install Node.js first: brew install node'; exit 1; }
"$UV" sync --frozen
.venv/bin/python scripts/download_models.py
(cd frontend && npm ci --cache ../.data/npm-cache && npm run build)
echo 'Ready. Start with ./scripts/start.sh'
