"""Where an internal note is lost, printed stage by stage for one booking.

I have now diagnosed this four times by reading code and been wrong or
incomplete each time — the public=False drop, the chat-bookkeeping over-match,
the shaping truncation, the position-based cap. Each fix was real and none of
them was the whole answer, and every round cost a screenshot and a re-run.

This asks Zendesk directly and prints what happens to every comment at every
gate, so the next answer is a fact rather than another reading of the source.

    python3 scripts/trace_notes.py 32885089
    python3 scripts/trace_notes.py 32885089 --show-body

Read-only. It fetches and classifies; it writes nothing and changes nothing.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("booking_id")
    ap.add_argument("--show-body", action="store_true",
                    help="print the first 200 chars of each comment")
    a = ap.parse_args(argv)

    from server.services import zendesk as zd
    from server.ticket_notes import note_disposition

    _z = zd._get_client()
    if _z is None:
        print("Zendesk is not live in this environment — nothing to trace.\n"
              "Set the Zendesk credentials and run this where the pipeline runs.")
        return 2

    tickets, tally = zd.collect_tickets(
        a.booking_id, lambda q: zd._search_with_retry(_z, q))
    print(f"\n=== SEARCH ===")
    print(f"  {tally}")
    print(f"  {len(tickets)} ticket(s): {[str(t.id) for t in tickets]}")
    if not tickets:
        print("\nNo tickets. Nothing downstream can show a note that was "
              "never fetched.")
        return 0

    print(f"\n=== COMMENTS, per ticket ===")
    totals = {"public": 0, "private": 0, "private_kept": 0,
              "private_dropped": 0, "machinery": 0}
    for t in tickets:
        try:
            comments = list(_z.tickets.comments(ticket=t.id))
        except Exception as e:
            print(f"  ZD-{t.id}: comments fetch FAILED — {e}")
            continue
        print(f"  ZD-{t.id}: {len(comments)} comment(s)")
        for c in comments:
            body = getattr(c, "body", "") or getattr(c, "html_body", "") or ""
            private = getattr(c, "public", True) is False
            via = getattr(getattr(c, "via", None), "channel", "") or ""
            verdict, why = note_disposition(body)
            reason = zd._internal_reason(body, via)
            totals["private" if private else "public"] += 1
            if reason:
                totals["machinery"] += 1
            state = []
            if private:
                state.append("PRIVATE")
                if verdict == "drop":
                    state.append(f"DROPPED at fetch ({why})")
                    totals["private_dropped"] += 1
                else:
                    state.append(f"kept ({verdict}: {why})")
                    totals["private_kept"] += 1
            else:
                state.append("public")
            if reason:
                state.append(f"marked internal: {reason}")
            first = " ".join(str(body).split())[:90]
            print(f"      · {getattr(c, 'created_at', '?')}  via={via or '-':<12} "
                  f"{' | '.join(state)}")
            print(f"        {first}")
            if a.show_body:
                print(f"        FULL: {' '.join(str(body).split())[:200]}")

    print(f"\n=== TOTALS ===")
    for k, v in totals.items():
        print(f"  {k:<16} {v}")
    print("\nWhat to read here:")
    print("  private = 0            → Zendesk returned no internal notes for")
    print("                           these tickets. The note is on a ticket")
    print("                           the search did not find, or this API")
    print("                           token cannot see private comments.")
    print("  DROPPED at fetch       → note_disposition called it ticket")
    print("                           administration. If one of those is the")
    print("                           note you want, the rule is too narrow.")
    print("  kept, marked internal  → it reached the timeline. If it is not on")
    print("                           the card, the loss is AFTER this point:")
    print("                           the shaping call or the render.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
