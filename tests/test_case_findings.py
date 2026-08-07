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
    # An EVIDENCE row that restates a narrative row takes the fold path, which
    # keeps its ticket reference; the collapse note is for narrative-vs-
    # narrative. One row either way — the difference is what is said about it.
    assert any("ticket reference was added to that finding" in n
               or "in different words and were collapsed" in n
               for n in notes), notes


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
    assert any("1 case finding(s) repeated" in n
               or "1 evidence point(s) said what a case finding already said" in n
               for n in notes), notes


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


# ── an evidence row that restates a narrative row folds INTO it ───────────
#
# MEASURED on tp_1785672694_664719 with scripts/trace_findings.py:
#
#   0.55  [ 6] x [14]  the NAR note, twice               DUPLICATE
#   0.50  [ 7] x [13]  the agent closing the window      DUPLICATE
#   0.45  [ 5] x [11]  the guest reporting 13:45         DUPLICATE
#   0.45  [ 4] x [11]  our email vs the guest's report   DIFFERENT
#   0.45  [ 1] x [ 9]  booking created vs cancelled      DIFFERENT
#
# There is NO GAP. 0.45 holds a duplicate and two non-duplicates, and
# stripping digits does not separate them either — both land at 0.375. Every
# row about one booking shares its times, its vendor and the word "guest".
#
# So the threshold does not decide whether a row SURVIVES. The evidence row's
# ZD REF moves onto the narrative row it restates, and no second row is
# written. A mis-attributed ticket link is far cheaper than a duplicated
# finding — or than merging a booking's creation into its cancellation, which
# is what any threshold low enough to catch [5]x[11] does to [1]x[9].

# A restatement close enough that the collapse rule would fire on it anyway.
# The threshold sits there deliberately — see _EVIDENCE_FOLD_OVERLAP.
NARRATIVE = ("Agent confirmed the rescheduling window had closed and no time "
             "change was possible")
RESTATED  = ("Agent confirmed the rescheduling window was closed; no time "
             "change was possible")


def test_a_clear_restatement_folds_rather_than_repeating():
    out, _ = validate(_wwr(
        case_findings=[{"text": NARRATIVE, "source": "zendesk"}],
        guest_issues=[{"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
                       "evidence": [{"text": RESTATED, "source": "zendesk",
                                     "ref": "ZD-34335318"}]}]))
    rows = _findings(out)
    assert len(rows) == 1, [r["text"] for r in rows]


def test_the_ticket_reference_survives_the_fold():
    """The whole reason not to just drop it: the evidence row carries the ZD
    ref and the narrative row does not."""
    out, _ = validate(_wwr(
        case_findings=[{"text": NARRATIVE, "source": "zendesk"}],
        guest_issues=[{"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
                       "evidence": [{"text": RESTATED, "source": "zendesk",
                                     "ref": "ZD-34335318"}]}]))
    assert _findings(out)[0]["ref"] == "ZD-34335318", _findings(out)[0]


def test_the_claim_routing_survives_the_fold():
    out, _ = validate(_wwr(
        case_findings=[{"text": NARRATIVE, "source": "zendesk"}],
        guest_issues=[{"issue": "A", "claim": "first", "claim_accuracy": "Accurate"},
                      {"issue": "B", "claim": "second", "claim_accuracy": "Accurate",
                       "evidence": [{"text": RESTATED, "source": "zendesk"}]}]))
    assert _findings(out)[0]["backs_claim"] == 1, _findings(out)[0]


def test_the_fold_is_counted():
    _, notes = validate(_wwr(
        case_findings=[{"text": NARRATIVE, "source": "zendesk"}],
        guest_issues=[{"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
                       "evidence": [{"text": RESTATED, "source": "zendesk",
                                     "ref": "ZD-1"}]}]))
    assert any("ticket reference was added to that finding" in n for n in notes), notes


def test_an_existing_reference_is_not_overwritten():
    """The narrative row's own ref is the one the model chose for it."""
    out, _ = validate(_wwr(
        case_findings=[{"text": NARRATIVE, "source": "zendesk", "ref": "ZD-999"}],
        guest_issues=[{"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
                       "evidence": [{"text": RESTATED, "source": "zendesk",
                                     "ref": "ZD-34335318"}]}]))
    assert _findings(out)[0]["ref"] == "ZD-999", _findings(out)[0]


def test_evidence_about_a_different_event_still_gets_its_own_row():
    """[4] x [11] from the trace: our confirmation email showing 11:00 against
    the guest reporting a 13:45 pickup. Same booking, same vocabulary, two
    events, 0.50 containment — and the duplicate pair [7] x [13] is ALSO 0.50.

    A fold REMOVES the row, so a false positive here deletes a real finding.
    That is why the threshold sits at the collapse value and not below it:
    some duplicates survive, and nothing is lost."""
    out, _ = validate(_wwr(
        case_findings=[{"text": "Updated confirmation email sent to guest "
                                "showing 11:00 AM start", "source": "zendesk"}],
        guest_issues=[{"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
                       "evidence": [{"text": "Guest reported the vendor sent a "
                                             "message showing 13:45 pickup, not "
                                             "11:00", "source": "zendesk",
                                     "ref": "ZD-34335318"}]}]))
    assert len(_findings(out)) == 2, [r["text"] for r in _findings(out)]


# ── the clock does the work the wording could not ──────────────────────────
#
# The block above measured a threshold with NO GAP in it: 0.57 was a duplicate
# and 0.50 was two different events, so no single number separates them on
# wording alone. That is still true. What changed is that the rows now carry
# times, and TWO ROWS AT THE SAME MINUTE ARE NOT A COINCIDENCE — the timestamp
# is a second, independent signal, and once it agrees the wording only has to
# corroborate rather than prove.
#
# MEASURED, on the pair from the trace:
#   0.571  [ 7] x [11]  same minute, one event written twice   DUPLICATE
#   0.100  [ 7] x [ 8]  same minute, guest writes / agent replies  DIFFERENT
# A real gap, and _SAME_MINUTE_OVERLAP sits at 0.35 inside it.

_MINUTE = "02 Aug 15:36"
_CHAT_IN = ("Guest contacted support via chat: the operator had messaged a "
            "13:45 pickup after the rescheduling window closed")
_CHAT_IN_RESTATED = ("Guest reported at 15:36 on 02 Aug that the vendor had "
                     "messaged a 13:45 pickup after the window closed")
_AGENT_REPLY = ("Agent confirmed no further changes possible, provided "
                "Krakville contact details for the guest")


def _one_issue(evidence):
    return [{"issue": "A", "claim": "c", "claim_accuracy": "Accurate",
             "evidence": evidence}]


def test_a_same_minute_restatement_folds_though_the_wording_alone_would_not():
    """0.571 containment — UNDER the 0.60 fold, and it reached the card as a
    second row. Both rows say 02 Aug 15:36."""
    out, _ = validate(_wwr(
        case_findings=[{"text": _CHAT_IN, "source": "zendesk",
                        "time": _MINUTE}],
        guest_issues=_one_issue([{"text": _CHAT_IN_RESTATED,
                                  "source": "zendesk", "time": _MINUTE,
                                  "ref": "ZD-34335318"}])))
    rows = _findings(out)
    assert len(rows) == 1, [r["text"] for r in rows]


def test_the_ref_still_moves_across_on_a_same_minute_fold():
    out, _ = validate(_wwr(
        case_findings=[{"text": _CHAT_IN, "source": "zendesk",
                        "time": _MINUTE}],
        guest_issues=_one_issue([{"text": _CHAT_IN_RESTATED,
                                  "source": "zendesk", "time": _MINUTE,
                                  "ref": "ZD-34335318"}])))
    assert _findings(out)[0]["ref"] == "ZD-34335318", _findings(out)[0]


def test_two_different_things_in_one_minute_both_survive():
    """The guest writing and the agent answering are both 02 Aug 15:36. A
    minute is not an event, and folding on the clock alone would delete the
    reply."""
    out, _ = validate(_wwr(
        case_findings=[{"text": _CHAT_IN, "source": "zendesk",
                        "time": _MINUTE},
                       {"text": _AGENT_REPLY, "source": "zendesk",
                        "time": _MINUTE}],
        guest_issues=_one_issue([])))
    assert len(_findings(out)) == 2, [r["text"] for r in _findings(out)]


def test_a_different_event_arriving_as_evidence_survives_its_own_minute():
    """SURVIVOR. `_SAME_MINUTE_OVERLAP = 0.0` passed the whole suite.

    The test above puts both rows in `case_findings`, so they meet at `_add`
    and never reach the same-minute bar at all — it proved nothing about the
    number. This one sends the different event in as EVIDENCE, which is the
    only path that bar governs. At 0.0 the clock folds alone and the agent's
    reply is deleted; the wording has to corroborate, and at 0.100 it does
    not."""
    out, _ = validate(_wwr(
        case_findings=[{"text": _AGENT_REPLY, "source": "zendesk",
                        "time": _MINUTE}],
        guest_issues=_one_issue([{"text": _CHAT_IN_RESTATED,
                                  "source": "zendesk", "time": _MINUTE,
                                  "ref": "ZD-34335318"}])))
    assert len(_findings(out)) == 2, [r["text"] for r in _findings(out)]


def test_the_fold_lands_on_the_row_that_matched_not_the_first_one():
    """SURVIVOR. Dropping the `_same[_hit]` mapping passed the whole suite.

    `_first_repeat_index` searches a SUBLIST — the same-minute rows — so it
    returns a position in that sublist, not in `rows`. Every test until now had
    the matching row at index 0 and only one candidate, where the two indices
    are identical and the bug is invisible.

    Here the match is the second row. Unmapped, the ref lands on the booking's
    creation: a ticket link on a finding that has nothing to do with it, and
    the finding it belonged to left bare. Nothing crashes and no count
    changes — the card just quietly cites the wrong record."""
    out, _ = validate(_wwr(
        case_findings=[{"text": "Booking created for the 03 Aug slot",
                        "source": "booking", "time": "21 Jul 09:00"},
                       {"text": _CHAT_IN, "source": "zendesk",
                        "time": _MINUTE}],
        guest_issues=_one_issue([{"text": _CHAT_IN_RESTATED,
                                  "source": "zendesk", "time": _MINUTE,
                                  "ref": "ZD-34335318"}])))
    by_text = {r["text"]: r for r in _findings(out)}
    assert by_text[_CHAT_IN]["ref"] == "ZD-34335318", _findings(out)
    assert not by_text["Booking created for the 03 Aug slot"]["ref"], \
        "the ticket landed on the booking's creation — a sublist index used " \
        "as a row index"


def test_the_same_wording_at_a_different_minute_is_left_alone():
    """0.571 is below the ordinary fold, and without the matching clock it
    stays below it. The timestamp is what earns the lower bar, so an evidence
    row an hour away from the narrative row keeps its own place."""
    out, _ = validate(_wwr(
        case_findings=[{"text": _CHAT_IN, "source": "zendesk",
                        "time": _MINUTE}],
        guest_issues=_one_issue([{"text": _CHAT_IN_RESTATED,
                                  "source": "zendesk", "time": "02 Aug 16:36",
                                  "ref": "ZD-34335318"}])))
    assert len(_findings(out)) == 2, [r["text"] for r in _findings(out)]


def test_an_undated_evidence_row_does_not_fold_into_a_dated_one():
    """"No time" is not a minute, and two rows that both lack one have not
    agreed on anything. This is the state the card was actually in — every
    evidence row came back timeless — and it must fall back to the wording
    threshold, not fold everything into the first row."""
    out, _ = validate(_wwr(
        case_findings=[{"text": _CHAT_IN, "source": "zendesk",
                        "time": _MINUTE}],
        guest_issues=_one_issue([{"text": _CHAT_IN_RESTATED,
                                  "source": "zendesk",
                                  "ref": "ZD-34335318"}])))
    assert len(_findings(out)) == 2, [r["text"] for r in _findings(out)]


def test_an_evidence_rows_own_time_places_it_in_the_order():
    """THE DEFECT THIS CLOSES. `_case_findings` has always read `e["time"]`,
    but `evidence[]` had no `time` in the prompt's schema — so the model never
    sent one, every merged evidence row sorted as undated, and §1 put four of
    them under "cannot be placed" while their own text read "at 15:36 on 02
    Aug". A field the validator reads and the prompt never asks for looks
    exactly like a model that had no time to give."""
    out, notes = validate(_wwr(
        case_findings=[{"text": "Booking created for the 03 Aug slot",
                        "source": "booking", "time": "21 Jul 09:00"},
                       {"text": "Refund settled and the case closed out",
                        "source": "zendesk", "time": "03 Aug 11:00"}],
        guest_issues=_one_issue([{"text": _CHAT_IN_RESTATED,
                                  "source": "zendesk", "time": _MINUTE,
                                  "ref": "ZD-34335318"}])))
    assert [r["time"] for r in _findings(out)] == [
        "21 Jul 09:00", "02 Aug 15:36", "03 Aug 11:00"], _findings(out)
    assert not any("carry no time" in n for n in notes), notes


# ── §1 is chronological, on a real date and not on the display string ─────

from server.services.rca_v4_validate import _finding_order


def test_july_sorts_before_august():
    """THE DEFECT. The key was `(time is None, time or "")` — the DISPLAY
    STRING — so "21 Jul" sorted after "03 Aug" because "2" > "0". §1 opened
    with an August payment and put the booking's own creation ninth:

        [ 0] BNPL payment charged successfully on 01 Aug
        ...
        [ 9] Booking created for 08:30 on 03 Aug with Krakville

    A lexical sort on a day-month string is not an ordering of anything."""
    rows = [{"time": "01 Aug 12:03"}, {"time": "21 Jul 15:28"},
            {"time": "03 Aug 08:30"}]
    assert [r["time"] for r in sorted(rows, key=_finding_order)] == [
        "21 Jul 15:28", "01 Aug 12:03", "03 Aug 08:30"]


def test_the_clock_orders_two_findings_on_one_day():
    rows = [{"time": "02 Aug 15:36"}, {"time": "02 Aug 09:13"}]
    assert [r["time"] for r in sorted(rows, key=_finding_order)][0] == "02 Aug 09:13"


def test_an_iso_time_sorts_beside_a_day_month_one():
    """The model writes both shapes. Two formats in one list must still be one
    chronology."""
    rows = [{"time": "03 Aug 08:30"}, {"time": "2026-08-02 15:36"},
            {"time": "21 Jul 15:28"}]
    assert [r["time"] for r in sorted(rows, key=_finding_order)] == [
        "21 Jul 15:28", "2026-08-02 15:36", "03 Aug 08:30"]


def test_an_undated_finding_sinks_rather_than_floating():
    """An undated row at the top reads as the first thing that happened, which
    is a claim nobody made."""
    rows = [{"time": None}, {"time": "21 Jul 15:28"}]
    assert sorted(rows, key=_finding_order)[-1]["time"] is None


def test_an_unreadable_time_is_treated_as_no_time():
    """"sometime in June" is not a time. Pretending to order it would put a
    row where the records do not support it."""
    rows = [{"time": "sometime in June"}, {"time": "21 Jul 15:28"}]
    assert sorted(rows, key=_finding_order)[-1]["time"] == "sometime in June"


def test_findings_come_out_of_validate_in_order():
    """Driven end to end, not through the key alone."""
    out, _ = validate(_wwr(case_findings=[
        {"text": "BNPL payment charged successfully", "source": "booking",
         "time": "01 Aug 12:03"},
        {"text": "Booking created with Krakville for the 08:30 slot",
         "source": "booking", "time": "21 Jul 15:28"},
        {"text": "Wallet credit issued the day after the visit",
         "source": "booking", "time": "03 Aug 12:44"}]))
    assert [r["text"][:12] for r in _findings(out)] == [
        "Booking crea", "BNPL payment", "Wallet credi"], _findings(out)


def test_undated_findings_are_counted_so_the_order_is_not_believed():
    """MEASURED on a real card: 14 findings, not one carrying a time, so §1
    opened with an August payment and put the booking's own creation eighth.

    The sort was correct. Every row sank equally, and the section was the
    model's writing order wearing a chronology's clothes — with nothing on the
    card to say so."""
    _, notes = validate(_wwr(case_findings=[
        {"text": "Booking created for the 03 Aug slot", "source": "booking"},
        {"text": "BNPL payment charged successfully", "source": "booking"}]))
    said = " ".join(n for n in notes if "carry no time" in n)
    assert "2 of 2" in said, notes
    assert "NOT a chronology" in said, notes


def test_a_fully_dated_section_says_nothing():
    """A count on every healthy card is the noise that makes a reader stop
    reading the ones that mean something."""
    _, notes = validate(_wwr(case_findings=[
        {"text": "Booking created", "source": "booking", "time": "21 Jul 15:28"},
        {"text": "Payment charged", "source": "booking", "time": "01 Aug 12:03"}]))
    assert not any("carry no time" in n for n in notes), notes


def test_a_partly_dated_section_counts_only_what_it_could_not_place():
    _, notes = validate(_wwr(case_findings=[
        {"text": "Booking created", "source": "booking", "time": "21 Jul 15:28"},
        {"text": "The product is reschedulable up to 1440 minutes before start",
         "source": "exp-page"}]))
    said = " ".join(n for n in notes if "carry no time" in n)
    assert "1 of 2" in said, notes
    assert "NOT a chronology" not in said, notes


# ── the prompt has to ASK for every field the validator reads ───────────────
#
# `_case_findings` read `e["time"]` off evidence rows from the day the fold was
# written. `evidence[]` in the schema had four keys and `time` was not one of
# them, so the model could not have sent one, and did not. §1 then filed four
# evidence rows under "cannot be placed" — the sentence a genuinely undated
# record gets. A field read but never asked for is indistinguishable from a
# record with nothing to give.
#
# These drive `rca_v3_prompt()`, the string the model actually receives.
# Asserting against RCA_V4_TEMPLATE would pass against a build where the
# substitution is broken.

def _assembled_prompt():
    from server import prompts
    return prompts.rca_v3_prompt(
        review_text="the tickets never arrived", booking={}, timeline=[],
        insights={}, dss_rec={}, l1="", l2="", sub_theme="",
        support_summary="", checklist={}, review_id="r1")


def _evidence_schema_block(text):
    """The `"evidence": [ { ... } ]` object out of the assembled schema.

    Located by brace-matching rather than a fixed window, because a
    fixed-width slice silently stops covering the block it names as soon as
    the schema grows."""
    i = text.index('"evidence": [')
    j = text.index("{", i)
    depth, k = 0, j
    while k < len(text):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[j:k + 1]
        k += 1
    raise AssertionError("the evidence object in the schema is unclosed")


def test_every_evidence_field_the_validator_reads_is_declared_to_the_model():
    """The contract, named. `_case_findings` reads exactly these off an
    evidence dict; each one has to appear as a key the model is asked for."""
    block = _evidence_schema_block(_assembled_prompt())
    for field in ("text", "source", "time", "ref", "backs_claim"):
        assert f'"{field}"' in block, \
            f"the validator reads evidence[].{field} and the prompt never " \
            f"asks for it:\n{block}"


def test_the_evidence_time_rule_reaches_the_model_with_its_reason():
    """A rule with no reason attached is the first thing lost in an edit —
    and this one has already been lost once, by never being written."""
    out = " ".join(_assembled_prompt().split())
    assert "THIS APPLIES TO `evidence[]` ENTRIES TOO" in out
    assert "sinks to the bottom of §1" in out
