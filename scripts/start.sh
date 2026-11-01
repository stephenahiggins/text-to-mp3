#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
[[ -x .venv/bin/python && -d frontend/dist ]] || { echo 'Run ./scripts/setup.sh first.'; exit 1; }
# No dependency resolution or network calls are required at launch.
(sleep 2; open http://127.0.0.1:8765) &
exec .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
