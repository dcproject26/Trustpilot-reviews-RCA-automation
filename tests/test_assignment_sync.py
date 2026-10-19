"""Case assignment sync — dashboard <-> Slack :wip: — driven at the logic AND
the DB level, covering every edge case we agreed must be airtight:

  * first-picker-wins (a :wip: does not steal an owned case)
  * the bot's own :wip: (from a dashboard claim) does not loop into a 2nd claim
  * a reaction on a review we do not have is a named no-op, not a silent drop
  * an unmapped reactor falls back to a display name (and is logged)
  * reacting on a solved case is ignored
  * un-reacting releases back to the pool only if it was that person's
  * a name change / release reflects (picked_up_at set on claim, cleared on
    release)

The pure functions are unit-tested without a DB or Slack; the webhook handler
is driven against the live schema with the Slack id->name roster that ships in
content/orm_macros.yaml.
"""
from datetime import datetime

import pytest

from server.services import assignment as A


class _Rev:
    """A minimal stand-in for a Review row, for the pure-logic tests."""
    def __init__(self, owner=None, at=None, status="draft"):
        self.picked_up_by = owner
        self.picked_up_at = at
        self.status = status
        self.id = "tp_x"
        self.slack_channel = "C0ORM"
        self.slack_ts = "1.1"


# ── pure claim/release/reassign logic ───────────────────────────────────────

def test_claim_an_unowned_case_sets_owner_and_time():
    r = _Rev()
    assert A.apply_claim(r, "Devshree") == "claimed"
    assert r.picked_up_by == "Devshree" and r.picked_up_at is not None


def test_claim_is_first_picker_wins():
    r = _Rev(owner="Devshree", at=datetime(2026, 9, 1))
    assert A.apply_claim(r, "Avi") == "already_owned"
    assert r.picked_up_by == "Devshree"            # NOT stolen
    assert r.picked_up_at == datetime(2026, 9, 1)  # original time kept


def test_reclaiming_your_own_case_does_not_reset_the_clock():
    t = datetime(2026, 9, 1)
    r = _Rev(owner="Devshree", at=t)
    assert A.apply_claim(r, "Devshree") == "already_mine"
    assert r.picked_up_at == t                      # idempotent


def test_a_claim_with_no_name_is_not_a_claim():
    r = _Rev()
    assert A.apply_claim(r, "") == "already_owned"
    assert r.picked_up_by is None


def test_release_force_clears_always():
    r = _Rev(owner="Devshree", at=datetime(2026, 9, 1))
    assert A.apply_release(r, None, force=True) == "released"
    assert r.picked_up_by == "" and r.picked_up_at is None


def test_release_without_force_only_clears_your_own():
    r = _Rev(owner="Devshree", at=datetime(2026, 9, 1))
    assert A.apply_release(r, "Avi", force=False) == "kept"   # not yours
    assert r.picked_up_by == "Devshree"
    assert A.apply_release(r, "Devshree", force=False) == "released"
    assert r.picked_up_by == "" and r.picked_up_at is None
def test_review_id_matches_the_ingest_format():
    assert A.review_id_for_ts("1743500000.000100") == "tp_1743500000_000100"


def test_roster_slack_id_maps_to_name_and_unknown_is_flagged():
    name, mapped = A.reviewer_for_slack_id("U02B2KJF2T0")   # Devshree in the roster
    assert name == "Devshree" and mapped is True
    name, mapped = A.reviewer_for_slack_id("U_NOT_ON_ROSTER")
    assert name is None and mapped is False


# ── the webhook handler, against the live schema ────────────────────────────

def _review(db, rid="tp_1743500000_000100", ts="1743500000.000100", status="draft", owner=None):
    from server.db import Review
    s = db.SessionLocal()
    try:
        s.add(Review(id=rid, slack_ts=ts, slack_channel="C0ORM", rating=1,
                     status=status, picked_up_by=owner, body_original="x"))
        s.commit()
    finally:
        s.close()


def _event(kind, ts="1743500000.000100", user="U02B2KJF2T0", emoji="wip"):
    return {"type": kind, "user": user, "reaction": emoji,
            "item": {"type": "message", "channel": "C0ORM", "ts": ts}}


@pytest.fixture(autouse=True)
def _no_real_bot(monkeypatch):
    # No Slack in tests: the loop-guard bot id is a known value, and display-name
    # lookups return a stub. Slack side-effects are no-ops.
    monkeypatch.setattr(A, "_BOT_UID", "UBOTSELF")
    monkeypatch.setattr(A, "_display_name", lambda uid: "Display Person")
    yield


def test_wip_reaction_claims_the_case(live_db):
    _review(live_db)
    s = live_db.SessionLocal()
    try:
        out = A.handle_reaction_event(_event("reaction_added"), s)
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert out["action"] == "claimed"
    assert r.picked_up_by == "Devshree" and r.picked_up_at is not None


def test_the_bots_own_wip_does_not_loop_into_a_claim(live_db):
    _review(live_db)
    s = live_db.SessionLocal()
    try:
        out = A.handle_reaction_event(_event("reaction_added", user="UBOTSELF"), s)
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert out["ignored"] == "own_reaction"
    assert r.picked_up_by is None                  # the bot did not assign anyone


def test_wip_on_a_solved_case_is_ignored(live_db):
    _review(live_db, status="sent")
    s = live_db.SessionLocal()
    try:
        out = A.handle_reaction_event(_event("reaction_added"), s)
    finally:
        s.close()
    assert out["ignored"] == "already_solved"


def test_wip_on_an_already_owned_case_is_first_picker_wins(live_db):
    _review(live_db, owner="Avi")
    s = live_db.SessionLocal()
    try:
        out = A.handle_reaction_event(_event("reaction_added"), s)   # Devshree reacts
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert out["action"] == "already_owned"
    assert r.picked_up_by == "Avi"                 # Devshree did not steal it


def test_removing_your_wip_releases_the_case(live_db):
    _review(live_db, owner="Devshree")
    s = live_db.SessionLocal()
    try:
        out = A.handle_reaction_event(_event("reaction_removed"), s)  # Devshree un-reacts
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert out["action"] == "released"
    assert (r.picked_up_by or "") == "" and r.picked_up_at is None   # back in the pool


def test_removing_someone_elses_wip_does_not_release(live_db):
    _review(live_db, owner="Avi")
    s = live_db.SessionLocal()
    try:
        # Devshree removes HER reaction; Avi still owns it.
        out = A.handle_reaction_event(_event("reaction_removed"), s)
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert out["action"] == "kept"
    assert r.picked_up_by == "Avi"


def test_removing_wip_on_a_solved_case_keeps_the_owner(live_db):
    # Finished work keeps its attribution — un-reacting :wip: on a sent case must
    # not blank out who handled it (symmetric with the reaction_added guard).
    _review(live_db, owner="Devshree", status="sent")
    s = live_db.SessionLocal()
    try:
        out = A.handle_reaction_event(_event("reaction_removed"), s)  # owner un-reacts
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert out["ignored"] == "already_solved"
    assert r.picked_up_by == "Devshree"            # attribution survives


def test_re_saving_the_same_owner_does_not_re_mirror(client, live_db, monkeypatch):
    # Saving the owner the case already has is a no-op on Slack — no duplicate
    # "Picked up by" thread note each time the dashboard re-PATCHes the same name.
    _review(live_db, owner="Shruti")
    calls = {"add": 0, "remove": 0}
    monkeypatch.setattr(A, "mirror_claim_to_slack", lambda r, n: calls.__setitem__("add", calls["add"] + 1))
    monkeypatch.setattr(A, "mirror_release_to_slack", lambda r: calls.__setitem__("remove", calls["remove"] + 1))
    out = client.patch("/api/reviews/tp_1743500000_000100/picked-up-by",
                       json={"name": "Shruti"}).json()
    assert out["picked_up_by"] == "Shruti"
    assert calls["add"] == 0 and calls["remove"] == 0   # unchanged owner, no mirror


def test_reaction_on_a_review_we_dont_have_is_a_named_noop(live_db):
    s = live_db.SessionLocal()
    try:
        out = A.handle_reaction_event(_event("reaction_added", ts="9999.0001"), s)
    finally:
        s.close()
    assert out["ignored"] == "no_such_review"      # said, not silently zero


def test_a_non_wip_reaction_is_ignored(live_db):
    _review(live_db)
    s = live_db.SessionLocal()
    try:
        out = A.handle_reaction_event(_event("reaction_added", emoji="eyes"), s)
    finally:
        s.close()
    assert out["ignored"] == "not_wip"


def test_an_unmapped_reactor_falls_back_to_a_display_name(live_db):
    _review(live_db)
    s = live_db.SessionLocal()
    try:
        out = A.handle_reaction_event(_event("reaction_added", user="U_OFF_ROSTER"), s)
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert out["action"] == "claimed"
    assert r.picked_up_by == "Display Person"      # not dropped — named + logged


# ── the dashboard PATCH endpoint mirrors to Slack ───────────────────────────

def test_dashboard_patch_sets_owner_time_and_mirrors(client, live_db, monkeypatch):
    _review(live_db, owner="Avi")        # already owned — the roster overrides
    calls = {"add": 0, "remove": 0}
    monkeypatch.setattr(A, "mirror_claim_to_slack", lambda r, n: calls.__setitem__("add", calls["add"] + 1))
    monkeypatch.setattr(A, "mirror_release_to_slack", lambda r: calls.__setitem__("remove", calls["remove"] + 1))
    out = client.patch("/api/reviews/tp_1743500000_000100/picked-up-by",
                       json={"name": "Shruti"}).json()
    assert out["picked_up_by"] == "Shruti" and out["picked_up_at"]   # overrode Avi
    assert calls["add"] == 1 and calls["remove"] == 0
    # now release
    out = client.patch("/api/reviews/tp_1743500000_000100/picked-up-by",
                       json={"name": ""}).json()
    assert (out["picked_up_by"] or "") == "" and out["picked_up_at"] is None
    assert calls["remove"] == 1


# ── the POLL reconcile: recover :wip: claims the webhook never delivered ─────
#
# The webhook path above fires only when Slack delivers the reaction event.
# When it does not (no reactions:read scope, no reaction_added subscription, a
# push outage), the claim lives only in the channel's history. These drive
# reconcile_wip_from_messages against the live schema with the real roster, so
# the behaviour — not the wording — is what is pinned (CLAUDE.md rule 2).

def _msg(ts="1743500000.000100", wip_users=("U02B2KJF2T0",), other=None):
    """A conversations.history message carrying a :wip: reaction, shaped like
    Slack's payload (reactions: [{name, users, count}])."""
    reactions = []
    if wip_users is not None:
        reactions.append({"name": "wip", "users": list(wip_users), "count": len(wip_users)})
    if other:
        reactions.append(other)
    return {"ts": ts, "reactions": reactions}


def test_poll_reconcile_claims_an_unowned_review_from_history(live_db):
    _review(live_db)                                   # unowned
    s = live_db.SessionLocal()
    try:
        out = A.reconcile_wip_from_messages(s, [_msg()])
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert out == {"examined": 1, "claimed": 1}
    assert r.picked_up_by == "Devshree" and r.picked_up_at is not None


def test_poll_reconcile_is_first_picker_wins_and_never_steals(live_db):
    _review(live_db, owner="Avi")                      # already owned
    s = live_db.SessionLocal()
    try:
        out = A.reconcile_wip_from_messages(s, [_msg(wip_users=("U02B2KJF2T0",))])
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert out == {"examined": 1, "claimed": 0}         # looked, changed nothing
    assert r.picked_up_by == "Avi"                      # NOT stolen


def test_poll_reconcile_ignores_the_bots_own_wip(live_db):
    """The bot adds :wip: when the dashboard claims; on an unowned review that
    the bot alone reacted to, reconcile must not invent a claim."""
    _review(live_db)
    s = live_db.SessionLocal()
    try:
        out = A.reconcile_wip_from_messages(s, [_msg(wip_users=("UBOTSELF",))])
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert out == {"examined": 0, "claimed": 0}
    assert (r.picked_up_by or "") == ""


def test_poll_reconcile_takes_the_first_human_past_the_bot(live_db):
    """Bot reacted first (dashboard mirror), a human after — the human is the
    claimant, and the bot in users[0] must not shadow them."""
    _review(live_db)
    s = live_db.SessionLocal()
    try:
        A.reconcile_wip_from_messages(s, [_msg(wip_users=("UBOTSELF", "U02B2KJF2T0"))])
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert r.picked_up_by == "Devshree"


def test_poll_reconcile_picks_the_earliest_of_two_humans(live_db):
    """Two humans reacted; the FIRST in Slack's order wins (first-picker-wins).
    With one human in the list first==last, so this needs two to pin that the
    EARLIEST is taken, not whoever happens to be at the end."""
    _review(live_db)
    s = live_db.SessionLocal()
    try:
        # Devshree reacted first, an off-roster person after.
        A.reconcile_wip_from_messages(
            s, [_msg(wip_users=("U02B2KJF2T0", "U_OFF_ROSTER"))])
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert r.picked_up_by == "Devshree"      # the first reactor, not "Display Person"


def test_poll_reconcile_leaves_a_solved_case_alone(live_db):
    _review(live_db, status="sent")                    # solved
    s = live_db.SessionLocal()
    try:
        out = A.reconcile_wip_from_messages(s, [_msg()])
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert out["claimed"] == 0
    assert (r.picked_up_by or "") == ""                # finished work not re-picked


def test_poll_reconcile_no_wip_reaction_is_a_clean_noop(live_db):
    _review(live_db)
    s = live_db.SessionLocal()
    try:
        # A message with a different emoji, and one with no reactions at all.
        out = A.reconcile_wip_from_messages(s, [
            _msg(wip_users=None, other={"name": "eyes", "users": ["U02B2KJF2T0"], "count": 1}),
            {"ts": "1743500000.000100"}])
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert out == {"examined": 0, "claimed": 0}
    assert (r.picked_up_by or "") == ""


def test_poll_reconcile_names_an_unmapped_reactor_rather_than_dropping(live_db):
    _review(live_db)
    s = live_db.SessionLocal()
    try:
        A.reconcile_wip_from_messages(s, [_msg(wip_users=("U_OFF_ROSTER",))])
        from server.db import Review
        r = s.query(Review).filter_by(id="tp_1743500000_000100").one()
    finally:
        s.close()
    assert r.picked_up_by == "Display Person"          # fallback, not dropped


def test_poll_reconcile_skips_a_message_with_no_review_row(live_db):
    """A :wip: on a message we never ingested is not a claim target — examined
    counts only reviews that exist, so a busy channel does not inflate it."""
    s = live_db.SessionLocal()
    try:
        out = A.reconcile_wip_from_messages(s, [_msg(ts="9999999999.000000")])
    finally:
        s.close()
    assert out == {"examined": 0, "claimed": 0}
