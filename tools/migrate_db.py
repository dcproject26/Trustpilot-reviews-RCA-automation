#!/usr/bin/env python3
"""Move the deployment's data onto one database, safely, with a dry run first.

    python3 tools/migrate_db.py --from "$OLD_URL" --to "$NEW_URL"          # dry run
    python3 tools/migrate_db.py --from "$OLD_URL" --to "$NEW_URL" --apply  # do it

The workspace is on Helium and the published deployment is still on the
Neon-backed legacy instance, because Replit's upgrade only touched the
workspace and deployment secrets are a separate store. Two dashboards then
disagree about what exists, permanently, and no amount of cache-clearing
changes it.

WHY NOT pg_dump. A plain restore into a populated target fails on duplicate
primary keys, and --clean drops rows the target already has that the source
does not — which here means the 30 reviews the workspace has been producing
since the split. Neither is what you want. This copies row by row and SKIPS
anything whose primary key already exists on the target, so it is:

  * safe to run twice — the second run copies nothing and says so;
  * safe with both sides populated — nothing on the target is touched;
  * honest about what it did NOT copy, which is the number that matters.

It never writes to the source.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Parents before children: a draft with no review breaks the join the whole
# dashboard is built on.
ORDER = ["reviews", "rca_drafts", "review_metrics", "slack_events_seen"]


def _norm(url: str) -> str:
    return (url or "").replace("postgres://", "postgresql://", 1)


def _identity(engine):
    """Something that proves two urls are or are not the same database.

    For Postgres that is the cluster's own system identifier: hostnames lie,
    because Replit proxies the same Postgres under different names, so two
    urls can look split when they are not and identical when they are not.

    For SQLite it is the file path, which is exactly as authoritative — the
    database IS the file. Returning None there instead would make the script
    untestable without two live Postgres instances, and an untested migration
    is not one I would run against your data.
    """
    from sqlalchemy import text
    if engine.url.get_backend_name().startswith("sqlite"):
        import os
        return os.path.abspath(engine.url.database or ":memory:")
    try:
        with engine.connect() as c:
            return str(c.execute(text(
                "SELECT system_identifier::text FROM pg_control_system()")).scalar())
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True, help="source url")
    ap.add_argument("--to", dest="dst", required=True, help="target url")
    ap.add_argument("--apply", action="store_true",
                    help="actually copy; without it nothing is written")
    a = ap.parse_args()

    from sqlalchemy import create_engine, text, inspect, select, Table, MetaData

    def _engine(url, side):
        try:
            return create_engine(_norm(url), pool_pre_ping=True)
        except Exception as e:
            # A missing driver is a setup problem with a one-line fix, and it
            # arrived as a forty-line traceback that reads like the migration
            # itself broke.
            print(f"cannot open the {side}: {type(e).__name__}: "
                  f"{' '.join(str(e).split())[:160]}")
            if "psycopg2" in str(e):
                print("  pip install psycopg2-binary")
            return None

    src, dst = _engine(a.src, "source"), _engine(a.dst, "target")
    if src is None or dst is None:
        print("\nNothing was read and nothing was written.")
        return 2

    si, di = _identity(src), _identity(dst)
    print(f"source  {src.url.host or '?'}  identity {si or 'unknown'}")
    print(f"target  {dst.url.host or '?'}  identity {di or 'unknown'}")
    if si and di and si == di:
        print("\nThese are the SAME database — same system identifier, whatever "
              "the hostnames say.\nThere is nothing to migrate. Point the "
              "deployment at this url and stop.")
        return 0
    if not (si and di):
        print("\nOne side would not report its identity, so I cannot prove "
              "these are different databases.\nRefusing to copy on a guess.")
        return 2
    print()

    s_tables = set(inspect(src).get_table_names())
    d_tables = set(inspect(dst).get_table_names())

    total_new = total_skipped = 0
    plan = []
    for tbl in ORDER:
        if tbl not in s_tables:
            print(f"  {tbl:20s} not on the source — nothing to copy")
            continue
        if tbl not in d_tables:
            print(f"  {tbl:20s} MISSING ON TARGET — start the app once against "
                  f"the target so init_db() creates it, then re-run me")
            return 1
        # Read through the TYPED table, not a raw SELECT. A raw select returns
        # datetimes as strings on SQLite and the typed insert then rejects
        # them — and a migration that only works between two Postgres
        # instances is one I cannot test before running it against your data.
        _md_s = MetaData()
        _st = Table(tbl, _md_s, autoload_with=src)
        with src.connect() as c:
            rows = [dict(r._mapping) for r in c.execute(select(_st))]
        if not rows:
            print(f"  {tbl:20s} 0 rows on the source")
            continue
        pk = inspect(src).get_pk_constraint(tbl)["constrained_columns"]
        if not pk:
            print(f"  {tbl:20s} has no primary key — cannot tell a duplicate "
                  f"from a new row, so it is skipped rather than doubled")
            continue
        with dst.connect() as c:
            have = {tuple(r._mapping[k] for k in pk)
                    for r in c.execute(text(f"SELECT {', '.join(pk)} FROM {tbl}"))}
        new = [r for r in rows if tuple(r[k] for k in pk) not in have]
        skipped = len(rows) - len(new)
        total_new += len(new)
        total_skipped += skipped
        plan.append((tbl, new, pk))
        print(f"  {tbl:20s} {len(rows):5d} on source · {len(new):5d} new · "
              f"{skipped:5d} already on target")

    print(f"\n{total_new} row(s) would be copied, {total_skipped} skipped as "
          f"already present.")
    if not a.apply:
        print("\nDRY RUN — nothing was written. Re-run with --apply to copy.")
        return 0
    if not total_new:
        print("Nothing to copy.")
        return 0

    # Only new rows, one table at a time, parents first. A failure part-way
    # leaves the tables already done — which is why the skip-by-key rule
    # matters: re-running finishes the job instead of doubling it.
    md = MetaData()
    done = 0
    for tbl, new, pk in plan:
        if not new:
            continue
        t = Table(tbl, md, autoload_with=dst)
        with dst.begin() as c:
            for r in new:
                c.execute(t.insert().values(
                    **{k: v for k, v in r.items() if k in t.c}))
        done += len(new)
        print(f"  copied {len(new):5d} into {tbl}")

    print(f"\n{done} row(s) copied.")
    print("Now set the deployment's DATABASE_URL to the target and republish, "
          "then run tools/doctor.py on BOTH surfaces and check the identity "
          "line matches. Nothing else proves they share a database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
