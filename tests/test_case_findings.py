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


# ── §1 does two jobs, and that is what stops it repeating itself ───────────
#
# NARRATIVE points say what happened: the booking arrived, was it fulfilled,
# what did the guest hit, why did they contact us, what did we say, how did it
# end. EVIDENCE points settle ONE claim the guest made, and carry the ZD ref
# they were read from. Two jobs, so neither restates the other — and where the
# model writes them as one fact anyway, it is collapsed and counted.

def test_evidence_moves_into_case_findings():
    out, notes = validate(_wwr(
        case_findings=[{"text": "Tickets were issued on 01 Aug for the 03 Aug slot",
                        "source": "booking"}],
        guest_issues=[{"issue": "A", "claim": "The new time was never sent to me",
                       "claim_accuracy": "Inaccurate",
                       "evidence": [{"text": "Updated confirmation was delivered "
                                             "to the address on the booking",
                                     "source": "zendesk", "ref": "ZD-33978941"}]}]))
    rows = _findings(out)
    assert len(rows) == 2, [r["text"] for r in rows]
    assert any(r["ref"] == "ZD-33978941" for r in rows), rows
    assert any("evidence point" in n for n in notes), notes


def test_a_reworded_repeat_of_a_narrative_point_is_collapsed():
    """THE PAIR THAT REACHED THE CARD. One event, two wordings, two different
    sorted-token keys — so the exact-key check passed both and §1 showed
    eighteen findings for nine facts."""
    out, notes = validate(_wwr(
        case_findings=[{"text": "Original 08:30 slot cancelled via API and "
                                "rebooking sent to Krakville at 11:00 AM on "
                                "the same booking reference",
                        "source": "booking"}],
        guest_issues=[{"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
                       "evidence": [{"text": "Rebooking to Krakville at 11:00 "
                                             "sent on the same booking "
                                             "reference after the 08:30 slot "
                                             "was cancelled via API",
                                     "source": "zendesk", "ref": "ZD-33978941"}]}]))
    assert len(_findings(out)) == 1, [r["text"] for r in _findings(out)]
    assert any("in different words and were collapsed" in n for n in notes), notes


def test_the_collapse_is_counted_not_silent():
    """A section that quietly shrinks is indistinguishable from a model that
    returned less. The count is how a threshold set too high or too low is
    visible rather than absorbed."""
    _, notes = validate(_wwr(
        case_findings=[{"text": "Guest contacted support at 15:36 about the "
                                "pickup time discrepancy with the vendor",
                        "source": "zendesk"}],
        guest_issues=[{"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
                       "evidence": [{"text": "The guest contacted support at "
                                             "15:36 regarding the vendor "
                                             "pickup time discrepancy",
                                     "source": "zendesk"}]}]))
    assert any("1 case finding(s) repeated" in n for n in notes), notes


def test_two_genuinely_different_facts_both_survive():
    """The threshold has to keep real findings. Same case, same vocabulary,
    different facts."""
    out, _ = validate(_wwr(
        case_findings=[{"text": "Tickets were issued on 01 Aug for the 03 Aug slot",
                        "source": "booking"},
                       {"text": "Wallet credit of USD 39.59 issued the day after "
                                "the visit with no refund of the booking amount",
                        "source": "booking"}]))
    assert len(_findings(out)) == 2, [r["text"] for r in _findings(out)]


def test_an_evidence_row_backing_no_claim_is_not_rendered():
    """Evidence settles a claim. A row citing none is a timeline entry that
    wandered in — and timeline entries are what made §1 read as a second copy
    of the events timeline.

    DRIVES `_case_findings` DIRECTLY. Going through `validate` cannot reach
    this: an issue with no claim is moved to flags, and one that survives on
    an operational failure alone has that dropped when the case shows nothing.
    Contriving a path would have tested the contrivance. The function is pure
    and takes its three inputs, so it is driven where it lives.
    """
    from server.services.rca_v4_validate import _case_findings
    notes = []
    rows = _case_findings(
        [], [{"issue": "A", "claim": "",
              "evidence": [{"text": "Confirmation email sent 09:13",
                            "source": "zendesk"}]}], notes)
    assert rows == [], rows
    assert any("backed no claim" in n for n in notes), notes


def test_a_dropped_evidence_row_says_so_rather_than_vanishing():
    """Counted, never silent. A section that shrinks without saying so reads
    as a model that returned less."""
    from server.services.rca_v4_validate import _case_findings
    notes = []
    _case_findings(
        [], [{"issue": "A", "claim": None,
              "evidence": [{"text": "Rebooking sent 09:11", "source": "zendesk"},
                           {"text": "Confirmation emailed 09:13",
                            "source": "zendesk"}]}], notes)
    assert any("2 evidence row(s) backed no claim" in n for n in notes), notes


def test_an_evidence_point_stays_routed_to_its_claim():
    """`backs_claim` is what kept a fact under the claim it settles. Moving the
    row into §1 must not lose the routing the card already does."""
    out, _ = validate(_wwr(
        guest_issues=[{"issue": "A", "claim": "first claim",
                       "claim_accuracy": "Accurate"},
                      {"issue": "B", "claim": "second claim",
                       "claim_accuracy": "Accurate",
                       "evidence": [{"text": "The refund was raised on 12 Aug "
                                             "and settled to the wallet",
                                     "source": "booking"}]}]))
    row = [r for r in _findings(out) if "refund was raised" in r["text"]]
    assert row and row[0]["backs_claim"] == 1, _findings(out)


def test_a_narrative_point_backs_no_claim():
    """The two jobs are distinguishable in the DATA, not only in the prose —
    otherwise the card cannot route one and not the other."""
    out, _ = validate(_wwr(
        case_findings=[{"text": "Booking arrived 01 Aug for the 03 Aug slot",
                        "source": "booking"}]))
    assert _findings(out)[0]["backs_claim"] is None, _findings(out)


def test_a_very_short_point_is_never_collapsed_on_overlap():
    """Two short rows sharing their only words are not necessarily one fact,
    and collapsing them would delete a real finding."""
    out, _ = validate(_wwr(
        case_findings=[{"text": "Tickets were sent", "source": "booking"},
                       {"text": "Tickets were late", "source": "booking"}]))
    assert len(_findings(out)) == 2, _findings(out)


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
