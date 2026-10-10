#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "No virtual environment found. Set it up once with:" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi
exec .venv/bin/python questlog.py "$@"
