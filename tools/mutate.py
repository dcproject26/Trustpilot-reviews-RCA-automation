#!/usr/bin/env python3
"""Run mutation tests against a COPY of the working tree, never the tree itself.

A mutation run edits source, runs the suite, and restores. If it is killed
between the edit and the restore - a container restart, a timeout, a Ctrl-C -
it leaves a deliberate bug in the working tree. That happened here: a killed
run left `for note in []:` in slack.py, a later subset test run passed with it
in place, and only the full suite caught it before the commit. That was timing,
not process.

So the tree under test is a copy in a temp directory. A killed run can lose
nothing but the copy.

    python3 tools/mutate.py mutations.json
    python3 tools/mutate.py mutations.json -k test_slack

mutations.json is a list of {file, name, find, replace}. `find` must appear
exactly once; an anchor that no longer matches is reported as a SKIP rather
than passing silently, because a mutation that was never applied looks exactly
like one the tests caught.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

SKIP = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"}


def _copy_tree(src: str) -> str:
    dst = tempfile.mkdtemp(prefix="mutate-")
    shutil.copytree(src, dst, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(*SKIP))
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", help="JSON file: [{file, name, find, replace}, ...]")
    ap.add_argument("-k", help="pytest -k expression (default: the whole suite)")
    ap.add_argument("--keep", action="store_true", help="leave the copy on disk")
    ap.add_argument("--fail-fast", action="store_true",
                    help="stop each mutated run at its first failing test. The "
                         "verdict is unchanged — CAUGHT is a non-zero exit "
                         "either way, and a SURVIVOR still runs the whole "
                         "suite because nothing failed to stop it. It only "
                         "stops paying for the rest of a suite whose answer "
                         "is already known.")
    a = ap.parse_args()

    muts = json.loads(open(a.spec, encoding="utf-8").read())
    root = os.path.abspath(os.path.dirname(__file__) + "/..")
    tree = _copy_tree(root)
    print(f"tree under test: {tree}\n")

    cmd = [sys.executable, "-m", "pytest", "-q"] + (["-k", a.k] if a.k else [])
    # The baseline is always a COMPLETE run: -x on a green suite changes
    # nothing, but if it were ever red we want the whole count, not the first
    # failure.
    base = subprocess.run(cmd, cwd=tree, capture_output=True, text=True)
    if base.returncode:
        print("BASELINE IS RED — fix the suite before mutating. Nothing below "
              "would mean anything.")
        print(base.stdout[-2000:])
        return 1
    print(f"baseline: {base.stdout.strip().splitlines()[-1]}\n")
    if a.fail_fast:
        cmd = cmd + ["-x"]

    caught = skipped = survived = 0
    for m in muts:
        path = os.path.join(tree, m["file"])
        try:
            orig = open(path, encoding="utf-8").read()
        except OSError as e:
            print(f"{m['name']:44} SKIP   {e}")
            skipped += 1
            continue
        n = orig.count(m["find"])
        if n != 1:
            # Not "assume it is fine". An unapplied mutation is not evidence
            # of anything, and reporting it as a pass is how a test that
            # guards nothing gets believed.
            print(f"{m['name']:44} SKIP   anchor matched {n} times, expected 1")
            skipped += 1
            continue
        open(path, "w", encoding="utf-8").write(orig.replace(m["find"], m["replace"], 1))
        try:
            r = subprocess.run(cmd, cwd=tree, capture_output=True, text=True)
            tail = (r.stdout.strip().splitlines() or [""])[-1]
            if r.returncode:
                caught += 1
                print(f"{m['name']:44} CAUGHT {tail}")
            else:
                survived += 1
                print(f"{m['name']:44} *** SURVIVED *** {tail}")
        finally:
            open(path, "w", encoding="utf-8").write(orig)

    print(f"\n{caught} caught · {survived} survived · {skipped} skipped")
    if not a.keep:
        shutil.rmtree(tree, ignore_errors=True)
    else:
        print(f"copy kept at {tree}")
    # Survivors and skips both mean the run did not establish what it set out
    # to; neither should read as success to a caller.
    return 1 if (survived or skipped) else 0


if __name__ == "__main__":
    sys.exit(main())
