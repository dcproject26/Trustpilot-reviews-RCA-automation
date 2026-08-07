"""An edit survives a refresh. Only the re-run button replaces it.

THE CONTRACT: every edit saves as it is made; a browser refresh shows it
still there; and the ONLY thing that discards manual edits is re-running the
RCA from the button.

Driven through the real endpoints, because "it saves" is a claim about a
request and nothing else can check it.
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


V3 = {"what_went_wrong": {
        "guest_issues": [{"issue": "Tickets late", "claim": "two hours",
                          "claim_accuracy": "Accurate",
                          "root_cause": "the run failed"}],
        "fixes": [{"action": "Alert on failure", "owner": "TECH"}],
        "case_findings": [{"text": "Tickets sent 14:02", "source": "bms"}]},
      "flags": [{"team": "CO", "flag": "No follow-up"}],
      "dss": {"prescribes": "Resend", "followed": "not_followed"}}


def _seed(live_db, rid="tp_edit"):
    s = live_db.SessionLocal()
    s.add(live_db.Review(id=rid, rating=1, author="A", body_original="b",
                         status="draft"))
    s.add(live_db.RcaDraft(id=f"d_{rid}", review_id=rid, rca_v3=dict(V3)))
    s.commit(); s.close()
    return rid


def _get(client, rid):
    r = client.get(f"/api/reviews/{rid}")
    assert r.status_code == 200, r.text
    return r.json()["draft"]


def _edit(client, rid, mutate):
    """Read, change one field, PATCH — exactly what a blur handler does."""
    v3 = dict(_get(client, rid)["rca_v3"])
    mutate(v3)
    r = client.patch(f"/api/reviews/{rid}/draft-v2", json={"rca_v3": v3})
    assert r.status_code == 200, r.text
    return r.json()["draft"]


def _issue(d):
    return d["rca_v3"]["what_went_wrong"]["guest_issues"][0]


# ── it saves, and it comes back ────────────────────────────────────────────

def test_an_edited_root_cause_survives_a_refresh(live_db, client):
    rid = _seed(live_db)

    def m(v3):
        v3["what_went_wrong"]["guest_issues"][0]["root_cause"] = "I typed this"
    _edit(client, rid, m)
    # The refresh: a fresh GET, which is all a reload does.
    assert _issue(_get(client, rid))["root_cause"] == "I typed this"


def test_an_edited_verdict_survives(live_db, client):
    rid = _seed(live_db, "tp_edit_v")

    def m(v3):
        v3["what_went_wrong"]["guest_issues"][0]["claim_accuracy"] = "Inaccurate"
    _edit(client, rid, m)
    assert _issue(_get(client, rid))["claim_accuracy"] == "Inaccurate"


def test_an_added_case_finding_survives(live_db, client):
    rid = _seed(live_db, "tp_edit_cf")

    def m(v3):
        v3["what_went_wrong"]["case_findings"].append(
            {"text": "I added this", "source": None})
    _edit(client, rid, m)
    got = _get(client, rid)["rca_v3"]["what_went_wrong"]["case_findings"]
    assert any(f["text"] == "I added this" for f in got), got


def test_a_deleted_issue_stays_deleted(live_db, client):
    """A splice that does not reach the server comes back on the next render,
    which reads as the delete having failed silently."""
    rid = _seed(live_db, "tp_edit_del")

    def m(v3):
        v3["what_went_wrong"]["guest_issues"] = []
    _edit(client, rid, m)
    assert _get(client, rid)["rca_v3"]["what_went_wrong"]["guest_issues"] == []


def test_an_edited_fix_owner_moves_its_action_row_too(live_db, client):
    """Actions Taken is a VIEW over the fixes, so an owner edit has to move
    both or the card and the Slack post disagree."""
    rid = _seed(live_db, "tp_edit_own")

    def m(v3):
        v3["what_went_wrong"]["fixes"][0]["owner"] = "CO"
    d = _edit(client, rid, m)
    # CO carries the re-owned fix AND its own flag — Actions Taken has two
    # sources by design, so this asserts the fix MOVED rather than that CO
    # holds nothing else.
    assert "Alert on failure" in d["actions_taken"]["co"], d["actions_taken"]
    assert d["actions_taken"]["tech"] == [], d["actions_taken"]


def test_two_edits_in_a_row_both_land(live_db, client):
    """The second PATCH reads what the first stored; a client sending a stale
    blob would silently revert the first edit."""
    rid = _seed(live_db, "tp_edit_two")
    _edit(client, rid, lambda v3: v3["what_went_wrong"]["guest_issues"][0]
          .__setitem__("root_cause", "first"))
    _edit(client, rid, lambda v3: v3["what_went_wrong"]["guest_issues"][0]
          .__setitem__("issue", "second"))
    got = _issue(_get(client, rid))
    assert got["root_cause"] == "first" and got["issue"] == "second", got


def test_the_edit_is_marked_so_a_bulk_rerun_can_respect_it(live_db, client):
    """`rca_v3_edited_at` is what tells a sweep this draft was touched by a
    person. Unset, a bulk re-run would overwrite hand-written analysis without
    anything recording that it had."""
    rid = _seed(live_db, "tp_edit_mark")
    s = live_db.SessionLocal()
    before = s.query(live_db.RcaDraft).filter_by(review_id=rid).first().rca_v3_edited_at
    s.close()
    assert before is None
    _edit(client, rid, lambda v3: v3.__setitem__("stated_issue", "x"))
    s = live_db.SessionLocal()
    after = s.query(live_db.RcaDraft).filter_by(review_id=rid).first().rca_v3_edited_at
    s.close()
    assert after is not None, "an edit left no trace that a person made it"


def test_reading_a_draft_never_rewrites_it(live_db, client):
    """A GET that recomputes would quietly undo an edit on every refresh —
    the exact failure this file exists to rule out."""
    rid = _seed(live_db, "tp_edit_ro")
    _edit(client, rid, lambda v3: v3["what_went_wrong"]["guest_issues"][0]
          .__setitem__("root_cause", "mine"))
    for _ in range(3):
        assert _issue(_get(client, rid))["root_cause"] == "mine"
