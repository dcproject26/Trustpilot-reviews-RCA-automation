#!/usr/bin/env bash
# Pull, restart, verify the running process matches, then report state.
#
#   bash tools/redeploy.sh
#
# The run command has no --reload, so a pull leaves the server serving whatever
# it imported at startup. That has now produced several rounds of reading
# correct code against output from a build that no longer exists, plus a round
# of running an old diagnostic against an old server. Three manual steps with
# no feedback between them is the wrong shape for something that has to be
# right every time.
#
# This does all of it and refuses to continue if the process does not come back
# on the commit that is checked out.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PORT="${PORT:-5000}"
REMOTE="${REMOTE:-}"
BASE="http://localhost:${PORT}"

# Find the GitHub remote rather than assuming its name - Replit generates one
# per repl ("subrepl-b48r782g"), so "origin" is not a safe guess.
if [ -z "$REMOTE" ]; then
  REMOTE=$(git remote -v | awk '/github\.com/ && /\(fetch\)/ {print $1; exit}')
fi
if [ -z "$REMOTE" ]; then
  echo "No GitHub remote found. Set REMOTE=<name> and re-run."
  git remote -v
  exit 1
fi

echo "==> pulling from $REMOTE"
git pull "$REMOTE" main || { echo "pull failed - resolve and re-run"; exit 1; }
WANT=$(git rev-parse HEAD)
echo "    working tree is ${WANT:0:7}"

echo "==> stopping the server"
pkill -f "server\.main" 2>/dev/null && sleep 2 || echo "    (nothing running)"

echo "==> starting on port $PORT"
mkdir -p .logs
PORT="$PORT" nohup python3 -m server.main > .logs/server.log 2>&1 &
disown 2>/dev/null

echo "==> waiting for it to answer"
for i in $(seq 1 45); do
  GOT=$(curl -s --max-time 3 "$BASE/api/version" 2>/dev/null \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('commit',''))" 2>/dev/null)
  if [ -n "$GOT" ]; then break; fi
  sleep 1
done

if [ -z "${GOT:-}" ]; then
  echo "    server did not answer /api/version in 45s. Last log lines:"
  tail -20 .logs/server.log
  exit 1
fi

if [ "$GOT" != "$WANT" ]; then
  echo "    MISMATCH: running ${GOT:0:7}, expected ${WANT:0:7}"
  echo "    Something else is serving port $PORT, or the start failed. Log:"
  tail -20 .logs/server.log
  exit 1
fi

echo "    running ${GOT:0:7} - matches"
echo
echo "==> which reviews can compute insights"
python3 tools/survey_drafts.py --base "$BASE"
