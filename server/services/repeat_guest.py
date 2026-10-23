"""Repeat-reviewer detection. Identity = guest EMAIL, Zendesk ACCOUNT id
(requester_id) and confirmed BOOKING id. NEVER a name, NEVER the PII hash.

Why: BigQuery's fct_bookings has no guest email and no customer id - only
`primary_guest_name`, a PII hash that is not an identity. The guest email lives
in Zendesk (custom field "Customer Email", else requester email) and used to be
computed then discarded; the pipeline now captures it at ticket-match time.

Two reviews are the SAME guest iff they share ANY of: normalised guest_email,
zendesk_requester_id, confirmed_bid. A review with none of the three makes no
claim and is never returned as another's repeat.

Payload states (CLAUDE.md rule 1 - "did not run" must not look like "ran, found
none"):
  not_matched   no confirmed booking, no Zendesk email/account: nothing checked
  booking_only  only the booking id was comparable (no email/account id) and no
                other review shares it
  no_match      keyed by email/account, checked, no other review
  found         at least one other review from this guest
The reason a key is absent is stored on the draft (guest_identity_reason).

All decision logic is pure and tested in tests/test_repeat_guest.py. The client
tile in client/index.html has no JS test harness in this repo (Rule 2 exception).
"""
from __future__ import annotations


def norm_email(v) -> str:
    v = str(v or "").strip().lower()
    return v if "@" in v else ""


def _confirmed_bid(booking, candidate_state=False):
    """(bid, reason). Only a confirmed, verified, actually-read booking counts."""
    b = booking or {}
    bid = str(b.get("id") or "").strip()
    if candidate_state:
        return None, "candidate picker active - booking not confirmed"
    if not bid:
        return None, "no booking id"
    if b.get("_unverified"):
        return None, "booking id not verified against the warehouse"
    if b.get("details_lookup") in ("absent", "failed"):
        return None, "booking record was not read"
    return bid, ""


def stamp_booking(draft) -> None:
    """Confirmation paths (select-candidate, set-booking-id, untraceable, early
    pipeline save). Sets confirmed_bid and CLEARS the Zendesk-derived email and
    account id: they were read off the previous booking's tickets, so they would
    be stale. The pipeline re-run re-captures them via stamp_identity."""
    bid, why = _confirmed_bid(draft.booking, bool(draft.candidate_state))
    draft.confirmed_bid = bid
    draft.guest_email = None
    draft.zendesk_requester_id = None
    draft.guest_identity_reason = (
        "zendesk not yet checked for this booking" if bid else why)


def stamp_identity(draft, zd_identity=None, zd_error=None, zd_searched=True) -> dict:
    """Pipeline capture, after the Zendesk step. `zd_identity` is
    zendesk.guest_identity_from_tickets()'s dict. Email/account id are kept only
    when the booking is confirmed (the tickets were matched on that booking)."""
    bid, why = _confirmed_bid(draft.booking, bool(draft.candidate_state))
    draft.confirmed_bid = bid
    draft.guest_email = draft.zendesk_requester_id = None
    if not bid:
        draft.guest_identity_reason = why
    elif zd_error is not None:
        draft.guest_identity_reason = f"zendesk search failed: {zd_error}"
    elif not zd_searched or zd_identity is None:
        draft.guest_identity_reason = "zendesk not searched"
    else:
        draft.guest_email = norm_email(zd_identity.get("guest_email")) or None
        draft.zendesk_requester_id = str(zd_identity.get("requester_id") or "") or None
        draft.guest_identity_reason = zd_identity.get("reason") or ""
    return {"confirmed_bid": draft.confirmed_bid, "guest_email": draft.guest_email,
            "zendesk_requester_id": draft.zendesk_requester_id,
            "reason": draft.guest_identity_reason}


def guest_key(row: dict) -> dict:
    """The comparable identity of a review row: only non-empty parts."""
    k = {"email": norm_email(row.get("guest_email")),
         "requester_id": str(row.get("zendesk_requester_id") or "").strip(),
         "bid": str(row.get("confirmed_bid") or "").strip()}
    return {n: v for n, v in k.items() if v}


def _sort_key(m):
    return (m["received_at"] is None, m["received_at"] or 0, str(m["id"]))


def find_repeats(me: dict, others: list) -> dict:
    """Pure matcher. Rows: id, guest_email, zendesk_requester_id, confirmed_bid,
    received_at. Returns {state, matches, ordinal, has_key, key_kinds}. Each
    match: the other row plus matched_on (list of 'email'|'requester_id'|
    'booking') and carry_forward_bid. Oldest first, self excluded."""
    mk = guest_key(me)
    if not mk:
        return {"state": "not_matched", "matches": [], "ordinal": None,
                "has_key": False, "key_kinds": []}
    matches = []
    for o in others:
        if o.get("id") == me.get("id"):
            continue
        ok = guest_key(o)
        hit = [n for n in mk if ok.get(n) == mk[n]]
        if not hit:
            continue
        m = dict(o)
        m["matched_on"] = ["booking" if n == "bid" else n for n in hit]
        m["carry_forward_bid"] = o.get("confirmed_bid") or None
        matches.append(m)
    matches.sort(key=_sort_key)
    kinds = sorted(mk)
    base = {"has_key": True, "key_kinds": kinds}
    if matches:
        mine = me.get("received_at")
        earlier = sum(1 for m in matches
                      if m["received_at"] is not None
                      and (mine is None or m["received_at"] <= mine))
        return {**base, "state": "found", "matches": matches,
                "ordinal": earlier + 1}
    state = "booking_only" if kinds == ["bid"] else "no_match"
    return {**base, "state": state, "matches": [], "ordinal": None}
