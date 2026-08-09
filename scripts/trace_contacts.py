"""Guest ↔ Support, as the Slack post and the card will render it.

WHAT THIS ANSWERS. The post opened with a contact the guest was never in:

    contact 01  ZD-33978941  2 events
       summary: Agent reviewed escalation and marked NAR
         - 02 Aug 15:28 web/co   we: Agent marked NAR; no further action
         - 03 Aug 12:45 web/co   we: ORM escalation; 25% credit

Two internal agent actions dressed as an exchange. This prints every stored
frame with the verdict each rule reaches, so "why is that row a contact" has
an answer on screen instead of in a diff.

IT DRIVES THE REAL FUNCTIONS. `split_contact_frames`, `is_conversation`,
`guest_took_part` and slack.py's own `_contacts` / `_note_for` are imported
and called — the same code the post is built from. Rebuilding the grouping
here would report on the rebuild, which has cost two sessions already.

TWO FIXES LAND AT DIFFERENT TIMES, and this says which you are looking at:

  `guest_took_part` runs at RENDER, so it applies to the card you already
  have. No regenerate needed.
  `promoted_from_internal` is stamped when events are SHAPED, so stored frames
  carry it only after a regenerate. Its absence on an old card is expected and
  is reported as such, not as a failure.

    python3 scripts/trace_contacts.py tp_abc123
    python3 scripts/trace_contacts.py --bid 32885089

Read-only: reads the stored draft and prints. No Zendesk, no model call,
nothing written, nothing posted.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _one(v, n=58):
    return " ".join(str(v or "").split())[:n]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("review_id", nargs="?", help="review id (tp_...)")
    ap.add_argument("--bid", help="booking id, if you do not have the review id")
    a = ap.parse_args(argv)
    if not a.review_id and not a.bid:
        ap.error("give a review id or --bid")

    from server.db import SessionLocal, RcaDraft
    from server.services.zendesk import (split_contact_frames, is_conversation,
                                         guest_took_part, moved_frames_note,
                                         NON_CONTACT_THREADS)
    from server.services.slack import _contacts, _note_for

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

        frames = [f for f in (d.support_interaction_frames or [])
                  if isinstance(f, dict)]
        v3 = d.rca_v3 if isinstance(d.rca_v3, dict) else {}
        notes = v3.get("support_interaction_notes")
        if notes is None:
            notes = v3.get("support_interaction")
        if not isinstance(notes, list):
            notes = []

        print(f"\n=== EVERY STORED FRAME ON {d.review_id}: {len(frames)} ===")
        print("  thread/actor    int  prom  guest  frame-test   what it is")
        print("  " + "-" * 72)
        marked = 0
        for f in frames:
            th = str(f.get("thread") or "?").lower()
            ac = str(f.get("actor") or "?").lower()
            prom = bool(f.get("promoted_from_internal"))
            marked += prom
            gs = bool(str(f.get("guestSaid") or "").strip()) or ac == "guest"
            ok = is_conversation(f)
            why = ""
            if not ok:
                if th in NON_CONTACT_THREADS:
                    why = f"thread {th} is machinery"
                elif f.get("is_internal"):
                    why = "still marked internal"
                elif prom:
                    why = "promoted internal note"
                else:
                    why = "actor is machinery"
            print(f"  {th + '/' + ac:<15} {'Y' if f.get('is_internal') else '-':<4} "
                  f"{'Y' if prom else '-':<5} {'Y' if gs else '-':<6} "
                  f"{'CONTACT' if ok else 'no':<12} "
                  f"{_one(f.get('label') or f.get('summary'))}")
            if why:
                print(f"  {'':<15} {'':<4} {'':<5} {'':<6} {'':<12} ^ {why}")

        convo, moved = split_contact_frames(frames)
        print(f"\n=== WHAT THE POST AND THE CARD WILL SHOW ===")
        rows = _contacts(convo)
        if not rows:
            print("  No contact. The guest never wrote in on this booking.")
        for n, (key, group) in enumerate(rows, 1):
            note = _note_for(key, notes)
            print(f"\n  contact {n:02d}  {'ZD-' + key if key else '(no ticket)'}"
                  f"  {len(group)} event(s)")
            print(f"     {_one((note or {}).get('summary'), 70) or '(no model note)'}")
            for f in group:
                who = str(f.get("actor") or "?").lower()
                print(f"       - {f.get('time') or '?':<16} {who:<7} "
                      f"{_one(f.get('guestSaid') or f.get('weDid'), 44)}")
            if not guest_took_part(group):
                print("       !! NO GUEST IN THIS EXCHANGE — this is the bug. "
                      "Every frame is agent-side.")

        # ── the counts, each said in its own words ─────────────────────────
        said = moved_frames_note(moved)
        print(f"\n=== WHAT WAS LEFT OUT: {len(moved)} frame(s) ===")
        print(f"  {said or '(nothing was left out)'}")
        print("\n  Left out is not lost — every one of these is on the events")
        print("  timeline. This section is who spoke to the guest.")

        print("\n=== WHICH FIX YOU ARE LOOKING AT ===")
        print(f"  {marked} of {len(frames)} frame(s) carry "
              f"`promoted_from_internal`.")
        if not marked:
            print("    ZERO IS EXPECTED ON A CARD GENERATED BEFORE THE FIX —")
            print("    the marker is stamped when events are SHAPED, so it "
                  "appears only")
            print("    after a regenerate. The grouping above is still "
                  "correct: the")
            print("    guest-took-part rule runs at RENDER and applies to this "
                  "card now.")
        else:
            print("    Stamped, so this card was regenerated since the fix. "
                  "Both rules")
            print("    are in force.")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
