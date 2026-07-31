"""The coercions that stop malformed model output reaching the screen.

Each case here is a defect the design handoff observed in the deployed build,
or a shape change v4 introduced. The frontend renders whatever passes this
layer with no special-casing, so anything that can fail an enum has to be
settled here rather than guarded in the UI.
"""
from server.services.rca_v4_validate import validate


def _ok(**over):
    base = {
        "stated_issue": "The voucher never arrived.",
        "tldr": {"our_mistake": "We did not send it.", "our_fix": "Refunded."},
        "l1": "Operations Issue", "l2": "Ticket Issues",
        "sub_themes": ["C. Ticket Delayed"], "scenarios": ["Ticket delivery delay"],
        "what_went_wrong": {"guest_issues": [{
            "issue": "Voucher never delivered",
            "claim": "I waited two hours and nothing came.",
            "claim_accuracy": "Accurate", "owner": "RO",
            "root_cause": "The fulfilment run failed silently.",
            "evidence": [{"text": "Fulfilment log shows three failed retries.",
                          "source": "bms", "ref": "https://x/1"}],
        }]},
        "issue_specific_answers": [],
        "takedown": {"verdict": "No"},
    }
    base.update(over)
    return base


# ── claim_accuracy: a closed four-value enum ────────────────────────────────

def test_the_v3_vocabulary_maps_onto_the_v4_enum():
    """Old drafts and a model still reaching for the old words must not all
    land on grey. "Partially True" is a real verdict; it is Partly accurate."""
    for raw, want in (("Yes", "Accurate"), ("Partially True", "Partly accurate"),
                      ("No", "Inaccurate"), ("Accurate", "Accurate"),
                      ("inaccurate", "Inaccurate"), ("Partly accurate", "Partly accurate")):
        out, _ = validate(_ok(what_went_wrong={"guest_issues": [
            {"issue": "x", "claim_accuracy": raw, "root_cause": "y"}]}))
        got = out["what_went_wrong"]["guest_issues"][0]["claim_accuracy"]
        assert got == want, f"{raw!r} → {got!r}, expected {want!r}"


def test_a_sentence_verdict_keeps_its_tail_instead_of_losing_it():
    """"Partially True — booking status shows…" put a sentence in a 140px
    chip. The verdict is the enum; the reasoning belongs in the note."""
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [{
        "issue": "x", "root_cause": "y",
        "claim_accuracy": "Partially True — booking status shows a refund",
    }]}))
    iss = out["what_went_wrong"]["guest_issues"][0]
    assert iss["claim_accuracy"] == "Partly accurate"
    assert "booking status shows a refund" in iss["claim_accuracy_note"]
    assert iss["claim_accuracy_raw"], "the model's raw string must stay recoverable"


def test_an_unrecognisable_verdict_becomes_unknown_not_a_raw_pill():
    out, notes = validate(_ok(what_went_wrong={"guest_issues": [
        {"issue": "x", "root_cause": "y", "claim_accuracy": "probably fine tbh"}]}))
    assert out["what_went_wrong"]["guest_issues"][0]["claim_accuracy"] == "Unknown"
    assert any("claim_accuracy" in n for n in notes)


def test_the_guest_quote_is_never_trimmed():
    """claim is verbatim at whatever length the guest wrote it — the Claim
    block is built to wrap it. The 8-16 word finding rule does not apply."""
    long_quote = ("I booked this weeks ago and nobody told me the tickets would "
                  "take two hours to arrive, which meant we stood outside in the "
                  "rain with two small children for the whole afternoon.")
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [
        {"issue": "x", "root_cause": "y", "claim": long_quote}]}))
    assert out["what_went_wrong"]["guest_issues"][0]["claim"] == long_quote


# ── evidence: structured, never a prefix in the sentence ────────────────────

def test_a_legacy_string_evidence_becomes_a_row():
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [{
        "issue": "x", "root_cause": "y",
        "evidence": ["[booking] The booking shows two adult tickets."]}]}))
    ev = out["what_went_wrong"]["guest_issues"][0]["evidence"][0]
    assert ev["text"] == "The booking shows two adult tickets."
    assert ev["source"] == "booking", "the prefix belongs in source, not the sentence"
    assert ev["ref"] is None


def test_a_source_prefix_is_stripped_out_of_the_sentence():
    """source and ref are structured fields; a prefix inside text renders the
    marker twice, once as the rail and once in the words."""
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [{
        "issue": "x", "root_cause": "y",
        "evidence": [{"text": "[insights] Twelve similar reviews in 90 days.",
                      "source": "insights", "ref": None}]}]}))
    assert out["what_went_wrong"]["guest_issues"][0]["evidence"][0]["text"] \
        == "Twelve similar reviews in 90 days."


def test_an_unknown_source_becomes_null_rather_than_a_broken_rail():
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [{
        "issue": "x", "root_cause": "y",
        "evidence": [{"text": "A finding.", "source": "guesswork", "ref": "-"}]}]}))
    ev = out["what_went_wrong"]["guest_issues"][0]["evidence"][0]
    assert ev["source"] is None and ev["ref"] is None


# ── issue_specific_answers: array, bare verdicts ────────────────────────────

def test_a_sentence_in_the_verdict_moves_into_evidence():
    """"28 minutes (…)" is an evidence value. Rendering it as an 82px pill is
    the defect the fixed vocabulary exists to remove."""
    out, notes = validate(_ok(issue_specific_answers=[
        {"question": "How long until first reply?", "verdict": "28 minutes (first agent)",
         "evidence": None}]))
    a = out["issue_specific_answers"][0]
    assert a["verdict"] == "Unknown"
    assert "28 minutes" in a["evidence"]
    assert any("moved into evidence" in n for n in notes)


def test_the_v3_question_answer_map_still_reads():
    """Drafts written before this deploy hold {question: answer}."""
    out, _ = validate(_ok(issue_specific_answers={
        "Was the ticket delivered on time?": "No",
        "Did CE reply within SLA?": "the agent replied after 40 minutes"}))
    rows = {r["question"]: r for r in out["issue_specific_answers"]}
    assert rows["Was the ticket delivered on time?"]["verdict"] == "No"
    unparsed = rows["Did CE reply within SLA?"]
    assert unparsed["verdict"] == "Unknown"
    assert "40 minutes" in unparsed["evidence"], "the answer must not be lost"


def test_a_question_is_never_dropped():
    out, _ = validate(_ok(issue_specific_answers=[
        {"question": "Q1", "verdict": "Yes"},
        {"question": "Q2", "verdict": None, "evidence": "not in the data"}]))
    assert [r["question"] for r in out["issue_specific_answers"]] == ["Q1", "Q2"]


# ── non-values, bullets, empties ────────────────────────────────────────────

def test_non_values_become_null_not_the_word_unknown():
    """"Unknown" in a time column is not a timestamp; the UI renders — for
    null and a literal "Unknown" for the string."""
    out, _ = validate(_ok(booking_logs=[
        {"time": "Unknown", "what": "Fulfilment attempted", "detail": "N/A"},
        {"time": "22 Jul 15:41", "what": "Voucher issued", "detail": "-"}]))
    assert out["booking_logs"][0]["time"] is None
    assert out["booking_logs"][0]["detail"] is None
    assert out["booking_logs"][1]["time"] == "22 Jul 15:41"


def test_leading_bullets_are_stripped():
    """A bullet prepended to a block-level editable renders on its own line."""
    out, _ = validate(_ok(area_of_improving=["• Add a delivery window to the page",
                                             "1. Alert on failed fulfilment"]))
    assert out["area_of_improving"] == ["Add a delivery window to the page",
                                        "Alert on failed fulfilment"]


def test_an_empty_contact_is_not_dressed_as_a_data_row():
    """"01 · UNKNOWN · Unknown · No guest contact found" was a nothing-found
    message rendered as a numbered contact. The empty state says it better."""
    out, _ = validate(_ok(support_interaction=[
        {"channel": "Unknown", "time": "Unknown",
         "summary": "No guest contact found on this booking"}]))
    assert out["support_interaction"] == []


def test_a_real_contact_survives():
    out, _ = validate(_ok(support_interaction=[
        {"channel": "chat", "time": "22 Jul 15:41",
         "summary": "Guest asked where the tickets were.", "ce_miss": None}]))
    assert len(out["support_interaction"]) == 1


# ── enums elsewhere ─────────────────────────────────────────────────────────

def test_sop_and_takedown_fall_back_inside_their_enums():
    out, _ = validate(_ok(sop_compliance={"verdict": "mostly followed"},
                          takedown={"verdict": "maybe"}))
    assert out["sop_compliance"]["verdict"] == "unknown"
    assert out["takedown"]["verdict"] == "Untraceable"


def test_overlays_never_repeat_a_scenario():
    out, notes = validate(_ok(scenarios=["A", "B"], overlay_scenarios=["B", "C"]))
    assert out["overlay_scenarios"] == ["C"]
    assert any("overlay" in n for n in notes)


def test_a_routed_scenario_with_no_issue_is_reported():
    _, notes = validate(_ok(), scenarios_routed=["Refund not processed"])
    assert any("Refund not processed" in n for n in notes)


def test_an_internal_name_in_the_guest_reply_is_flagged():
    """suggested_response is guest-facing. Selenium, BMS and a ZD id are not."""
    _, notes = validate(_ok(suggested_response="Sorry — the Selenium job failed, see ZD-4491."))
    assert any("internal name" in n for n in notes)


def test_a_clean_reply_is_not_flagged():
    _, notes = validate(_ok(suggested_response="I'm sorry your tickets were late. "
                                               "We have refunded you in full."))
    assert not any("internal name" in n for n in notes)


def test_a_flag_team_outside_the_vocabulary_does_not_reach_the_chip():
    out, _ = validate(_ok(flags=[
        {"team": "Growth", "flag": "No alert on failed fulfilment",
         "evidence": "Three retries, no page."},
        {"team": "ce", "flag": "First reply after SLA", "evidence": "40 minutes."}]))
    assert out["flags"][0]["team"] is None
    assert out["flags"][1]["team"] == "CE", "case is not a reason to lose the team"
    assert out["flags"][0]["flag"], "the flag itself must survive its bad team"


def test_a_contact_channel_outside_the_vocabulary_becomes_null():
    """"UNKNOWN" rendered as a channel pill is the defect, not the fix."""
    out, _ = validate(_ok(support_interaction=[
        {"channel": "whatsapp", "time": "22 Jul 15:41",
         "summary": "Guest asked where the tickets were."}]))
    assert out["support_interaction"][0]["channel"] is None
    assert out["support_interaction"][0]["summary"]


# ── classification is echoed, so a fabrication has to be caught here ────────

def test_an_invented_category_falls_back_to_the_catch_all():
    """The Slack Issue: line and every aggregation are built from l1/l2. A
    plausible-looking invention is worse than admitting we could not tell."""
    out, notes = validate(_ok(l1="Refund Issue", l2="Delayed Refund"))
    assert (out["l1"], out["l2"]) == ("Miscellaneous Issue", "Vague review")
    assert (out["l1_raw"], out["l2_raw"]) == ("Refund Issue", "Delayed Refund")
    assert any("taxonomy" in n for n in notes)


def test_a_real_l1_with_an_l2_from_the_wrong_l1_is_still_rejected():
    """"Operations Issue" and "Guide No Show" are each real; the pair is not."""
    out, _ = validate(_ok(l1="Operations Issue", l2="Guide No Show"))
    assert out["l1"] == "Miscellaneous Issue"


def test_a_valid_pair_passes_untouched_and_records_no_raw():
    out, notes = validate(_ok(l1="Supply Partner Issue", l2="Guide No Show"))
    assert (out["l1"], out["l2"]) == ("Supply Partner Issue", "Guide No Show")
    assert out["l1_raw"] is None and out["l2_raw"] is None
    assert not any("taxonomy" in n for n in notes)


# ── it must never take the whole RCA down ───────────────────────────────────

def test_garbage_in_does_not_raise():
    for bad in (None, [], "not json", 42, {"what_went_wrong": "nope"}):
        out, notes = validate(bad)
        assert isinstance(out, dict) and isinstance(notes, list)


def test_the_document_level_v3_fields_are_not_carried_forward():
    """what_happened / root_causes / fixes moved onto the issues. Emitting
    them at document level again would render nothing and hide the move."""
    out, _ = validate(_ok(what_went_wrong={
        "what_happened": "old shape", "root_causes": ["old"],
        "guest_issues": [{"issue": "x", "root_cause": "y"}]}))
    assert set(out["what_went_wrong"]) == {"guest_issues"}
