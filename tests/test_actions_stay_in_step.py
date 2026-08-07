"""Editing a fix's owner moves the Actions Taken tab and the Slack post with it.

THE DRIFT THIS GUARDS. §3's fixes live in `rca_v3`; `actions_taken` is a
COLUMN, and Slack reads that column. `PATCH /draft-v2` writes `rca_v3` raw and
never re-runs validate, so changing an owner on the card used to move the fix
and leave the column behind — the card showing the new routing and the Slack
post carrying the old one, with nothing to say they disagreed.

Driven through the endpoint, because the whole failure was that a write path
existed which no test walked.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(live_db):
    from server.main import app
    from server.db import get_session
    app.dependency_overrides[get_session] = lambda: live_db.SessionLocal()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed(live_db, rid="tp_sync"):
    s = live_db.SessionLocal()
    s.add(live_db.Review(id=rid, rating=1, author="A", body_original="b",
                         status="draft"))
    s.add(live_db.RcaDraft(id=f"d_{rid}", review_id=rid, rca_v3={
        "what_went_wrong": {"guest_issues": [],
                            "fixes": [{"action": "Resend the tickets",
                                       "owner": "CO"}]},
        "flags": []},
        actions_taken={"co": ["Resend the tickets"]}))
    s.commit(); s.close()
    return rid


def _patch(client, rid, blob):
    r = client.patch(f"/api/reviews/{rid}/draft-v2", json={"rca_v3": blob})
    assert r.status_code == 200, r.text
    return r.json()["draft"]


def test_changing_a_fix_owner_moves_the_action_row(live_db, client):
    rid = _seed(live_db)
    draft = _patch(client, rid, {
        "what_went_wrong": {"guest_issues": [],
                            "fixes": [{"action": "Resend the tickets",
                                       "owner": "TECH"}]},
        "flags": []})
    assert draft["actions_taken"]["tech"] == ["Resend the tickets"], \
        draft["actions_taken"]
    assert draft["actions_taken"]["co"] == [], \
        "the row stayed on the old tab — the column drifted from the fix"


def test_the_column_the_slack_post_reads_is_the_one_that_moved(live_db, client):
    """slack.py reads draft.actions_taken. A card-only update would post the
    old routing."""
    rid = _seed(live_db)
    _patch(client, rid, {
        "what_went_wrong": {"guest_issues": [],
                            "fixes": [{"action": "Resend the tickets",
                                       "owner": "TECH"}]},
        "flags": []})
    s = live_db.SessionLocal()
    row = s.query(live_db.RcaDraft).filter_by(review_id=rid).first()
    got = dict(row.actions_taken or {})
    s.close()
    assert got.get("tech") == ["Resend the tickets"], got


def test_removing_a_fix_empties_its_tab(live_db, client):
    """A deleted fix that left its action row behind would show work nobody
    is doing."""
    rid = _seed(live_db)
    draft = _patch(client, rid, {"what_went_wrong": {"guest_issues": [],
                                                     "fixes": []},
                                 "flags": []})
    assert all(v == [] for v in draft["actions_taken"].values()), \
        draft["actions_taken"]


def test_an_unowned_fix_lands_on_the_unrouted_tab(live_db, client):
    rid = _seed(live_db)
    draft = _patch(client, rid, {
        "what_went_wrong": {"guest_issues": [],
                            "fixes": [{"action": "Someone should look at this"}]},
        "flags": []})
    assert draft["actions_taken"]["unrouted"] == ["Someone should look at this"]


def test_a_flag_does_not_reach_a_team_tab(live_db, client):
    """REMOVED BY REQUEST. Routing flags in here produced rows like "No
    Headout process required monitoring SP-initiated time-change
    communications" and "Nobody was required to contact the guest proactively"
    — the ABSENCE of an action, filed under Actions Taken and formatted
    identically to a row someone had performed.

    A flag is what went wrong. It is already a finding in the Flags section
    and does not need a second home."""
    rid = _seed(live_db)
    draft = _patch(client, rid, {
        "what_went_wrong": {"guest_issues": [], "fixes": []},
        "flags": [{"team": "TECH", "flag": "No alert on failed fulfilment"}]})
    assert draft["actions_taken"]["tech"] == [], draft["actions_taken"]


def test_the_client_cannot_write_the_column_behind_the_fixes(live_db, client):
    """A second writer is how the two drift. Whatever the client sends for
    actions_taken in the same request is overwritten by the regroup."""
    rid = _seed(live_db)
    r = client.patch(f"/api/reviews/{rid}/draft-v2", json={
        "rca_v3": {"what_went_wrong": {"guest_issues": [],
                                       "fixes": [{"action": "Resend the tickets",
                                                  "owner": "TECH"}]},
                   "flags": []},
        "actions_taken": {"finance": ["a row the client invented"]}})
    assert r.status_code == 200, r.text
    got = r.json()["draft"]["actions_taken"]
    assert got["tech"] == ["Resend the tickets"], got
    assert got["finance"] == [], "the client's array survived the regroup"
