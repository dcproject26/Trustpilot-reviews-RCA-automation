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
    """The commit this tree is on, or "unknown" — never the empty string.

    `git rev-parse` outside a repository exits 128 with empty stdout, and this
    returned that empty string straight through. Printed, it left the "you are
    on" line blank: a git call that failed looked exactly like a commit with no
    characters in it. Only the returncode tells them apart, so read it.
    """
    try:
        p = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else "unknown"
    except Exception:
        return "unknown"


def _get(url: str):
    """(status, content_type, body) or (None, None, reason)."""
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode(
                "utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode(
            "utf-8", "replace")
    except urllib.error.URLError as e:
        return None, None, f"cannot reach it ({e.reason}) — nothing is listening, "
    except Exception as e:
        return None, None, str(e)[:140]


def ask(base: str):
    """The build a host is running, or a reason that names what actually came back.

    "not JSON" collapsed three very different situations into one sentence: a
    Replit placeholder page while a deployment is mid-publish, an older build of
    our own app that predates this endpoint, and a completely different service
    on the host. /healthz tells the first two apart - it has been in this app far
    longer than /api/version, so answering there and not here means our app, but
    an old one.
    """
    base = base.rstrip("/")
    status, ctype, body = _get(base + "/api/version")
    if status is None:
        return None, body.rstrip(" —") + " or the host is wrong"

    if "json" in (ctype or "").lower():
        try:
            return json.loads(body), None
        except json.JSONDecodeError:
            return None, f"HTTP {status} claimed JSON and sent something else"

    hstatus, hctype, hbody = _get(base + "/healthz")
    healthy = hstatus == 200 and "json" in (hctype or "").lower() and '"ok"' in hbody
    snippet = " ".join(body.split())[:70]

    if healthy:
        return None, (f"this IS the app, but an OLDER build — /healthz answers and "
                      f"/api/version does not (HTTP {status}). Nothing new has been "
                      f"published to this host.")
    if status == 404:
        return None, (f"HTTP 404 for both /api/version and /healthz — this host is "
                      f"not running the app at all")
    return None, (f"HTTP {status}, {ctype or 'no content-type'} — not the app. "
                  f"Probably a platform page while the deployment is not live. "
                  f"First bytes: {snippet!r}")


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
