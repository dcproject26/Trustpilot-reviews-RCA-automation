#!/usr/bin/env python3
"""Which build is a given host running, and is it behind the checkout.

There are two things called "the app": the workspace server on :5000 and the
deployment on the public URL. They are separate processes from separate
snapshots, so `git pull` in the workspace changes one and not the other - and
from a browser they look identical. That is what makes "the dashboard is on the
previous version" unanswerable without asking each one directly.

    python3 tools/which_build.py                          # the workspace
    python3 tools/which_build.py https://your-app.replit.app
    python3 tools/which_build.py http://localhost:5000 https://your-app.replit.app

Reports the commit each host is RUNNING, what is checked out beside it, and
what this working tree is on. A refused connection is reported as refused -
not as an empty answer, which is how `curl -s` made a dead port look like a
broken endpoint.
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT = "http://localhost:5000"


def local_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return "unknown"


def ask(base: str):
    url = base.rstrip("/") + "/api/version"
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} — is this the app, or a router in front of it?"
    except urllib.error.URLError as e:
        return None, f"cannot reach it ({e.reason}) — nothing is listening, or the host is wrong"
    except json.JSONDecodeError:
        return None, "answered, but not with JSON — something else is on this port"
    except Exception as e:
        return None, str(e)[:120]


def main():
    hosts = sys.argv[1:] or [DEFAULT]
    head = local_head()
    print(f"\n  this working tree is on {head}\n")
    bad = 0
    for h in hosts:
        v, err = ask(h)
        if err:
            bad += 1
            print(f"  {h}\n      {err}\n")
            continue
        running, disk = v.get("short", "?"), str(v.get("on_disk", ""))[:7]
        state = ("STALE — the process is behind its own checkout"
                 if v.get("stale") else "current with its checkout")
        print(f"  {h}")
        print(f"      running   {running}   ({v.get('environment', '?')}, "
              f"up {v.get('uptime_s', '?')}s)")
        print(f"      on disk   {disk}")
        print(f"      {state}")
        if head not in ("unknown", "") and running not in ("?", head):
            bad += 1
            print(f"      DIFFERS from this working tree ({head}) — a pull here "
                  f"does not move that host")
        print()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
