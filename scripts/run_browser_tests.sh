#!/usr/bin/env bash
# Run the browser tests so a hang FAILS instead of wedging the batch.
#
# WHY THIS EXISTS. Playwright's `evaluate` takes no timeout, so an awaited
# promise that never settles stops a whole `pytest <28 files>` run — and every
# file passes on its own, so it reads as a slow machine rather than a bug.
# Two separate causes have been found and fixed by hand, each needing a
# verbose run and a py-spy dump to name.
#
# A per-test timeout inside pytest does NOT work: SIGALRM does not unwind
# Playwright's greenlet while it is blocked on CDP (see tests/conftest.py).
# A per-FILE timeout in a separate process does, because the kernel kills the
# process outright.
#
# Each file gets its own pytest, so a wedged one is killed, NAMED, and the
# rest still run. Exit code is non-zero if anything failed or timed out.
set -uo pipefail

TIMEOUT_S="${BROWSER_FILE_TIMEOUT_S:-300}"
FILES=$(grep -rl "def test_.*(page" tests/ | sort)

failed=()
timedout=()
total=0

for f in $FILES; do
    total=$((total + 1))
    if timeout "$TIMEOUT_S" python3 -m pytest -q "$f" > /tmp/bt.$$ 2>&1; then
        printf '  ok       %s\n' "$(basename "$f")"
    else
        rc=$?
        if [ "$rc" -eq 124 ]; then
            # 124 is timeout(1)'s own code. A file that WEDGED is a different
            # fact from one whose assertions failed, and they must not read
            # the same.
            timedout+=("$f")
            printf '  TIMEOUT  %s (over %ss — a hung evaluate, almost certainly)\n' \
                   "$(basename "$f")" "$TIMEOUT_S"
        else
            failed+=("$f")
            printf '  FAILED   %s\n%s\n' "$(basename "$f")" \
                   "$(grep -E '^(FAILED|E  )' /tmp/bt.$$ | head -5)"
        fi
    fi
    # Playwright leaves these behind when a run is killed, and they pile up
    # across files until the next run is competing with the last one's ghosts.
    pkill -f "uvicorn server.main" >/dev/null 2>&1 || true
done
rm -f /tmp/bt.$$

echo
echo "$total file(s): $(( total - ${#failed[@]} - ${#timedout[@]} )) ok, ${#failed[@]} failed, ${#timedout[@]} timed out"
for f in "${timedout[@]:-}"; do [ -n "$f" ] && echo "  timed out: $f"; done
for f in "${failed[@]:-}";   do [ -n "$f" ] && echo "  failed:    $f"; done
[ ${#failed[@]} -eq 0 ] && [ ${#timedout[@]} -eq 0 ]
