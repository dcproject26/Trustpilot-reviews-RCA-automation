"""The override survives a real PATCH, and the card is told where it stands.

`test_scenario_override.py` drives the reconcile logic as a pure function.
This drives it through the endpoints, because the logic being right proves
nothing about whether the patch handler records provenance or whether the
draft payload carries it — and a rule that is correct and unwired is this
project's most repeated bug.

The specific thing at stake: setting a scenario has to be recorded as a
JUDGEMENT, not just written. If it is written without provenance, the next
L1/L2 correction re-routes over it and leaves no trace that a judgement was
ever made.
"""
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp.name}")
    monkeypatch.setenv("MOCK_MODE", "true")
    import importlib
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()
    import server.api as api
    importlib.reload(api)
    import server.main as main
    importlib.reload(main)

    from datetime import datetime
    s = db.SessionLocal()
    s.query(db.RcaDraft).delete()
    s.query(db.Review).delete()
    s.add(db.Review(id="tp_so", slack_ts="1", slack_channel="C1", rating=1,
                    author="A", body_original="x", status="draft",
                    received_at=datetime.utcnow()))
    s.add(db.RcaDraft(id="d_so", review_id="tp_so",
                      l1="Operations Issue", l2="Meeting Point Issues",
                      primary_scenario="Meeting point issues",
                      scenarios=["Meeting point issues"],
                      overlay_scenarios=[], booking={"id": "1"},
                      rca_v3={"what_went_wrong": {"guest_issues": []}},
                      generated_at=datetime.utcnow()))
    s.commit()
    s.close()
    yield TestClient(main.app)


def _draft(client):
    r = client.get("/api/reviews/tp_so")
    assert r.status_code == 200, r.text
    got = r.json().get("draft")
    assert got, "the review has no draft — the fixture did not seed one"
    return got


def _patch(client, **body):
    r = client.patch("/api/reviews/tp_so/draft-v2", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ── the payload carries the comparison ─────────────────────────────────────

def test_the_payload_carries_the_routing_block(client):
    got = _draft(client).get("scenario_routing")
    assert got, "the card has no way to tell an override from a routed value"
    for k in ("primary", "routed_now", "source", "diverged", "overlays",
              "effective", "uncovered"):
        assert k in got, k


def test_a_fresh_draft_reads_as_routed_not_overridden(client):
    got = _draft(client)["scenario_routing"]
    assert got["source"] == "routed"
    assert got["diverged"] is False, \
        "a draft nobody touched is showing a reconcile prompt"


# ── setting a scenario records a judgement ─────────────────────────────────

def test_setting_a_scenario_marks_it_as_hand_set(client):
    _patch(client, primary_scenario="Guest error")
    got = _draft(client)["scenario_routing"]
    assert got["source"] == "manual", (
        "the scenario was written with no provenance — the next L1/L2 change "
        "will re-route over it and leave no trace a judgement was made")
    assert got["primary"] == "Guest error"


def test_the_override_survives_a_classification_change(client):
    _patch(client, primary_scenario="Guest error")
    _patch(client, l1="External Factor", l2="Customer Late")
    got = _draft(client)["scenario_routing"]
    assert got["primary"] == "Guest error", \
        "correcting the classification discarded the override"


def test_the_change_surfaces_the_disagreement(client):
    """The refinement asked for: the comparison fires the moment L1/L2 moves,
    while the person still remembers why they set it."""
    _patch(client, primary_scenario="Guest error")
    _patch(client, l1="Operations Issue", l2="Venue closure")
    got = _draft(client)["scenario_routing"]
    assert got["diverged"] is True
    assert got["routed_now"] == "Venue closure (weather/strike)", got
    assert got["primary"] == "Guest error"


def test_an_override_that_agrees_with_routing_is_quiet(client):
    """"Override matches what routing would now produce → nothing to
    reconcile." """
    _patch(client, primary_scenario="Venue closure (weather/strike)")
    _patch(client, l1="Operations Issue", l2="Venue closure")
    got = _draft(client)["scenario_routing"]
    assert got["diverged"] is False, \
        "a badge is firing where there is nothing to reconcile"


# ── a routed primary still follows the classification ──────────────────────

def test_a_routed_primary_re_routes_when_the_classification_changes(client):
    """Without this an override would be indistinguishable from a stale value
    and nothing would ever re-route."""
    _patch(client, l1="External Factor", l2="Customer Late")
    got = _draft(client)
    assert got["primary_scenario"] == "Guest error", got["primary_scenario"]
    assert got["scenario_routing"]["source"] == "routed"


def test_the_re_route_reaches_the_stored_scalar_not_just_the_payload(client):
    """The prompt, the DSS lookup and the Slack post all read the stored
    scalar. A re-route computed for display only would leave every one of
    them carrying the scenario for the OLD classification."""
    _patch(client, l1="External Factor", l2="Customer Late")
    assert _draft(client)["primary_scenario"] == "Guest error"
    assert "Guest error" in _draft(client)["scenarios"]


def test_reverting_clears_the_override(client):
    """One-click revert: set it back to what routing says, and the draft
    should stop reporting a disagreement."""
    _patch(client, primary_scenario="Guest error")
    _patch(client, l1="Operations Issue", l2="Venue closure")
    assert _draft(client)["scenario_routing"]["diverged"] is True
    _patch(client, primary_scenario="Venue closure (weather/strike)")
    got = _draft(client)["scenario_routing"]
    assert got["diverged"] is False
    assert got["primary"] == "Venue closure (weather/strike)"


# ── overlays ───────────────────────────────────────────────────────────────

def test_an_override_does_not_stop_a_fact_driven_overlay(client):
    """Overlays read booking FACTS. Overriding the primary says how to read
    the case; it does not claim the booking was not cancelled."""
    from datetime import datetime
    import server.db as db
    s = db.SessionLocal()
    d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == "tp_so").first()
    d.booking = {"id": "1", "booking_status": "CANCELLED"}
    s.commit(); s.close()

    _patch(client, primary_scenario="Guest error")
    got = _draft(client)["scenario_routing"]
    assert got["primary"] == "Guest error"
    assert "Unfulfilled booking" in got["overlays"], got["overlays"]


def test_the_effective_list_leads_with_the_primary(client):
    _patch(client, primary_scenario="Guest error")
    got = _draft(client)["scenario_routing"]
    assert got["effective"][0] == "Guest error"


# ── rule 13 coverage after an override ─────────────────────────────────────

def test_a_scenario_no_guest_issue_covers_is_reported(client):
    """Output rule 13 guarantees coverage at generation time. An override
    applied afterwards breaks it silently in one of two directions; this is
    the one a reader can act on."""
    _patch(client, primary_scenario="Guest error")
    got = _draft(client)["scenario_routing"]
    assert "Guest error" in got["uncovered"], got


def test_a_covered_scenario_is_not_reported(client):
    _patch(client, rca_v3={"what_went_wrong": {"guest_issues": [
        {"issue": "Guest error on the booking date",
         "root_cause": "Guest picked the wrong slot"}]}})
    _patch(client, primary_scenario="Guest error")
    got = _draft(client)["scenario_routing"]
    assert "Guest error" not in got["uncovered"], got
