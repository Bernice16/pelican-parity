#!/usr/bin/env bash
# Offline test suite: fake claude + fake renderer, no model calls, $0.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3
if ! "$PY" -c "import sys" >/dev/null 2>&1; then PY=python; fi
exec "$PY" tests/run_offline.py "$@"
