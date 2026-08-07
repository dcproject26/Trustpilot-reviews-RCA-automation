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


# ── the evidence merges into §1, and §1 is the only place it renders ───────

def test_evidence_is_merged_into_case_findings():
    """BACK ON, and this is now the only render.

    It was switched off because it duplicated: the model writes one fact two
    ways — as a case finding and again as evidence — and the dedupe keys on
    normalised wording, so both survived.

    Switching it off did not leave the evidence somewhere else. `evRow`, the
    per-issue renderer in the client, had NO CALLERS, so the claim-backing
    facts appeared nowhere at all. A dead renderer and a working one look
    identical on a card whose evidence is empty, which is why nothing said so.

    The answer to the duplication is not a better wording threshold — none
    exists that separates two phrasings of one event from two different
    events. It is that the prompt now tells the model §1 IS where a
    claim-backing fact goes, so it has no reason to write the same fact twice.
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
    assert len(rows) == 2, [r["text"] for r in rows]
    assert any(r["ref"] == "ZD-33978941" for r in rows), rows


def test_a_merged_finding_says_it_was_merged():
    """A count the reader can check. Rows appearing in §1 that the model did
    not write there is a rewrite of what it returned, and a rewrite is
    announced."""
    _, notes = validate(_wwr(
        guest_issues=[{"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
                       "evidence": [{"text": "Ticket resent 11:02",
                                     "source": "zendesk"}]}]))
    assert any("claim-backing fact" in n for n in notes), notes


def test_a_merged_finding_stays_routed_to_its_claim():
    """`backs_claim` is what kept a fact under the claim it supports. It has
    to survive the move, or §1 becomes a flat list and the routing the card
    does today is lost."""
    out, _ = validate(_wwr(
        guest_issues=[{"issue": "A", "claim": "c", "claim_accuracy": "Accurate"},
                      {"issue": "B", "claim": "d", "claim_accuracy": "Accurate",
                       "evidence": [{"text": "Refund raised 12 Aug",
                                     "source": "zendesk"}]}]))
    row = [r for r in _findings(out) if "Refund raised" in r["text"]]
    assert row, _findings(out)
    assert row[0]["backs_claim"] == 1, row[0]


def test_an_exact_repeat_is_still_collapsed():
    """The dedupe that can work still runs. Identical wording in both places
    is one fact, and showing it twice is the complaint that started this."""
    out, _ = validate(_wwr(
        case_findings=[{"text": "Confirmation emailed to guest", "source": "zendesk"}],
        guest_issues=[{"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
                       "evidence": [{"text": "Confirmation emailed to guest",
                                     "source": "zendesk"}]}]))
    assert len(_findings(out)) == 1, [r["text"] for r in _findings(out)]


def test_the_card_has_no_second_place_for_evidence_to_render():
    """NEGATIVE source assertion on CLIENT-SIDE JAVASCRIPT, the exception
    CLAUDE.md names. The whole point of merging into §1 is that there is ONE
    render; a second one reintroduces the duplication from the other side, and
    last time the second one was dead code nobody could see."""
    src = open("client/index.html", encoding="utf-8").read()
    for gone in ("const evRow", "data-ev-del=", "data-wwr-ev-add="):
        assert gone not in src, f"{gone} is back — §1 is no longer the only render"


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
