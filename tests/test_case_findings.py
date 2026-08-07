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


# ── ordering ───────────────────────────────────────────────────────────────

def test_an_undated_row_sinks_rather_than_leading():
    """A plain list is the honest rendering of rows carrying no order.
    Inventing one would put a sequence on screen the records do not support."""
    out, _ = validate(_wwr(case_findings=[
        {"text": "No time on this one", "source": "booking"},
        {"text": "Dated", "source": "bms", "time": "01 Jul 10:00"}]))
    assert [r["text"] for r in _findings(out)] == ["Dated", "No time on this one"]


# ── the empty state, and what it must not look like ────────────────────────

def test_an_empty_section_is_a_list_the_renderer_can_iterate():
    out, _ = validate(_wwr(guest_issues=[]))
    assert _findings(out) == []


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


def test_a_ref_written_directly_on_a_case_finding_survives_too():
    out, _ = validate(_wwr(case_findings=[
        {"text": "Ticket raised", "source": "zendesk", "ref": "ZD-34011333"}]))
    assert _findings(out)[0]["ref"] == "ZD-34011333", _findings(out)


# ── the evidence merge is off ──────────────────────────────────────────────

def test_evidence_is_not_merged_into_case_findings():
    """TURNED OFF BY REQUEST after it produced duplicates on real cards.

    The dedupe keys on normalised wording, and the model writes one fact two
    ways — as a case finding and again as evidence — so both survived and the
    section showed the same event twice, once with a ZD ref and once without.
    No wording threshold separates that from two genuinely different facts.

    Eight tests drove the merge and were removed WITH it rather than left
    asserting behaviour nobody wants.
    """
    out, notes = validate(_wwr(
        case_findings=[{"text": "Confirmation emailed to guest",
                        "source": "zendesk"}],
        guest_issues=[{"issue": "A", "claim": "c",
                       "claim_accuracy": "Accurate",
                       "evidence": [{"text": "Confirmation email sent 09:13",
                                     "source": "zendesk",
                                     "ref": "ZD-33978941"}]}]))
    rows = _findings(out)
    assert len(rows) == 1, [r["text"] for r in rows]
    assert rows[0]["ref"] is None, rows[0]
    assert not any("merged in from the issues" in n for n in notes), notes


def test_the_evidence_is_still_stored_on_its_issue():
    """Off the section, not deleted — restoring the merge is re-enabling a
    loop rather than rebuilding the data."""
    out, _ = validate(_wwr(guest_issues=[
        {"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
         "evidence": [{"text": "Confirmation email sent 09:13",
                       "source": "zendesk", "ref": "ZD-33978941"}]}]))
    ev = out["what_went_wrong"]["guest_issues"][0]["evidence"]
    assert ev and ev[0]["ref"] == "ZD-33978941", ev


def test_a_case_finding_the_model_wrote_still_orders_by_time():
    out, _ = validate(_wwr(case_findings=[
        {"text": "Later", "source": "bms", "time": "05 Aug 10:00"},
        {"text": "Earlier", "source": "bms", "time": "01 Aug 10:00"}]))
    assert [r["text"] for r in _findings(out)] == ["Earlier", "Later"]
