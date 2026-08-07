"""Move `received_at` off the ingest moment for reviews written before the fix.

WHAT THIS CAN AND CANNOT DO, because the difference matters more than the
backfill itself.

`received_at` has held three different facts at different times:

  1. the Trustpilot publish time — what "Review date" and the timeline's
     "Review posted" row claim to be;
  2. the Slack message timestamp — when the integration relayed it, later by
     however long the relay took;
  3. the ingest moment — `datetime.utcnow()` at the instant we happened to
     run, which is not a fact about the review at all.

The column default was removed and the publish date is now read from the
payload, so new reviews hold (1). Rows written before that hold (3), and
re-running the match does not rewrite them: `received_at` is set once, at
creation. That is why a re-run does not move the "Review posted" row.

THE PUBLISH DATE IS NOT RECOVERABLE for those rows. It was never stored — no
column, no payload blob — so this script does NOT invent one. What it can do
is move a row from (3) to (2): `slack_ts` IS stored, and the relay time is at
least a fact about the review rather than about our cron. Every row it moves
is reported, and every row it leaves alone is reported with the reason.

    python3 scripts/backfill_received_at.py            # report only
    python3 scripts/backfill_received_at.py --apply    # write

Dry by default, because a column this many things read is not one to rewrite
on a typo.
"""
import argparse
import sys
from datetime import datetime, timezone


def slack_time(slack_ts):
    """The relay time from a Slack ts, or None if it is not one.

    Same range check as `_received_at_from`: a value outside 2001-2096 is not
    a message timestamp, and treating one as a date would put a review in
    1970 — which reads as data rather than as the parse failure it is.
    """
    try:
        ts = float(str(slack_ts).strip())
    except (TypeError, ValueError):
        return None
    if not (1e9 < ts < 4e9):
        return None
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def classify(received_at, slack_ts, tolerance_s=90):
    """(action, why) for one row.

    A row whose `received_at` already matches its Slack time is either right
    or already backfilled; either way there is nothing to do, and saying
    "no change" for it is different from saying we could not read it.
    """
    st = slack_time(slack_ts)
    if st is None:
        return "skip", "slack_ts is not a usable timestamp, so there is no better value to move to"
    if received_at is None:
        return "set", "received_at is empty"
    delta = abs((received_at - st).total_seconds())
    if delta <= tolerance_s:
        return "keep", "already the relay time, within tolerance"
    if received_at < st:
        # EARLIER than the relay is what a real publish date looks like: the
        # review existed before Slack carried it. Moving that FORWARD would
        # replace the better fact with the worse one.
        return "keep", "earlier than the relay time — this looks like a real publish date"
    return "set", f"later than the relay time by {delta / 3600:.1f}h — the ingest moment"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is a dry report)")
    args = ap.parse_args(argv)

    from server.db import SessionLocal, Review
    s = SessionLocal()
    counts = {"set": 0, "keep": 0, "skip": 0}
    examples = {"set": [], "skip": []}
    try:
        for r in s.query(Review).all():
            action, why = classify(r.received_at, r.slack_ts)
            counts[action] += 1
            if action == "set":
                st = slack_time(r.slack_ts)
                if len(examples["set"]) < 5:
                    examples["set"].append(f"{r.id}: {r.received_at} -> {st} ({why})")
                if args.apply:
                    r.received_at = st
            elif action == "skip" and len(examples["skip"]) < 5:
                examples["skip"].append(f"{r.id}: {why}")
        if args.apply:
            s.commit()
    finally:
        s.close()

    total = sum(counts.values())
    print(f"{total} review(s) examined")
    print(f"  {counts['set']:>5} moved to the Slack relay time"
          f"{'' if args.apply else ' (dry run — nothing written)'}")
    print(f"  {counts['keep']:>5} left alone — already right, or earlier than "
          f"the relay and so probably a real publish date")
    print(f"  {counts['skip']:>5} could NOT be moved — no usable slack_ts")
    for line in examples["set"]:
        print(f"    set  {line}")
    for line in examples["skip"]:
        print(f"    skip {line}")
    if counts["skip"]:
        print("  a skipped row keeps whatever it had; it is not silently "
              "corrected, and it is not the publish date")
    print("\nNOTE: none of these is the Trustpilot publish date. That was "
          "never stored for these rows and this script does not invent one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
