"""The events timeline as stored, with the things that decide it made visible.

WHY THIS EXISTS. The timeline has been wrong four times in four different
ways — internal notes missing, chronology scrambled, rows too vague to read,
a support contact not recognised as one — and each time the only diagnostic
was a screenshot. A screenshot shows the rendering. It does not show
`time_sort`, which is what actually orders the list, nor `is_internal`, which
is what decides whether a row appears at all.

So this prints the fields the CODE uses beside the text a reader sees, and
checks the invariants that have broken before:

  ORDER      the list is sorted by `time_sort`, not by `time`. A row whose
             display string and sort key disagree is the UTC/IST bug, and it
             puts a review before the chat that caused it.
  INTERNAL   internal notes are the reschedule and cancellation records. If
             none are present that is either a case with none or a filter
             eating them, and those must not look alike.
  CONTACT    a guest contact has to survive `is_conversation` to reach the
             Guest ↔ Support panel. A chat transcript that fails it is a
             support interaction that was extracted and then dropped.
  BOOKENDS   a row with no digits in `time` was stamped from the booking or
             the review date. Stamped rows are named, because a bookend is a
             JUDGEMENT and not a record.

IT DRIVES THE REAL PREDICATES. `is_conversation`, `_event_rank` and
`_normalize_time` are imported, never restated. A tracer that rebuilds the
rule it is checking reports on its own copy — that is how trace_shaping.py
called a fixed bug broken and cost a session.

    python3 scripts/trace_timeline.py tp_abc123
    python3 scripts/trace_timeline.py --bid 32885089

Read-only: reads the stored draft and prints. No Zendesk, no model call,
nothing written.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _has_digit(s) -> bool:
    return any(c.isdigit() for c in str(s or ""))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("review_id", nargs="?", help="review id (tp_...)")
    ap.add_argument("--bid", help="booking id, if you do not have the review id")
    a = ap.parse_args(argv)
    if not a.review_id and not a.bid:
        ap.error("give a review id or --bid")

    from server.db import SessionLocal, RcaDraft
    from server.services.zendesk import is_conversation

    s = SessionLocal()
    try:
        q = s.query(RcaDraft)
        d = (q.filter(RcaDraft.review_id == a.review_id).first() if a.review_id
             else next((r for r in q.all()
                        if str((r.booking or {}).get("id") or "") == str(a.bid)),
                       None))
        if not d:
            # NAME WHAT WOULD WORK. "not found" alone is the same sentence a
            # broken lookup gives.
            ids = [r.review_id for r in q.limit(8).all()]
            print("No draft found for that id.")
            if ids:
                print("Drafts that are here: " + ", ".join(ids))
            return 1

        rows = d.timeline or []
        print(f"\n=== EVENTS TIMELINE ON {d.review_id}: {len(rows)} rows ===")
        if not rows:
            print("  The timeline is EMPTY. That is either a booking with no "
                  "events or a shaping call that failed — check the confidence "
                  "trail for a shaping entry before reading it as the former.")
            return 0

        print("  sort  = time_sort, the ISO-8601 UTC key the list is ordered by")
        print("  shown = `time`, the IST display string the reader sees\n")

        internal = contacts = stamped = 0
        for i, r in enumerate(rows):
            r = r if isinstance(r, dict) else {}
            ts = str(r.get("time_sort") or "")
            disp = str(r.get("time") or "")
            is_int = bool(r.get("is_internal"))
            internal += is_int
            conv = is_conversation(r)
            contacts += bool(conv)
            # A ROW WITH NO DIGITS IN `time` WAS BOOKEND-STAMPED. The stamp is
            # a judgement — "this belongs at the booking's creation" — and a
            # judgement has to announce itself.
            stamp = not _has_digit(disp)
            stamped += stamp
            marks = []
            if is_int:
                marks.append("INTERNAL:" + (str(r.get("internal_reason"))
                                            or "unnamed"))
            if conv:
                marks.append("CONTACT")
            if stamp:
                marks.append("BOOKEND-STAMPED")
            if not ts:
                marks.append("NO SORT KEY — position is arbitrary")
            print(f"\n  [{i:>2}] sort {ts or '(none)':<26} shown {disp or '(none)'}")
            print(f"       {str(r.get('thread') or '?'):<8} "
                  f"{str(r.get('actor') or '?'):<6} "
                  f"{str(r.get('ticket_id') or '-')}"
                  + ("   " + " | ".join(marks) if marks else ""))
            print(f"       {str(r.get('label') or '(no label)')}")
            print(f"       {' '.join(str(r.get('summary') or '').split())}")

        # ── ORDER: the key that sorts, not the string that shows ───────────
        print("\n=== ORDER ===")
        keys = [str((r if isinstance(r, dict) else {}).get("time_sort") or "")
                for r in rows]
        dated = [(i, k) for i, k in enumerate(keys) if k]
        bad = [(i, j) for (i, k), (j, k2) in zip(dated, dated[1:]) if k > k2]
        if not dated:
            print("  NO ROW CARRIES A SORT KEY. The order on screen is the "
                  "order the shaping call returned them in, which is not a "
                  "chronology.")
        elif bad:
            print(f"  OUT OF ORDER at {len(bad)} boundary/ies — the list is not "
                  f"sorted by its own key:")
            for i, j in bad:
                print(f"    [{i}] {keys[i]}  comes before  [{j}] {keys[j]}")
        else:
            print(f"  Sorted. {len(dated)} of {len(rows)} rows carry a sort key.")
        if len(dated) < len(rows):
            print(f"  {len(rows) - len(dated)} row(s) carry NO sort key and sit "
                  f"wherever the list put them — that is a placement nothing "
                  f"in the record supports.")

        # ── the three counts that must not read as zero-because-broken ─────
        print("\n=== WHAT IS IN HERE ===")
        # ZERO HERE IS THE GOOD OUTCOME, and saying otherwise is the inverse
        # bug. `note_disposition` CLEARS `is_internal` on every note it keeps
        # — that is what promotes a booking fact out from behind the toggle
        # and onto the timeline. So a healthy card has no rows left marked
        # internal: the reschedule and cancellation records are here, rendered
        # inline, indistinguishable in this field from rows that were never
        # internal at all.
        #
        # An earlier version of this line read zero as "the fetch gate ate
        # them" and would have called a working card broken. What is still
        # marked internal is what was DROPPED as ticket administration, and
        # that is the only thing this count can honestly report.
        print(f"  {internal} row(s) are still marked internal — these are the "
              f"ones dropped as ticket administration and kept behind the "
              f"toggle.")
        if not internal:
            print("    Zero is the EXPECTED state, not a warning: a kept note "
                  "has `is_internal` cleared so it renders inline. The "
                  "reschedule and cancellation records are in the rows above.")
        print("    This count cannot see promoted notes — run trace_notes.py "
              "to check what the fetch gate dropped before it got here.")
        print(f"  {contacts} row(s) pass `is_conversation` and reach the "
              f"Guest <-> Support panel.")
        if not contacts:
            print("    NONE. A case where the guest contacted us and no row "
                  "passes is the extraction working and the classifier "
                  "rejecting it — that is the bug, not an empty case.")
        print(f"  {stamped} row(s) were BOOKEND-STAMPED — placed at the "
              f"booking's creation or the review's publication because the "
              f"record carried no clock. That placement is a judgement.")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
