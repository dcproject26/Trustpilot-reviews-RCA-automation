"""Drop Actions Taken rows that no current gap explains. DRY RUN by default.

WHY A SCRIPT AND NOT AN AUTOMATIC SWEEP. `actions_taken` holds two kinds of
row and the column cannot tell them apart:

  derived      built from `what_went_wrong.gaps` on every rebuild
  hand-typed   a row an associate wrote, which a rebuild must never eat

`hand_typed_actions` separates them by subtracting what the previous gaps
explain. That works from the run AFTER gaps were first stored. It does NOT
reach backwards: a draft written before gaps existed has rows that are
neither derived-from-current-gaps nor hand-typed — they are model output from
the old fixes-derived section — and nothing distinguishes them from a row
somebody typed the same afternoon.

So the choice is yours and not the code's. Deleting somebody's work on a guess
is the expensive direction; leaving four stale recommendations on a CO tab
that no gap explains is the cheap one, and reversible. This prints exactly
what it would remove and removes nothing until you say so.

    python3 scripts/clear_unattributed_actions.py tp_abc123          # shows
    python3 scripts/clear_unattributed_actions.py tp_abc123 --apply  # writes

Rows the CURRENT gaps explain are never touched — they are rebuilt from the
gaps anyway, and removing them here would just make the column disagree with
the card until the next regenerate.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("review_id", nargs="?", help="review id (tp_...)")
    ap.add_argument("--bid", help="booking id, if you do not have the review id")
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without it nothing is changed.")
    a = ap.parse_args(argv)
    if not a.review_id and not a.bid:
        ap.error("give a review id or --bid")

    from server.db import SessionLocal, RcaDraft
    from server.checklist import actions_from_gaps

    s = SessionLocal()
    try:
        q = s.query(RcaDraft)
        d = (q.filter(RcaDraft.review_id == a.review_id).first() if a.review_id
             else next((r for r in q.all()
                        if str((r.booking or {}).get("id") or "") == str(a.bid)),
                       None))
        if not d:
            ids = [r.review_id for r in q.limit(8).all()]
            print("No draft found for that id.")
            if ids:
                print("Drafts that are here: " + ", ".join(ids))
            return 1

        wwr = ((d.rca_v3 if isinstance(d.rca_v3, dict) else {})
               .get("what_went_wrong") or {})
        gaps = wwr.get("gaps")
        if gaps is None:
            # REFUSE RATHER THAN GUESS WIDE. With no stored gaps every row
            # looks unexplained, so this would clear the whole column —
            # including rows a person typed. Regenerating first is what makes
            # the question answerable.
            print("This draft has NO stored gaps, so every row would look "
                  "unexplained and this would clear the entire column.")
            print("Regenerate the RCA first, then run this again.")
            return 1

        derived, _ = actions_from_gaps(gaps, keep=None)
        explained = {str(r).strip() for rows in derived.values()
                     for r in (rows or [])}
        stored = d.actions_taken if isinstance(d.actions_taken, dict) else {}

        doomed = {}
        for tab, rows in stored.items():
            for row in (rows or []):
                txt = str(row or "").strip()
                if txt and txt not in explained:
                    doomed.setdefault(tab, []).append(txt)

        if not doomed:
            print(f"Nothing to clear on {d.review_id}: every row in the column "
                  f"is explained by a current gap.")
            return 0

        n = sum(len(v) for v in doomed.values())
        print(f"\n{n} row(s) on {d.review_id} that NO current gap explains:\n")
        for tab, rows in doomed.items():
            print(f"  {tab.upper()}")
            for r in rows:
                print(f"    - {' '.join(r.split())}")
        print("\nEach is either stale model output from the old fixes-derived "
              "section, or a row somebody typed. Nothing in the data tells "
              "them apart — read them before deciding.")

        if not a.apply:
            print("\nDRY RUN — nothing was changed. Re-run with --apply to "
                  "remove exactly the rows above.")
            return 0

        kept = {tab: [r for r in (rows or [])
                      if str(r or "").strip() in explained]
                for tab, rows in stored.items()}
        d.actions_taken = kept
        try:
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(d, "actions_taken")
        except Exception:
            pass
        s.commit()
        print(f"\nRemoved {n} row(s). The column now holds only what the "
              f"current gaps explain.")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
