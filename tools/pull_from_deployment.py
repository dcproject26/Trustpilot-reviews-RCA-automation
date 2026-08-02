#!/usr/bin/env python3
"""Copy the deployment's reviews into THIS database, over its own HTTP API.

    python3 tools/pull_from_deployment.py --url https://your-app.replit.app
    python3 tools/pull_from_deployment.py --url https://your-app.replit.app --apply

No database credentials needed, and that is the whole point. The deployment is
on a Neon instance whose connection string lives in the deployment's own secret
store — visible to the deployment and to nobody else, including this shell. But
the deployment already serves its reviews over HTTP, so the data can come
across without anyone hunting for a password.

It writes into whatever DATABASE_URL this shell has, and only rows whose id is
not already there. Nothing existing is touched, and running it twice copies
nothing the second time.

WHAT DOES NOT COME ACROSS, and this matters more than what does: the API
serves what the dashboard renders, not every column of the row. Anything the
dashboard never shows is not in the payload and cannot be recovered this way —
it is listed at the end of the run rather than left for you to discover. If
you need a byte-exact copy, that needs the Neon connection string and
tools/migrate_db.py.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _get(url, timeout=30):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"


# Which keys to copy is DERIVED from the payload and the model, not listed by
# hand. The hand-written list was wrong on its first outing — it named 33
# columns as "not in the payload" when 30 of them were, because I had simply
# not typed them out. A map that has to be maintained in step with two other
# files rots silently and reports the rot as a property of the data.
def _copyable(payload_keys, model):
    cols = {c.name for c in model.__table__.columns}
    return sorted(payload_keys & cols)


def _coerce(model, name, value):
    """JSON has no datetime, so every timestamp arrives as an ISO string and
    the typed column rejects it. Coerce by the column's own type rather than by
    guessing from the field name — a name-based rule breaks the day someone
    adds a column called something else."""
    from datetime import datetime, date
    import sqlalchemy as sa
    col = model.__table__.columns.get(name)
    if col is None or value is None:
        return value
    if isinstance(col.type, (sa.DateTime, sa.Date)) and isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None      # unparseable is not a timestamp; do not invent one
        # A tz-aware value into a naive column raises on some backends.
        if dt.tzinfo is not None and getattr(col.type, "timezone", False) is False:
            dt = dt.replace(tzinfo=None)
        return dt.date() if isinstance(col.type, sa.Date) and not isinstance(
            col.type, sa.DateTime) else dt
    return value


def _absent(payload_keys, model, skip=()):
    cols = {c.name for c in model.__table__.columns}
    return sorted(cols - payload_keys - set(skip))


def _looks_like_a_placeholder(u: str) -> bool:
    """A url with no scheme, or one that is plainly a stand-in.

    Handing someone a command with PASTE_THE_URL in it and expecting them to
    substitute it is a design fault, not a user error — it failed four times
    before this check existed. Catch it and say where the real one is.
    """
    u = (u or "").strip()
    return (not u.startswith(("http://", "https://"))
            or any(t in u.upper() for t in
                   ("PASTE", "YOUR-", "YOUR_", "<", ">", "EXAMPLE.COM")))


def _discover():
    """Deployment urls this shell can guess, most likely first.

    Replit does not put the DEPLOYMENT domain in the workspace environment —
    only the dev one — so these are patterns, probed rather than trusted. Each
    is asked for /api/version and only kept if it answers as this app.
    """
    import os
    slug = os.getenv("REPL_SLUG", "")
    owner = os.getenv("REPL_OWNER", "")
    out = []
    for host in (f"{slug}.replit.app",
                 f"{slug}-{owner}.replit.app",
                 f"{slug}.{owner}.repl.co"):
        if slug and "." in host and not host.startswith("."):
            out.append("https://" + host)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="the deployment's base url; omitted, this "
                                  "shell tries to find it")
    ap.add_argument("--apply", action="store_true",
                    help="actually write; without it nothing is inserted")
    a = ap.parse_args()

    if a.url and not _looks_like_a_placeholder(a.url):
        base = a.url.rstrip("/")
    else:
        if a.url:
            print(f"{a.url!r} is not a url — it is the placeholder from the "
                  f"instructions.\n")
        print("Looking for the deployment...")
        base = None
        for cand in _discover():
            probe, err = _get(cand + "/api/version", timeout=10)
            print(f"  {cand}  {'answered' if isinstance(probe, dict) else err}")
            if isinstance(probe, dict) and "fingerprint" in probe:
                base = cand
                break
        if not base:
            print("\nCould not find it. The url is the one you open the "
                  "dashboard with:")
            print("  Replit -> Deployments (or Publishing) -> your app -> the "
                  "link at the top")
            print("  or whatever address is in your browser when the dashboard "
                  "is open.")
            print("\nThen: python3 tools/pull_from_deployment.py --url "
                  "https://that-address")
            return 2
        print(f"\nfound {base}\n")

    ver, err = _get(base + "/api/version")
    if err or not isinstance(ver, dict):
        print(f"cannot read {base}/api/version — {err or 'not JSON'}")
        print("Nothing was read and nothing was written.")
        return 2
    them = (ver.get("db") or {}).get("identity")
    print(f"deployment  {(ver.get('db') or {}).get('target', '?')}")
    print(f"            identity {them or 'unknown'}")

    import server.db as db
    from sqlalchemy import text
    with db.engine.connect() as c:
        try:
            mine = str(c.execute(text(
                "SELECT system_identifier::text FROM pg_control_system()")).scalar())
        except Exception:
            mine = None
    print(f"this shell   {db.engine.url.host or db.engine.url.database}")
    print(f"            identity {mine or 'unknown'}")
    if them and mine and them == mine:
        print("\nSAME database. There is nothing to copy.")
        return 0
    print()

    listing, err = _get(base + "/api/reviews")
    if err:
        print(f"cannot list the deployment's reviews — {err}")
        return 2
    rows = listing if isinstance(listing, list) else (listing or {}).get("reviews", [])
    ids = [r.get("id") for r in rows if r.get("id")]
    print(f"{len(ids)} review(s) on the deployment")

    s = db.SessionLocal()
    try:
        have = {r[0] for r in s.query(db.Review.id).all()}
    finally:
        s.close()
    missing = [i for i in ids if i not in have]
    print(f"{len(ids) - len(missing)} already here · {len(missing)} missing\n")

    if not missing:
        print("Every review the deployment has is already in this database.")
        print("Nothing to copy — repoint the deployment's DATABASE_URL and "
              "republish.")
        return 0

    for i in missing:
        print(f"  missing: {i}")
    if not a.apply:
        print(f"\nDRY RUN — nothing was written. Re-run with --apply to copy "
              f"the {len(missing)} above.")
        return 0

    dropped = set()
    copied = 0
    s = db.SessionLocal()
    try:
        for rid in missing:
            payload, err = _get(f"{base}/api/reviews/{rid}")
            if err or not payload:
                print(f"  SKIPPED {rid} — {err or 'empty payload'}")
                continue
            rv = payload.get("review") or {}
            dr = payload.get("draft") or {}
            s.add(db.Review(**{k: _coerce(db.Review, k, rv[k])
                               for k in _copyable(set(rv), db.Review)}))
            if dr:
                keys = set(dr)
                s.add(db.RcaDraft(id=f"draft_{rid}", review_id=rid,
                                  **{k: _coerce(db.RcaDraft, k, dr[k])
                                     for k in _copyable(keys, db.RcaDraft)
                                     if k not in ("id", "review_id")}))
                dropped |= set(_absent(keys, db.RcaDraft, ("id", "review_id")))
            copied += 1
            print(f"  copied {rid}")
        s.commit()
    finally:
        s.close()

    print(f"\n{copied} review(s) copied.")
    if dropped:
        # Rule one of this codebase: what could NOT be done is counted and
        # said. A copy that silently loses columns looks exactly like a
        # complete one until somebody opens a card and finds a blank section.
        print(f"\n{len(dropped)} draft column(s) are NOT in the API payload and "
              f"could not be copied:")
        print("  " + ", ".join(sorted(dropped)))
        print("  These are blank on the copied rows. Re-run those reviews to "
              "rebuild them, or use tools/migrate_db.py with the Neon "
              "connection string for a byte-exact copy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
