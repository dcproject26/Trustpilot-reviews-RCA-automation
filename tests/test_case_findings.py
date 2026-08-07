"""§1: the booking's story, evidenced — one ordered, deduplicated list.

WHAT THIS REPLACES. Evidence rows were per-issue, so a fact cited by two
claims rendered twice. That was the single biggest source of repeated text on
the card. They keep their claim association in the data — nothing is deleted —
and are merged here for rendering, once each.
"""
from server.services.rca_v4_validate import validate


def _wwr(**kw):
    return {"what_went_wrong": kw}


def _findings(out):
    return out["what_went_wrong"]["case_findings"]


def test_one_fact_cited_by_two_claims_renders_once():
    """The repetition this section exists to remove."""
    out, notes = validate(_wwr(guest_issues=[
        {"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
         "evidence": [{"text": "Tickets sent at 14:02", "source": "bms"}]},
        {"issue": "B", "claim": "d", "claim_accuracy": "Accurate",
         "evidence": [{"text": "Tickets sent at 14:02", "source": "bms"}]}]))
    texts = [r["text"] for r in _findings(out)]
    assert texts == ["Tickets sent at 14:02"], texts
    assert any("merged in from the issues" in n for n in notes), notes


def test_the_same_fact_worded_differently_is_still_one_row():
    """Two claims citing one fact routinely word it differently; an exact-string
    key would keep both, which is the repetition arriving by another route."""
    out, _ = validate(_wwr(guest_issues=[
        {"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
         "evidence": [{"text": "Tickets sent 14:02, two hours after the slot",
                       "source": "bms"}]},
        {"issue": "B", "claim": "d", "claim_accuracy": "Accurate",
         "evidence": [{"text": "The tickets were sent two hours after the "
                               "slot, at 14:02", "source": "bms"}]}]))
    assert len(_findings(out)) == 1, [r["text"] for r in _findings(out)]


def test_two_genuinely_different_facts_both_survive():
    """Dedupe that eats a real finding is worse than the repetition."""
    out, _ = validate(_wwr(guest_issues=[
        {"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
         "evidence": [{"text": "Tickets sent at 14:02", "source": "bms"},
                      {"text": "Guest first wrote in on 14 July",
                       "source": "zendesk"}]}]))
    assert len(_findings(out)) == 2, [r["text"] for r in _findings(out)]


def test_the_evidence_stays_on_its_issue():
    """It keeps its claim association in the data — only the rendering moves."""
    out, _ = validate(_wwr(guest_issues=[
        {"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
         "evidence": [{"text": "Tickets sent at 14:02", "source": "bms"}]}]))
    assert out["what_went_wrong"]["guest_issues"][0]["evidence"], \
        "the row was moved instead of merged — the claim lost its evidence"


# ── ordering ───────────────────────────────────────────────────────────────

def test_dated_rows_lead_in_event_order():
    out, _ = validate(_wwr(
        case_findings=[{"text": "Booking confirmed", "source": "booking",
                        "time": "01 Jul 10:00"}],
        guest_issues=[{"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
                       "evidence": [
                           {"text": "Tickets sent late", "source": "bms",
                            "time": "14 Jul 14:02"},
                           {"text": "Guest wrote in", "source": "zendesk",
                            "time": "14 Jul 09:10"}]}]))
    assert [r["time"] for r in _findings(out)] == \
        ["01 Jul 10:00", "14 Jul 09:10", "14 Jul 14:02"], _findings(out)


def test_an_undated_row_sinks_rather_than_leading():
    """A plain list is the honest rendering of rows carrying no order.
    Inventing one would put a sequence on screen the records do not support."""
    out, _ = validate(_wwr(case_findings=[
        {"text": "No time on this one", "source": "booking"},
        {"text": "Dated", "source": "bms", "time": "01 Jul 10:00"}]))
    assert [r["text"] for r in _findings(out)] == ["Dated", "No time on this one"]


def test_a_time_survives_validation_of_the_evidence_row():
    """It was dropped in `_evidence_rows`, so every merged finding arrived
    undated and the section fell back to write-order — the chronology the
    records DO support, thrown away in validation."""
    out, _ = validate(_wwr(guest_issues=[
        {"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
         "evidence": [{"text": "Tickets sent late", "source": "bms",
                       "time": "14 Jul 14:02"}]}]))
    assert _findings(out)[0]["time"] == "14 Jul 14:02", _findings(out)


# ── the empty state, and what it must not look like ────────────────────────

def test_an_empty_section_is_a_list_the_renderer_can_iterate():
    out, _ = validate(_wwr(guest_issues=[]))
    assert _findings(out) == []


def test_nothing_merged_says_nothing_rather_than_reporting_a_zero():
    """A note on every clean run is how a trail stops being read."""
    _, notes = validate(_wwr(guest_issues=[]))
    assert not [n for n in notes if "case findings" in n], notes


# ── the row shape ──────────────────────────────────────────────────────────

def test_an_unknown_source_becomes_null_not_a_broken_rail():
    out, _ = validate(_wwr(case_findings=[
        {"text": "A finding", "source": "guesswork"}]))
    assert _findings(out)[0]["source"] is None


def test_the_dss_sheet_is_not_a_source_for_a_case_finding():
    """A remark about our own decision sheet's coverage is not a record of
    what happened to this booking."""
    out, _ = validate(_wwr(case_findings=[
        {"text": "No DSS row covers this", "source": "dss"}]))
    assert _findings(out)[0]["source"] is None


def test_a_legacy_string_row_still_becomes_a_finding():
    out, _ = validate(_wwr(case_findings=["The booking shows two adults"]))
    assert _findings(out)[0]["text"] == "The booking shows two adults"


def test_an_empty_text_takes_no_row():
    out, _ = validate(_wwr(case_findings=[{"text": "  ", "source": "bms"},
                                          {"text": "Real", "source": "bms"}]))
    assert [r["text"] for r in _findings(out)] == ["Real"]


def test_a_ref_survives_onto_the_finding():
    """`source` and `time` are carried unrendered by design; `ref` is RENDERED,
    and it is what turns "41 negative reviews in the window" into a number
    with a range attached and a ticket id into something you can open. Dropped
    in validation, it is gone for good."""
    out, _ = validate(_wwr(guest_issues=[
        {"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
         "evidence": [{"text": "41 negative reviews", "source": "insights",
                       "ref": "90 days before the review"}]}]))
    assert _findings(out)[0]["ref"] == "90 days before the review", _findings(out)


def test_a_ref_written_directly_on_a_case_finding_survives_too():
    out, _ = validate(_wwr(case_findings=[
        {"text": "Ticket raised", "source": "zendesk", "ref": "ZD-34011333"}]))
    assert _findings(out)[0]["ref"] == "ZD-34011333", _findings(out)
