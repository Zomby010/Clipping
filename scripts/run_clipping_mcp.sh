#!/usr/bin/env bash
# Starts the clipping tools for Claude Code (see .mcp.json) with the project's
# own Python: .venv or venv if there is one, else python3.
cd "$(dirname "$0")/.." || exit 1
for py in .venv/bin/python venv/bin/python; do
  if [ -x "$py" ]; then exec "$py" clipping_mcp.py "$@"; fi
done
exec python3 clipping_mcp.py "$@"
