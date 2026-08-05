"""Deleting a scenario chip has to stick, and one scenario renders once.

THE REPORT. The Classification block showed `Refund issues ×` twice under
Scenarios and a third time under Overlays, and clicking × did nothing.

NEITHER HALF WAS AN UNBOUND HANDLER. The chip controls were already delegated
at document level, which is the fix this project reached for the last two times
it hit a dead control. This was the other shape: THREE STORES FOR ONE FACT.

  * `scenarios` is the whole ordered list and ALREADY contains the overlays.
    The card sent `regenerate-rca` the concatenation
    `[...scenarios, ...overlayScenarios]`, so every scenario edit appended the
    overlays a second time — and that endpoint writes the list it is given
    straight back over `d.scenarios`. The chips multiplied on every edit.
  * The same write is why × looked dead. The removal WAS saved, and was then
    overwritten by the union that still contained it, one request later.
  * `primary_scenario` and `overlay_scenarios` were authored independently of
    `scenarios` in two endpoints, so all three could disagree.

DECIDED: THE PRIMARY IS NOT ITS OWN OVERLAY. An overlay is an ADDITIONAL
scenario layered on the primary, and a scenario cannot be additional to
itself — a primary that also sat in the overlays is what put one chip on the
card three times. `scenarios` is the one list, `primary_scenario` is its first
element and `overlay_scenarios` is the rest, both DERIVED. The card renders
the primary in one row and the tail in the other, so the list appears exactly
once.
"""
import pytest

from server.api import settle_scenarios


# ── the settle itself, driven ───────────────────────────────────────────────

def test_a_duplicate_is_dropped_and_order_is_kept():
    scen, primary, overlays = settle_scenarios(
        ["Refund issues", "Refund issues", "Tickets sent late"])
    assert scen == ["Refund issues", "Tickets sent late"]
    assert primary == "Refund issues"
    assert overlays == ["Tickets sent late"]


def test_the_primary_is_never_its_own_overlay():
    _, primary, overlays = settle_scenarios(["Refund issues", "Refund issues"])
    assert primary == "Refund issues"
    assert overlays == [], (
        f"the primary is listed as an overlay of itself: {overlays}")


def test_the_union_the_card_used_to_send_settles_to_one_list():
    """`[...scenarios, ...overlayScenarios]` where scenarios already holds the
    overlays. This exact value is what doubled the list on every edit."""
    scenarios = ["Refund issues", "Tickets sent late"]
    overlays = ["Tickets sent late"]
    scen, primary, ov = settle_scenarios(scenarios + overlays)
    assert scen == ["Refund issues", "Tickets sent late"]
    assert ov == ["Tickets sent late"]
    assert primary == "Refund issues"


def test_empty_stays_empty_and_yields_no_primary():
    scen, primary, overlays = settle_scenarios([])
    assert scen == [] and primary is None and overlays == []


def test_blanks_and_non_strings_are_dropped_not_rendered():
    scen, _, _ = settle_scenarios(["Refund issues", "", None, 7, "  "])
    assert scen == ["Refund issues"]


# ── the wire: what the card is served ───────────────────────────────────────

def _draft(**kw):
    from server.db import RcaDraft
    return RcaDraft(id="d1", review_id="tp_1", **kw)


def test_a_legacy_row_with_duplicates_renders_clean():
    """A draft written by the old build holds the doubled list. It must render
    correctly on the NEXT LOAD, not only after someone edits it again."""
    from server.api import _draft_dict
    out = _draft_dict(_draft(
        scenarios=["Refund issues", "Refund issues", "Tickets sent late"],
        primary_scenario="Refund issues",
        overlay_scenarios=["Refund issues", "Tickets sent late"]))
    assert out["scenarios"] == ["Refund issues", "Tickets sent late"]
    assert out["overlay_scenarios"] == ["Tickets sent late"], (
        f"the primary is still served as its own overlay: "
        f"{out['overlay_scenarios']}")
    assert out["primary_scenario"] == "Refund issues"


def test_the_two_served_keys_cannot_disagree():
    """overlay_scenarios is the tail of scenarios, by construction."""
    from server.api import _draft_dict
    out = _draft_dict(_draft(
        scenarios=["A", "B", "C"],
        primary_scenario="A",
        overlay_scenarios=["STALE — nothing should read this"]))
    assert out["overlay_scenarios"] == out["scenarios"][1:], out


def test_a_draft_with_only_the_scalars_still_yields_a_list():
    """The pre-`scenarios`-column shape. Losing it here would empty the block
    for every old draft."""
    from server.api import _draft_dict
    out = _draft_dict(_draft(primary_scenario="Refund issues",
                             overlay_scenarios=["Tickets sent late"]))
    assert out["scenarios"] == ["Refund issues", "Tickets sent late"]
    assert out["overlay_scenarios"] == ["Tickets sent late"]


# ── the round trip through the real endpoints ───────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import importlib, os
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("MOCK_MODE", "true")
    import server.config as cfg; importlib.reload(cfg)
    import server.db as db; importlib.reload(db); db.init_db()
    import server.api as api; importlib.reload(api)
    import server.main as main; importlib.reload(main)
    from fastapi.testclient import TestClient
    s = db.SessionLocal()
    try:
        s.add(db.Review(id="tp_s", slack_ts="1", slack_channel="C1", rating=1,
                        author="D", body_original="x", status="draft"))
        s.add(db.RcaDraft(id="d_s", review_id="tp_s",
                          l1="Operations Issue", l2="Refund Issues",
                          scenarios=["Refund issues", "Refund issues"],
                          primary_scenario="Refund issues",
                          overlay_scenarios=["Refund issues"]))
        s.commit()
    finally:
        s.close()
    return TestClient(main.app), db


def test_a_patch_that_removes_a_scenario_actually_removes_it(client):
    """The delete, end to end. Before the fix the shortened list was stored and
    then overwritten, so the next GET returned the chip that had just been
    deleted."""
    c, db = client
    got = c.get("/api/reviews/tp_s").json()["draft"]
    assert got["scenarios"] == ["Refund issues"], (
        f"the seeded duplicate did not settle on read: {got['scenarios']}")

    r = c.patch("/api/reviews/tp_s/draft-v2", json={"scenarios": []})
    assert r.status_code == 200, r.text
    after = c.get("/api/reviews/tp_s").json()["draft"]
    assert after["scenarios"] == [], (
        f"the scenario came back after being deleted: {after['scenarios']}")
    assert after["overlay_scenarios"] == []
    assert after["primary_scenario"] == ""


def test_removing_one_of_two_keeps_the_other(client):
    c, _ = client
    c.patch("/api/reviews/tp_s/draft-v2",
            json={"scenarios": ["Refund issues", "Tickets sent late"]})
    c.patch("/api/reviews/tp_s/draft-v2", json={"scenarios": ["Tickets sent late"]})
    after = c.get("/api/reviews/tp_s").json()["draft"]
    assert after["scenarios"] == ["Tickets sent late"], after
    assert after["primary_scenario"] == "Tickets sent late"
    assert after["overlay_scenarios"] == []


def test_the_three_columns_agree_after_a_patch(client):
    """They are three columns describing one list. A patch that leaves them
    disagreeing is how the card got a scenario the list did not have."""
    c, db = client
    c.patch("/api/reviews/tp_s/draft-v2",
            json={"scenarios": ["Refund issues", "Tickets sent late",
                                "Refund issues"]})
    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == "tp_s").first()
        assert d.scenarios == ["Refund issues", "Tickets sent late"], d.scenarios
        assert d.primary_scenario == "Refund issues"
        assert d.overlay_scenarios == ["Tickets sent late"], d.overlay_scenarios
    finally:
        s.close()
