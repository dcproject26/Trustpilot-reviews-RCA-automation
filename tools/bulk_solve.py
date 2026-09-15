#!/usr/bin/env python3
"""Mark a selected set of reviews as solved, credited to one person.

This WRITES to the review table, so it is a DRY RUN unless you pass --apply. The
dry run prints exactly which reviews would change and which would not, because a
bulk write you cannot preview is a bulk write you cannot check.

    # see what would happen (writes nothing)
    python tools/bulk_solve.py --owner Devshree --ids tp_123,tp_456
    python tools/bulk_solve.py --owner Devshree --status draft --from 2026-09-01 --to 2026-09-10

    # do it
    python tools/bulk_solve.py --owner Devshree --ids tp_123,tp_456 --apply

"Solved" here means what the RCA card's "finish without posting" means: status
becomes 'sent', closed_at is stamped, and a close_reason records that it was a
bulk action. It does NOT post anything to Slack — nothing leaves this machine.

Rule 1: the summary separates what changed from what could not, and names the
reviews in each group. "3 already solved, 1 id not found" is a different fact
from "4 updated", and a run that silently did nothing must not look like a run
that did everything.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

CLOSE_REASON = "Bulk-solved from the command line without posting to Slack."


def select(db, ids=None, status=None, date_from=None, date_to=None, owner_is=None):
    """The reviews a run would touch, plus the ids that matched nothing.

    Returning the misses is the point: an id that does not exist and an id that
    was already solved are different problems, and a caller that only gets the
    hits cannot tell either from "everything was fine"."""
    from server.db import Review
    q = db.query(Review)
    if ids:
        q = q.filter(Review.id.in_(list(ids)))
    if status:
        q = q.filter(Review.status == status)
    if date_from:
        q = q.filter(Review.received_at.isnot(None), Review.received_at >= date_from)
    if date_to:
        q = q.filter(Review.received_at.isnot(None), Review.received_at < date_to)
    if owner_is:
        q = q.filter(Review.picked_up_by == owner_is)
    found = q.all()
    missing = sorted(set(ids) - {r.id for r in found}) if ids else []
    return found, missing


def plan(reviews, owner: str):
    """Split the selection into what this run would change and what it would not.

    A review already at 'sent' is left ALONE — re-stamping it would rewrite the
    closed_at of work someone actually finished, and silently reassign its
    owner."""
    from server.tiers import SENT
    to_change, already = [], []
    for r in reviews:
        (already if r.status == SENT else to_change).append(r)
    return to_change, already


def apply(db, reviews, owner: str, now: datetime | None = None) -> int:
    """Mark them solved. Returns how many rows were actually written."""
    from server.tiers import SENT
    now = now or datetime.utcnow()
    n = 0
    for r in reviews:
        r.status = SENT
        r.picked_up_by = owner
        r.closed_at = now
        r.close_reason = CLOSE_REASON
        n += 1
    db.commit()
    return n


def _report(to_change, already, missing, owner, applied: bool) -> str:
    lines = []
    verb = "Solved" if applied else "Would solve"
    lines.append(f"{verb} {len(to_change)} review(s) as {owner}.")
    for r in to_change[:20]:
        lines.append(f"  - {r.id}  ({r.status} -> sent)")
    if len(to_change) > 20:
        lines.append(f"  ... and {len(to_change) - 20} more")
    # NOT merged into the count above: these are the reviews the run did not act
    # on, and saying so is the difference between "nothing matched" and "it ran".
    if already:
        lines.append(f"Left alone — already solved: {len(already)}"
                     f" ({', '.join(r.id for r in already[:5])}"
                     f"{'...' if len(already) > 5 else ''})")
    if missing:
        lines.append(f"NOT FOUND — no review with these ids: {', '.join(missing)}")
    if not to_change:
        lines.append("Nothing to do. (This is a real answer, not a failure: the "
                     "selection matched no unsolved review.)")
    if not applied and to_change:
        lines.append("\nDRY RUN — nothing was written. Re-run with --apply to do it.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", required=True, help="who to credit, e.g. Devshree")
    ap.add_argument("--ids", help="comma-separated review ids")
    ap.add_argument("--status", help="only reviews currently in this status, e.g. draft")
    ap.add_argument("--from", dest="date_from", help="received on/after, YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="received before, YYYY-MM-DD")
    ap.add_argument("--owner-is", help="only reviews already picked up by this person")
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    a = ap.parse_args(argv)

    if not (a.ids or a.status or a.date_from or a.owner_is):
        # Refuse to guess. "Solve everything" is not something to reach by
        # leaving the arguments off.
        ap.error("give a selection: --ids, and/or --status/--from/--to/--owner-is")

    def day(v):
        if not v:
            return None
        try:
            return datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            ap.error(f"date must be YYYY-MM-DD, got {v!r}")

    from server.db import SessionLocal
    db = SessionLocal()
    try:
        ids = [x.strip() for x in a.ids.split(",") if x.strip()] if a.ids else None
        found, missing = select(db, ids, a.status, day(a.date_from), day(a.date_to),
                                a.owner_is)
        to_change, already = plan(found, a.owner)
        if a.apply and to_change:
            apply(db, to_change, a.owner)
        print(_report(to_change, already, missing, a.owner, a.apply))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
