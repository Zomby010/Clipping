#!/usr/bin/env bash
# One-time setup so Claude Code can run the clipping tools, ClipsAI and Postiz.
# Run it in the Clipping folder:   bash scripts/connect_claude.sh
set -e
cd "$(dirname "$0")/.."
PY=python3
for p in .venv/bin/python venv/bin/python; do [ -x "$p" ] && PY="$p" && break; done

echo "1/3  Installing the MCP package for Claude's tools…"
"$PY" -m pip install -q -r requirements-claude-tools.txt

echo "2/3  Checking the tools…"
"$PY" clipping_mcp.py --list

KEY=$(grep -E '^POSTIZ_API_KEY=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"'')
URL=$(grep -E '^POSTIZ_URL=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"'')
if [ -n "$KEY" ] && command -v claude >/dev/null 2>&1; then
  # Postiz's own MCP server (all of Postiz's tools). Saved in your local Claude
  # settings only, never in git. Self-hosted: https://<server>/mcp/<key>
  case "$URL" in
    ""|*api.postiz.com*) MCP="https://mcp.postiz.com/mcp/$KEY" ;;
    *) BASE="${URL%/api/public/v1}"; BASE="${BASE%/public/v1}"; MCP="$BASE/mcp/$KEY" ;;
  esac
  claude mcp remove --scope local postiz >/dev/null 2>&1 || true
  claude mcp add --transport http --scope local postiz "$MCP"
  echo "3/3  Postiz connected to Claude."
else
  echo "3/3  Postiz skipped: put POSTIZ_API_KEY (and POSTIZ_URL if self-hosted) in .env, then run this again."
fi
echo
echo "ClipsAI is optional and large:  $PY -m pip install -r requirements-clipsai.txt"
echo "Done. Start Claude in this folder (claude) and say: \"check the clipping tools status\"."
