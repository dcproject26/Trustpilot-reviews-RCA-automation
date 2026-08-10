"""Which Zendesk route found what, live, for one booking id.

WHAT THIS ANSWERS. A card that says "No direct interaction found between the
customer and the support team" is the model correctly reporting an empty
record. It cannot say WHY the record was empty, and there are four different
whys with four different fixes:

  the search found nothing      the tickets are not indexed under this id
  the search found the wrong    a TICKET whose number equals the BOOKING id
    thing and it was dropped      is dropped on purpose — same numeric space
  a route never ran             `requester:` needs an email, and the email is
                                  read off the tickets the first two routes
                                  found. Nothing found, no email, no third
                                  search — so a guest with seven tickets under
                                  a different booking id is invisible
  the tickets were found and    shaping or the contact split dropped them
    lost later                    later, which is trace_contacts.py's job

From the card those four are one sentence. This runs the REAL
`collect_tickets` with the REAL search, prints every query as it goes out,
every ticket that came back, and every one that was dropped with the reason.

    python3 scripts/trace_zendesk.py 32728059
    python3 scripts/trace_zendesk.py --review tp_1786007317_243143

Read-only. It searches Zendesk and prints. Nothing is written.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _s(v, n=64):
    return " ".join(str(v or "").split())[:n]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("booking_id", nargs="?", help="the booking id to look up")
    ap.add_argument("--review", help="a review id, to read its booking id from")
    a = ap.parse_args(argv)

    from server.config import is_live
    from server.services import zendesk as Z

    bid = a.booking_id
    if not bid and a.review:
        from server.db import SessionLocal, RcaDraft
        s = SessionLocal()
        d = s.query(RcaDraft).filter(RcaDraft.review_id == a.review).first()
        if not d:
            print(f"No draft for {a.review!r}. Try --review with an id from "
                  f"the inbox, or pass a booking id directly.")
            return 1
        bid = str(((d.booking or {}).get("id")
                   or (d.booking or {}).get("bid") or "")).strip()
        s.close()
        print(f"review {a.review} -> booking {bid or '(none on the draft)'}")
    if not bid:
        ap.error("give a booking id, or --review with one that has a booking")

    print(f"\n  zendesk live: {is_live('zendesk')}")
    if not is_live("zendesk"):
        print("  NOTHING BELOW WOULD MEAN ANYTHING — every route would return "
              "an empty list\n  and be indistinguishable from a real miss. "
              "Stopping.")
        return 1

    _z = Z._get_client()
    if _z is None:
        print("  no Zendesk client could be built. That is the credential, "
              "not the data.")
        return 1

    # THE REAL SEARCH, WRAPPED ONLY TO NARRATE IT. Rebuilding the queries here
    # would report on the rebuild — the mistake trace_card.py already made
    # once, inventing a shape and then seeding tests to match it.
    seen_queries = []

    def narrating_search(query):
        rows = Z._search_with_retry(_z, query)
        seen_queries.append((query, len(rows)))
        print(f"\n  QUERY  {query}")
        print(f"  ->     {len(rows)} ticket(s)")
        for t in rows:
            print(f"           #{getattr(t, 'id', '?'):<10} "
                  f"{_s(getattr(t, 'subject', ''), 58)}")
        return rows

    print("\n" + "=" * 72)
    print(f"THE THREE ROUTES, for booking {bid}")
    print("=" * 72)
    tickets, tally = Z.collect_tickets(bid, narrating_search)

    print("\n" + "=" * 72)
    print("WHAT EACH ROUTE CONTRIBUTED, after dedupe")
    print("=" * 72)
    print(f"  by booking-id field      {tally['fieldvalue']}")
    print(f"  by searching the text    {tally['free_text']}")
    print(f"  by the same requester    {tally['requester']}")
    print(f"  dropped as duplicates    {tally['duplicates']}")
    print(f"  dropped as id collision  {tally['id_collision']}"
          + ("   <- a TICKET numbered like this BOOKING" if tally['id_collision'] else ""))
    if tally["requester_skipped"]:
        print("\n  THE REQUESTER SEARCH DID NOT RUN.")
        print("  It needs an email, and the email is read off whatever the "
              "first two routes\n  found. They found nothing, so there was no "
              "address to search on — and any\n  ticket this guest filed under "
              "a DIFFERENT booking id is invisible to this\n  lookup. That is "
              "a gap in the cascade, not a guest who never wrote in.")

    print(f"\n  TOTAL {len(tickets)} ticket(s) reached shaping.")
    for t in tickets:
        print(f"    #{getattr(t, 'id', '?'):<10} "
              f"{_s(getattr(t, 'subject', ''), 52)}  "
              f"req={_s(getattr(getattr(t, 'requester', None), 'email', ''), 28)}")

    if not tickets:
        print("\n  Downstream, an empty list here is what makes the card say "
              "'No direct\n  interaction found' AND what makes the timeline "
              "fall back to narrating the\n  review. Both are the model "
              "following its instructions on an empty record.")
    else:
        print("\n  These reached shaping. If the card still shows no contact, "
              "the loss is\n  after this point — run trace_contacts.py, which "
              "covers that half.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
