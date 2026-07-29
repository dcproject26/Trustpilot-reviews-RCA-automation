#!/usr/bin/env bash
# Pull, confirm the running server picked it up, then report state.
#
#   bash tools/redeploy.sh
#
# This does NOT kill the server. An earlier version did, with
# pkill -f server.main, which killed the process Replit's Run button owns -
# Replit saw its child die and reported "Your Start application artifact
# crashed". The log showed a clean shutdown because that is exactly what it
# was: something else had killed it.
#
# With UVICORN_RELOAD=1 in .replit (set alongside this), the dev server picks
# up a pull on its own within a few seconds and no restart is needed. This
# waits for that to happen and says so plainly if it does not.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PORT="${PORT:-5000}"
BASE="http://localhost:${PORT}"
REMOTE="${REMOTE:-$(git remote -v | awk '/github\.com/ && /\(fetch\)/ {print $1; exit}')}"

if [ -z "$REMOTE" ]; then
  echo "No GitHub remote found. Set REMOTE=<name> and re-run."
  git remote -v
  exit 1
fi

echo "==> pulling from $REMOTE"
git pull "$REMOTE" main || { echo "pull failed - resolve and re-run"; exit 1; }
WANT=$(git rev-parse HEAD)
echo "    working tree is ${WANT:0:7}"

running_commit() {
  curl -s --max-time 3 "$BASE/api/version" 2>/dev/null \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('commit',''))" 2>/dev/null
}

echo "==> waiting for the server to pick it up (reload is automatic)"
GOT=""
for _ in $(seq 1 40); do
  GOT=$(running_commit)
  [ "$GOT" = "$WANT" ] && break
  sleep 1
done

if [ -z "$GOT" ]; then
  echo
  echo "    The server is not answering on port $PORT."
  echo "    Press Run in Replit, then run this again."
  exit 1
fi

if [ "$GOT" != "$WANT" ]; then
  echo
  echo "    Still on ${GOT:0:7}, expected ${WANT:0:7} after 40s."
  echo "    UVICORN_RELOAD is probably not set - it lives in .replit and only"
  echo "    takes effect on a fresh Run. Press Stop then Run in Replit."
  exit 1
fi

echo "    running ${GOT:0:7} - matches"
echo
echo "==> which reviews can compute insights"
python3 tools/survey_drafts.py --base "$BASE"
