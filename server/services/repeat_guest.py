"""Repeat-reviewer detection, keyed on the CONFIRMED booking's guest.

NOT BY NAME. A review's display name is not an identity (too many guests share
one), and a review can only be called a repeat after its booking id is
confirmed, because only then do we know who the guest on the booking is. So:

  * a review with no confirmed BID makes NO repeat claim, and is never returned
    as anyone else's repeat;
  * two reviews are the same guest when their confirmed bookings share a guest
    key (a guest email if the booking ever exposes one, else the PII hash in
    fct_bookings.primary_guest_name), or share the same booking id.

WHICH IDENTITY FIELDS EXIST (verified in code). `verify_bid` returns
`primary_guest_name` (a PII hash on a large share of rows). The only email on
the booking path, `escalation_email`, comes off dim_vendors.contact_details -
the SUPPLIER's contact, not the guest's - so it is deliberately NOT used. A
`guest_email` key on the booking dict is honoured if a query ever supplies one.
A plain (non-hashed) primary_guest_name is a display name, so it is not a key.

States (CLAUDE.md rule 1 - "did not run" must not look like "ran, found none"):
  not_confirmed  no confirmed BID: nothing was checked
  no_guest_key   BID confirmed but no guest key, and no same-booking match:
                 only the booking id could be compared
  none           key present, checked, no other review by this guest
  found          at least one other review by this guest

All decision logic is pure and driven by tests/test_repeat_guest.py. The
client panel in client/index.html has no JS test harness in this repo.
"""
from __future__ import annotations

from server.names import looks_like_digest


def guest_key_from_booking(booking, candidate_state: bool = False) -> dict:
    """Resolve the durable guest identity of a CONFIRMED booking.

    Returns {confirmed_bid, guest_key, guest_email, guest_name_hash, reason}.
    Unconfirmed (no id, candidate picker active, unverified id) yields all
    None and a reason - never a key.
    """
    out = {"confirmed_bid": None, "guest_key": None, "guest_email": None,
           "guest_name_hash": None, "reason": ""}
    b = booking or {}
    bid = str(b.get("id") or "").strip()
    if candidate_state:
        out["reason"] = "candidate picker active - booking not confirmed"
        return out
    if not bid:
        out["reason"] = "no booking id"
        return out
    if b.get("_unverified"):
        out["reason"] = "booking id not verified against the warehouse"
        return out
    if b.get("details_lookup") in ("absent", "failed"):
        out["reason"] = "booking record was not read"
        return out
    out["confirmed_bid"] = bid
    email = str(b.get("guest_email") or "").strip().lower()
    nm = str(b.get("primary_guest_name") or "").strip()
    if email and "@" in email:
        out["guest_email"] = email
        out["guest_key"] = "email:" + email
    if nm and looks_like_digest(nm):
        out["guest_name_hash"] = nm
        if not out["guest_key"]:
            out["guest_key"] = "hash:" + nm
    if not out["guest_key"]:
        out["reason"] = ("booking exposes no guest email and a non-hashed "
                         "guest name, which is not an identity")
    return out


def stamp_guest_key(draft) -> dict:
    """Write the resolved key onto an RcaDraft (clears it when unconfirmed)."""
    r = guest_key_from_booking(draft.booking, bool(draft.candidate_state))
    draft.confirmed_bid = r["confirmed_bid"]
    draft.guest_key = r["guest_key"]
    draft.guest_email = r["guest_email"]
    draft.guest_name_hash = r["guest_name_hash"]
    return r


def _sort_key(m):
    return (m["received_at"] is None, m["received_at"] or 0, str(m["id"]))


def find_repeats(me: dict, others: list) -> dict:
    """Pure matcher. `me`/`others` are dicts with id, guest_key, confirmed_bid,
    received_at (comparable or None). Returns {state, matches, ordinal}.
    Each match: the other row plus matched_on ('guest'|'booking') and
    carry_forward_bid (its confirmed BID). Oldest first.
    """
    bid, key = me.get("confirmed_bid"), me.get("guest_key")
    if not bid:
        return {"state": "not_confirmed", "matches": [], "ordinal": None,
                "has_guest_key": False}
    matches = []
    for o in others:
        if o.get("id") == me.get("id") or not o.get("confirmed_bid"):
            continue
        same_key = bool(key) and o.get("guest_key") == key
        same_bid = o["confirmed_bid"] == bid
        if not (same_key or same_bid):
            continue
        m = dict(o)
        m["matched_on"] = "guest" if same_key else "booking"
        m["carry_forward_bid"] = o["confirmed_bid"]
        matches.append(m)
    matches.sort(key=_sort_key)
    if matches:
        mine = me.get("received_at")
        earlier = sum(1 for m in matches
                      if m["received_at"] is not None
                      and (mine is None or m["received_at"] <= mine))
        return {"state": "found", "matches": matches, "ordinal": earlier + 1,
                "has_guest_key": bool(key)}
    return {"state": "none" if key else "no_guest_key", "matches": [],
            "ordinal": None, "has_guest_key": bool(key)}
