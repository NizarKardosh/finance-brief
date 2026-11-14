#!/usr/bin/env bash
# Linux/cloud version of run_daily.ps1: subagent news + market data -> reports/<date>.html
# Usage: bash run_daily.sh [YYYY-MM-DD]     (needs CLAUDE_CODE_OAUTH_TOKEN in the environment)
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
DATE="${1:-$(date +%F)}"
DATA="data/$DATE"
PREV="$DATA/previous-run"
mkdir -p logs "$DATA" reports
LOG="logs/$DATE.log"
log() { echo "[$(date +%T)] $*" | tee -a "$LOG"; }

log "=== Morning Brief $DATE ==="
rm -f "$DATA/status.json"
# Tokens pasted from a wrapped line often contain whitespace
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then export CLAUDE_CODE_OAUTH_TOKEN="$(printf %s "$CLAUDE_CODE_OAUTH_TOKEN" | tr -d '[:space:]')"; fi

# Re-runs on the same day: subagents can't overwrite existing files, so move the previous run aside
OUTPUTS="news_google.json news_yahoo.json news_bloomberg.json pulse.json briefing.json"
moved=0
for f in $OUTPUTS; do
  if [ -f "$DATA/$f" ]; then
    [ $moved -eq 0 ] && { rm -rf "$PREV"; mkdir -p "$PREV"; moved=1; }
    mv "$DATA/$f" "$PREV/"
  fi
done
[ $moved -eq 1 ] && log "Moved previous run outputs to $PREV"

# 1) Orchestrator + subagents (20 min timeout)
PROMPT="$(sed "s/{{DATE}}/$DATE/g" prompts/orchestrator.md)"
TOOLS='Agent,Task,WebFetch,WebSearch,Read,Glob,Grep,Write,Edit,Bash(python fetch_markets.py:*)'
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=1080000   # let background subagents finish (18 min) before -p exits
OUT="$(printf '%s' "$PROMPT" | timeout 1200 claude -p --output-format text --max-turns 80 \
        --settings agent_permissions.json --allowedTools "$TOOLS" 2>&1)"
CODE=$?
printf '%s\n' "$OUT" | grep -v 'Permission \(allow\|deny\) rule' | sed 's/^/claude> /' | tee -a "$LOG"
log "claude exit code $CODE"
if printf '%s' "$OUT" | grep -qiE 'Failed to authenticate|not logged in|Invalid API key|Invalid bearer token'; then
  log 'AUTH ERROR: the Claude token is missing or invalid. Update the CLAUDE_CODE_OAUTH_TOKEN repository secret.'
  echo '{"auth_error": true}' > "$DATA/status.json"
fi

# Restore the earlier run if this one produced no briefing
if [ ! -f "$DATA/briefing.json" ] && [ -f "$PREV/briefing.json" ]; then
  log 'No new briefing - restoring the previous run'
  for f in $OUTPUTS; do [ -f "$PREV/$f" ] && [ ! -f "$DATA/$f" ] && cp "$PREV/$f" "$DATA/"; done
fi
rm -rf "$PREV"

# 2) Market data even if the agent step failed
if [ ! -f "$DATA/markets.json" ]; then
  log 'markets.json missing - fetching directly'
  python fetch_markets.py "$DATE" 2>&1 | tee -a "$LOG"
fi

# 3) Render (also refreshes reports/index.html = latest edition)
python render.py "$DATE" 2>&1 | tee -a "$LOG"
log 'Done.'
