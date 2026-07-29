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
#
# The reloader watches *.py and nothing else. A pull carrying only client/ or
# docs therefore never restarts the process and never needs to, so this must
# not report one as a failure - it did, for every frontend-only pull, and the
# fix it advised (Stop then Run) was answering a problem that did not exist.

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

# The server's own view of the code it LOADED. Asking it for "commit" and
# comparing to git rev-parse here is what hid the problem for hours: the
# endpoint was reading the same .git the shell was, so it always agreed.
version_json() {
  curl -s --max-time 3 "$BASE/api/version" 2>/dev/null
}

# "commit" is the sha frozen at import; "on_disk" is what is checked out now,
# and "stale" is just those two disagreeing. Take "commit" straight through
# rather than blanking it whenever "stale" is set: blanking made a stale
# process indistinguishable from a dead one, so the only branch that could
# then fire told the user the port was down while the server was up and
# answering on it.
field() {   # field <json> <key>; booleans come back as python True/False
  printf '%s' "$1" \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('$2',''))" \
      2>/dev/null
}

# The *.py the running process has not loaded. Diff from the commit it
# actually LOADED, not from the pre-pull HEAD: a process that already sat out
# an earlier frontend-only pull is behind by more than this pull alone, and
# the narrower range calls a real Python change cosmetic and sends the user
# away happy with old code serving.
py_changed_since() {
  git cat-file -e "${1}^{commit}" 2>/dev/null || { echo "unknown"; return; }
  git diff --name-only "$1" "$WANT" -- '*.py'
}

echo "==> waiting for the server to pick it up (reload is automatic)"
JSON=""
GOT=""
for _ in $(seq 1 40); do
  JSON=$(version_json)
  GOT=$(field "$JSON" commit)
  [ "$GOT" = "$WANT" ] && break
  # Do not burn the other 39s on a restart that cannot happen: with no *.py
  # in the gap the reloader has nothing to notice, now or ever.
  [ -n "$GOT" ] && [ -z "$(py_changed_since "$GOT")" ] && break
  sleep 1
done

if [ -z "$GOT" ]; then
  echo
  echo "    The server is not answering on port $PORT."
  echo "    Press Run in Replit, then run this again."
  exit 1
fi

if [ "$GOT" != "$WANT" ]; then
  PY=$(py_changed_since "$GOT")
  if [ -z "$PY" ]; then
    echo
    echo "    Still on ${GOT:0:7}, and that is correct: this pull changed no"
    echo "    .py file, so the reloader had nothing to act on. Nothing needs"
    echo "    restarting - server/main.py reads client/index.html off disk on"
    echo "    every request and sends Cache-Control: no-store, so the new"
    echo "    frontend is already being served. If the page still looks old,"
    echo "    that is the browser's own copy: hard-reload the tab."
  else
    echo
    echo "    Still on ${GOT:0:7}, expected ${WANT:0:7} after 40s."
    if [ "$PY" = "unknown" ]; then
      echo "    ${GOT:0:7} is not a commit in this clone, so whether it missed"
      echo "    a Python change cannot be checked from here."
    else
      echo "    Python this process has not loaded:"
      while IFS= read -r f; do echo "      $f"; done <<< "$PY"
    fi
    # Ask the process rather than guessing. Blaming UVICORN_RELOAD outright
    # was wrong every time the reloader was running and the module had failed
    # to import - the traceback is in the log and Stop/Run only reruns it.
    if [ "$(field "$JSON" reload)" = "True" ]; then
      echo "    UVICORN_RELOAD is set in this process, so the reloader is"
      echo "    there and did not act - the reload most likely raised on"
      echo "    import. Read the server log for the traceback."
    else
      echo "    UVICORN_RELOAD is not set in this process - it lives in"
      echo "    .replit and only takes effect on a fresh Run. Press Stop then"
      echo "    Run in Replit."
    fi
    exit 1
  fi
else
  echo "    running ${GOT:0:7} - matches"
fi
echo
echo "==> which reviews can compute insights"
python3 tools/survey_drafts.py --base "$BASE"
