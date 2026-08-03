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

    # An EMPTY url is not a malformed url, and saying "Could not parse
    # SQLAlchemy URL from string ''" sends someone to look at a url they never
    # typed. It means an unset shell variable — the command was pasted without
    # the line above it that sets one. That has now happened twice, which
    # makes it this tool's problem rather than the reader's.
    for val, flag, side in ((a.src, "--from", "source"),
                            (a.dst, "--to", "target")):
        if not (val or "").strip():
            print(f"{flag} is empty, so the {side} was never opened.")
            print("  An unset shell variable expands to nothing, so the "
                  "command ran with no url at all.")
            print("  Paste the connection string straight into the command "
                  "rather than through a variable:")
            print('    python3 tools/migrate_db.py --from "postgresql://..." '
                  '--to "postgresql://..."')
            print("\nNothing was read and nothing was written.")
            return 2

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
        # EVERY unique key, not just the primary one. reviews.slack_ts is
        # unique, and skipping on the id alone let a row through that the
        # target already held under a different id — the insert then raised
        # IntegrityError, aborted the whole run, and left the earlier tables
        # committed. "Safe to run twice" was true only for the collisions I
        # happened to think of.
        keysets = [list(pk)]
        for uc in (inspect(src).get_unique_constraints(tbl) or []):
            cols = uc.get("column_names") or []
            if cols and cols not in keysets:
                keysets.append(list(cols))
        for ix in (inspect(src).get_indexes(tbl) or []):
            cols = ix.get("column_names") or []
            if ix.get("unique") and cols and all(cols) and cols not in keysets:
                keysets.append(list(cols))

        # Only keys the TARGET actually has. A target whose schema is behind
        # cannot be matched on a column it does not carry, and asking anyway
        # raised "no such column" from inside the duplicate check — which
        # reads as the migration being broken rather than the schema being
        # out of date, and hid the real message two lines further down.
        dst_cols = {c["name"] for c in inspect(dst).get_columns(tbl)}
        usable, unusable = [], []
        for keys in keysets:
            (usable if set(keys) <= dst_cols else unusable).append(keys)
        if not usable:
            print(f"  {tbl:20s} the target has none of this table's key "
                  f"columns — its schema is behind; run the app once against "
                  f"it so init_db() catches up, then re-run me")
            return 1
        for keys in unusable:
            print(f"  {tbl:20s} cannot match on {'+'.join(keys)} — the target "
                  f"has no such column, so a row already there under that key "
                  f"will be attempted and refused rather than skipped")
        keysets = usable

        present = []
        with dst.connect() as c:
            for keys in keysets:
                present.append({
                    tuple(r._mapping[k] for k in keys)
                    for r in c.execute(text(
                        f"SELECT {', '.join(keys)} FROM {tbl}"))})

        def _already_there(row):
            for keys, have in zip(keysets, present):
                vals = tuple(row[k] for k in keys)
                # SQL unique constraints permit any number of NULLs, so two
                # rows that both have slack_ts NULL are not duplicates — and
                # slack_ts IS nullable, because a review added by hand has no
                # Slack message behind it. Treating (None,) as a match would
                # copy the first manual review and silently drop every one
                # after it, counted as "already on target". Data quietly not
                # copied, reported as success, is the worst outcome this tool
                # has available.
                if any(v is None for v in vals):
                    continue
                if vals in have:
                    return True
            return False

        new = [r for r in rows if not _already_there(r)]
        skipped = len(rows) - len(new)
        total_new += len(new)
        total_skipped += skipped
        plan.append((tbl, new, pk))
        extra = ("" if len(keysets) == 1 else
                 f"  (matched on {len(keysets)} key(s): "
                 + "; ".join("+".join(k) for k in keysets) + ")")
        print(f"  {tbl:20s} {len(rows):5d} on source · {len(new):5d} new · "
              f"{skipped:5d} already on target{extra}")

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
    refused = []
    orphaned = []
    for tbl, new, pk in plan:
        if not new:
            continue
        t = Table(tbl, md, autoload_with=dst)

        # A child whose parent was skipped must not be copied. The reviews
        # pass skips a row the target already holds under a different id, and
        # its draft was still copied — pointing at a review that is not there.
        # SQLite does not enforce foreign keys by default, so this does not
        # even raise: it writes a draft the dashboard's join will never find,
        # which is a blank card with no explanation. Checked explicitly, and
        # the ones held back are named.
        fks = []
        for fk in (inspect(src).get_foreign_keys(tbl) or []):
            cols = fk.get("constrained_columns") or []
            ref_t = fk.get("referred_table")
            ref_c = fk.get("referred_columns") or []
            if cols and ref_t and ref_c and ref_t in d_tables:
                with dst.connect() as c:
                    have = {tuple(row._mapping[k] for k in ref_c)
                            for row in c.execute(text(
                                f"SELECT {', '.join(ref_c)} FROM {ref_t}"))}
                fks.append((cols, ref_t, have))

        copied_here = 0
        for r in new:
            missing = next(((cols, ref_t) for cols, ref_t, have in fks
                            if tuple(r[k] for k in cols) not in have), None)
            if missing:
                orphaned.append((tbl, r.get(pk[0], "?"), missing[1],
                                 tuple(r[k] for k in missing[0])))
                continue
            # One transaction per row. A single row the target refuses used to
            # abort the table and everything after it, forty lines of
            # traceback deep, having already committed the tables before it —
            # so the run both failed and half-succeeded, and said neither.
            try:
                with dst.begin() as c:
                    c.execute(t.insert().values(
                        **{k: v for k, v in r.items() if k in t.c}))
                copied_here += 1
            except Exception as e:
                refused.append((tbl, r.get(pk[0], "?"),
                                " ".join(str(e).split())[:120]))
        done += copied_here
        print(f"  copied {copied_here:5d} into {tbl}")

    print(f"\n{done} row(s) copied.")
    if refused:
        # Rule one: what could NOT be done is counted and said. A migration
        # that quietly drops rows looks exactly like a complete one until
        # somebody goes looking for a review that is not there.
        print(f"\n{len(refused)} row(s) were REFUSED by the target and are "
              f"still only on the source:")
        for tbl, key, why in refused[:12]:
            print(f"  {tbl}  {key}  — {why}")
        if len(refused) > 12:
            print(f"  ... and {len(refused) - 12} more")
        print("  Nothing was lost: the source is untouched. Re-run after "
              "resolving the conflict and only these will be attempted.")
    if orphaned:
        print(f"\n{len(orphaned)} row(s) were held back because their parent "
              f"row is not on the target:")
        for tbl, key, ref_t, ref_key in orphaned[:12]:
            print(f"  {tbl}  {key}  — no {ref_t} with key {ref_key}")
        if len(orphaned) > 12:
            print(f"  ... and {len(orphaned) - 12} more")
        print("  Copying them would write rows the dashboard's join can never "
              "find, which renders as a blank card with no reason given.")
        print("  Usually this means the target already holds that review "
              "under a different id — check it before forcing anything.")
    print("\nNow set the deployment's DATABASE_URL to the target and republish, "
          "then run tools/doctor.py on BOTH surfaces and check the identity "
          "line matches. Nothing else proves they share a database.")
    return 1 if (refused or orphaned) else 0


if __name__ == "__main__":
    sys.exit(main())
