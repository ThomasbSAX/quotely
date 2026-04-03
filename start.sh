#!/usr/bin/env bash
# Start the RagCite backend server
ROOT="$(cd "$(dirname "$0")" && pwd)"
unset PYTHONPATH
cd "$ROOT/backend"
exec .venv/bin/python main.py "$@"
