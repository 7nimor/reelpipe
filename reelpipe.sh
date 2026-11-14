#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export DISPLAY="${DISPLAY:-:0}"
exec "$ROOT/venv/bin/python" "$ROOT/app.py"
