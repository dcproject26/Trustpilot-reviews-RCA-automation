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
            "claim_accuracy": "Accurate",
            "fix": {"action": "Watch the fulfilment queue", "owner": "RO"},
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
            {"issue": "x", "claim": "the guest said so", "claim_accuracy": raw,
             "root_cause": "y",
             "fix": {"action": "do the thing", "owner": "CE"}}]}))
        got = out["what_went_wrong"]["guest_issues"][0]["claim_accuracy"]
        assert got == want, f"{raw!r} → {got!r}, expected {want!r}"


def test_a_sentence_verdict_keeps_its_tail_instead_of_losing_it():
    """"Partially True — booking status shows…" put a sentence in a 140px
    chip. The verdict is the enum; the reasoning belongs in the note."""
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [{
        "issue": "x", "root_cause": "y", "fix": {"action": "do the thing", "owner": "CE"},
        "claim_accuracy": "Partially True — booking status shows a refund",
    }]}))
    iss = out["what_went_wrong"]["guest_issues"][0]
    assert iss["claim_accuracy"] == "Partly accurate"
    assert "booking status shows a refund" in iss["claim_accuracy_note"]
    assert iss["claim_accuracy_raw"], "the model's raw string must stay recoverable"


def test_an_unrecognisable_verdict_becomes_unknown_not_a_raw_pill():
    out, notes = validate(_ok(what_went_wrong={"guest_issues": [
        {"issue": "x", "claim": "the guest said so", "root_cause": "y", "fix": {"action": "do the thing", "owner": "CE"}, "claim_accuracy": "probably fine tbh"}]}))
    assert out["what_went_wrong"]["guest_issues"][0]["claim_accuracy"] == "Unknown"
    assert any("claim_accuracy" in n for n in notes)


def test_the_guest_quote_is_never_trimmed():
    """claim is verbatim at whatever length the guest wrote it — the Claim
    block is built to wrap it. The 8-16 word finding rule does not apply."""
    long_quote = ("I booked this weeks ago and nobody told me the tickets would "
                  "take two hours to arrive, which meant we stood outside in the "
                  "rain with two small children for the whole afternoon.")
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [
        {"issue": "x", "root_cause": "y", "fix": {"action": "do the thing", "owner": "CE"}, "claim": long_quote}]}))
    assert out["what_went_wrong"]["guest_issues"][0]["claim"] == long_quote


# ── evidence: structured, never a prefix in the sentence ────────────────────

def test_a_legacy_string_evidence_becomes_a_row():
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [{
        "issue": "x", "claim": "the guest said so", "root_cause": "y",
        "fix": {"action": "do the thing", "owner": "CE"},
        "evidence": ["[booking] The booking shows two adult tickets."]}]}))
    ev = out["what_went_wrong"]["guest_issues"][0]["evidence"][0]
    assert ev["text"] == "The booking shows two adult tickets."
    assert ev["source"] == "booking", "the prefix belongs in source, not the sentence"
    assert ev["ref"] is None


def test_a_source_prefix_is_stripped_out_of_the_sentence():
    """source and ref are structured fields; a prefix inside text renders the
    marker twice, once as the rail and once in the words."""
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [{
        "issue": "x", "claim": "the guest said so", "root_cause": "y",
        "fix": {"action": "do the thing", "owner": "CE"},
        "evidence": [{"text": "[insights] Twelve similar reviews in 90 days.",
                      "source": "insights", "ref": None}]}]}))
    assert out["what_went_wrong"]["guest_issues"][0]["evidence"][0]["text"] \
        == "Twelve similar reviews in 90 days."


def test_an_unknown_source_becomes_null_rather_than_a_broken_rail():
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [{
        "issue": "x", "claim": "the guest said so", "root_cause": "y",
        "fix": {"action": "do the thing", "owner": "CE"},
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
    out, _ = validate(_ok(
        what_went_wrong={"guest_issues": [{
            "issue": "Voucher never delivered", "claim": "I waited two hours.",
            "claim_accuracy": "Accurate",
            "operational_failure": "The fulfilment run failed with no alert",
            "evidence": []}]},
        area_of_improving=[
            {"point": "• Add a delivery window to the page",
             "from": "operational_failure",
             "source": "The fulfilment run failed with no alert"},
            {"point": "1. Alert on failed fulfilment",
             "from": "operational_failure",
             "source": "the fulfilment run failed with no alert"}]))
    assert [a["point"] for a in out["area_of_improving"]] == [
        "Add a delivery window to the page", "Alert on failed fulfilment"]


def test_an_empty_contact_is_not_dressed_as_a_data_row():
    """"01 · UNKNOWN · Unknown · No guest contact found" was a nothing-found
    message rendered as a numbered contact. The empty state says it better."""
    out, _ = validate(_ok(support_interaction=[
        {"channel": "Unknown", "time": "Unknown",
         "summary": "No guest contact found on this booking"}]))
    assert out["support_interaction_notes"] == []


def test_a_real_contact_survives():
    out, _ = validate(_ok(support_interaction=[
        {"channel": "chat", "time": "22 Jul 15:41",
         "summary": "Guest asked where the tickets were.", "ce_miss": None}]))
    assert len(out["support_interaction_notes"]) == 1


# ── enums elsewhere ─────────────────────────────────────────────────────────

def test_takedown_falls_back_inside_its_enum():
    out, _ = validate(_ok(takedown={"verdict": "maybe"}))
    assert out["takedown"]["verdict"] == "Untraceable"


def test_the_removed_sections_are_not_projected():
    """TL;DR and SOP compliance were removed from the RCA. A model still
    emitting them — an older prompt cached somewhere, a hand-edited draft —
    must not put them back into the projection, or the UI grows a section
    nobody asked for and the reader cannot tell it from a live one."""
    out, _ = validate(_ok(tldr={"our_mistake": "x", "our_fix": "y"},
                          sop_compliance={"verdict": "deviated"}))
    assert "tldr" not in out
    assert "sop_compliance" not in out


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


def test_a_flag_team_outside_the_vocabulary_becomes_other():
    """The UI renders team as a chip-select over the closed list, so the
    fallback has to be a real member of it. A null would blank the control;
    the raw value would add a stray option to it.

    OTHER is not a tenth team. It is the marker for a flag whose team could not
    be read, and it raises nothing — which is why the model's word has to stay
    recoverable in team_raw."""
    out, notes = validate(_ok(flags=[
        {"team": "Growth", "flag": "No alert on failed fulfilment",
         "evidence": "Three retries, no page."},
        {"team": "content", "flag": "Page states no delivery window",
         "evidence": "Experience page."}]))
    assert out["flags"][0]["team"] == "OTHER"
    assert out["flags"][0]["team_raw"] == "Growth", "the model's word must stay recoverable"
    assert out["flags"][1]["team"] == "CONTENT", "case is not a reason to lose the team"
    assert out["flags"][1]["team_raw"] is None, "a valid value has no raw to keep"
    assert out["flags"][0]["flag"], "the flag itself must survive its bad team"
    assert any("Growth" in n for n in notes)


def test_a_flag_from_the_old_vocabulary_reaches_the_team_that_owns_it_now():
    """CE and RO were both the support desk and are the CO team now. Left to
    fail the enum they would land on OTHER, which raises nothing — a real flag
    against a real team, silently unrouted. The translation is REPORTED,
    because a coercion nobody can see is a silent edit."""
    out, notes = validate(_ok(flags=[
        {"team": "CE", "flag": "First reply after SLA", "evidence": "40 minutes."},
        {"team": "RO", "flag": "Vendor issue never raised", "evidence": "No ticket."},
        {"team": "Business", "flag": "Recurring on this TID-VID", "evidence": "9 in 30d."}]))
    assert [f["team"] for f in out["flags"]] == ["CO", "CO", "BIZ"]
    # Scoped to FLAG notes. `fix.owner` is translated through the same aliases
    # and emits "→ CO" too, so counting the arrow anywhere counted a different
    # coercion and broke the moment owners joined the nine.
    _flag_notes = [n for n in notes if n.startswith("flag team")]
    assert sum("→ CO" in n for n in _flag_notes) == 2, _flag_notes
    assert any("→ BIZ" in n for n in _flag_notes), _flag_notes


def test_a_note_may_carry_a_time_and_a_channel_but_never_invent_the_channel():
    """This used to assert the fields were struck from the schema outright,
    because on a real run the model left both null while its prose said "chat
    at 15:41 IST" — a field the model must not fill is a field it will fill.

    That was right about PRECEDENCE and wrong about PRESENCE. A contact with
    no Zendesk frame — a call the guest describes, an off-Zendesk exchange —
    has no frame to take a time from, so striking the field rendered a dash:
    the same dash a broken lookup renders, on a contact where we knew the
    answer perfectly well.

    So the fields are back and the guard moved. The model may state them; the
    frame's value wins wherever a frame exists (driven in the browser, in
    tests/test_contact_narrative_ui.py), and the channel is still closed to a
    vocabulary so an invented one cannot render as a pill.
    """
    out, notes = validate(_ok(support_interaction_notes=[
        {"zd_ref": "ZD-1", "channel": "whatsapp", "time": "22 Jul 15:41",
         "summary": "Guest asked where the tickets were."}]))
    row = out["support_interaction_notes"][0]
    assert row["time"] == "22 Jul 15:41", \
        "an off-Zendesk contact loses the one timestamp we had for it"
    assert row["channel"] is None, \
        "'whatsapp' is not in the frame vocabulary and rendered as a pill"
    assert row["channel_raw"] == "whatsapp", "the model's word was thrown away"
    assert any("whatsapp" in n for n in notes), \
        "the channel was coerced with no word to the reader"
    assert row["summary"], "the interpretation still has to survive"


def test_an_sp_record_cannot_carry_a_time_either():
    out, _ = validate(_ok(sp_interaction_notes={
        "raised": "Yes",
        "records": [{"zd_ref": "ZD-7", "time": "22 Jul 16:02", "summary": "asked"}]}))
    assert "time" not in out["sp_interaction_notes"]["records"][0]


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
    """what_happened / root_causes are v3 shapes with no renderer. Emitting
    them again would render nothing and hide the move.

    `fixes` IS BACK AT DOCUMENT LEVEL, DELIBERATELY. It was moved onto the
    issues on the reasoning that a document-level array "would render nothing"
    — true then, false now: §3 is its own section and Actions Taken is a view
    over exactly this array, so a fix here is neither inert nor hidden. The
    per-issue `fix` is still read, and migrated, for drafts written before the
    section existed."""
    out, _ = validate(_ok(what_went_wrong={
        "what_happened": "old shape", "root_causes": ["old"],
        "guest_issues": [{"issue": "x", "root_cause": "y", "fix": {"action": "do the thing", "owner": "CE"}}]}))
    assert set(out["what_went_wrong"]) == {"guest_issues", "fixes"}
    assert "what_happened" not in out["what_went_wrong"]
    assert "root_causes" not in out["what_went_wrong"]


def test_a_pre_restructure_fix_is_migrated_into_the_fixes_array():
    """A draft written before §3 existed keeps its fix on the issue. Reading
    it as a case with no fixes would render "Nothing to fix" beside an issue
    that names one — and would empty that team's Actions Taken tab."""
    out, notes = validate(_ok(what_went_wrong={"guest_issues": [{
        "issue": "x", "claim": "the guest said so", "claim_accuracy": "Accurate",
        "root_cause": "y",
        "fix": {"action": "Watch the fulfilment queue", "owner": "TECH"}}]}))
    fixes = out["what_went_wrong"]["fixes"]
    assert [f["action"] for f in fixes] == ["Watch the fulfilment queue"], fixes
    assert fixes[0]["owner"] == "TECH", fixes
    assert out["actions_taken"]["tech"] == ["Watch the fulfilment queue"]
    assert any("old per-issue shape" in n for n in notes), \
        "a rewrite of what was stored must be reported"


def test_an_issue_with_no_diagnosis_contributes_no_fix_to_migrate():
    """`_diagnosable` already drops a fix under an Unknown verdict with
    nothing in the case. The migration reads the VALIDATED issues, so it
    inherits that rather than resurrecting a fix the validator removed."""
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [
        {"issue": "x", "root_cause": "y",
         "fix": {"action": "do the thing", "owner": "CE"}}]}))
    assert out["what_went_wrong"]["fixes"] == []


# ── the model writes interpretation, under its own key ──────────────────────

def test_the_model_output_lands_on_the_notes_key():
    """Under the frames' key, presence-based reading would reverse the
    pipeline's precedence. A distinct key makes that structurally impossible."""
    out, _ = validate(_ok(support_interaction_notes=[
        {"zd_ref": "ZD-4491", "summary": "Guest chased the voucher.",
         "ce_miss": "No proactive update after the first failure."}]))
    assert out["support_interaction_notes"][0]["zd_ref"] == "ZD-4491"
    assert "support_interaction" not in out, \
        "the model must not emit anything under the frames' key"


def test_the_pre_split_key_is_still_read():
    """Drafts written before the split, and a model still reaching for the old
    name, keep their interpretation rather than losing it silently."""
    out, _ = validate(_ok(support_interaction=[
        {"zd_ref": "ZD-1", "summary": "Guest asked where the tickets were."}]))
    assert out["support_interaction_notes"][0]["summary"]


def test_an_unverified_contact_is_still_reportable():
    """A contact the model saw that Zendesk has no ticket for. It has no
    verified time or channel by definition — zd_ref: null is what marks it."""
    out, _ = validate(_ok(support_interaction_notes=[
        {"zd_ref": None, "summary": "Guest says they phoned and got no answer."}]))
    row = out["support_interaction_notes"][0]
    assert row["zd_ref"] is None
    assert row["summary"] == "Guest says they phoned and got no answer."


def test_sp_notes_land_on_their_own_key_too():
    out, _ = validate(_ok(sp_interaction_notes={
        "raised": "Yes", "records": [{"zd_ref": "ZD-7", "summary": "Operator confirmed."}]}))
    assert out["sp_interaction_notes"]["raised"] == "Yes"
    assert out["sp_interaction_notes"]["records"][0]["zd_ref"] == "ZD-7"
    assert "sp_interaction" not in out


# ── a repair that did not happen must not be reported as one ────────────────

def test_a_legitimate_unknown_verdict_is_not_reported_as_a_coercion():
    """"claim_accuracy 'Unknown' → Unknown" put a warn on the trail for a model
    that did exactly what it was asked. That is the silent-failure bug
    inverted, and it costs the same thing: a reader who stops believing the
    trail."""
    _, notes = validate(_ok(what_went_wrong={"guest_issues": [
        {"issue": "x", "claim": "the guest said so", "root_cause": "y", "fix": {"action": "do the thing", "owner": "CE"}, "claim_accuracy": "Unknown"}]}))
    assert not any("claim_accuracy" in n for n in notes), notes


def test_a_case_only_difference_is_not_a_coercion_either():
    _, notes = validate(_ok(what_went_wrong={"guest_issues": [
        {"issue": "x", "claim": "the guest said so", "root_cause": "y", "fix": {"action": "do the thing", "owner": "CE"}, "claim_accuracy": "accurate"}]}))
    assert not any("claim_accuracy" in n for n in notes), notes


def test_a_real_coercion_is_still_reported():
    _, notes = validate(_ok(what_went_wrong={"guest_issues": [
        {"issue": "x", "claim": "the guest said so", "root_cause": "y", "fix": {"action": "do the thing", "owner": "CE"}, "claim_accuracy": "probably fine"}]}))
    assert any("claim_accuracy" in n for n in notes)


# ── the ceilings are checkable, so they are checked ─────────────────────────

def test_an_over_long_reply_is_counted_and_named():
    """The model overshot 120 by 35% on a real run. Not truncated — cutting a
    guest-facing apology mid-sentence is worse than a long one — but said."""
    _, notes = validate(_ok(suggested_response="word " * 162))
    assert any("suggested_response is 162 words, over the 120-word ceiling" in n
               for n in notes)


def test_an_over_long_stated_issue_is_counted_too():
    _, notes = validate(_ok(stated_issue="word " * 84))
    assert any("stated_issue is 84 words" in n for n in notes)


def test_a_reply_inside_its_ceiling_says_nothing():
    _, notes = validate(_ok(suggested_response="word " * 90))
    assert not any("ceiling" in n for n in notes)


# ── "N/A" is only an answer when it says why ────────────────────────────────

def test_the_sp_reason_survives():
    """raised N/A with no records and no reason is indistinguishable from a
    section the model skipped."""
    out, _ = validate(_ok(sp_interaction_notes={
        "raised": "N/A", "reason": "the vendor is not a partnered SP",
        "records": []}))
    assert out["sp_interaction_notes"]["reason"] == "the vendor is not a partnered SP"


# ── our findings are not the guest's complaints ─────────────────────────────

def _finding(**over):
    """A finding about US wearing a guest issue's clothes: no claim, no owner,
    no operational failure.

    `owner` is a kwarg for the caller's convenience and lands on the FIX,
    which is where owner lives now. Passing it at the top level would set a
    key nothing reads, and the test would then prove the opposite of its name.
    """
    owner = over.pop("owner", None)
    base = {"issue": "Out-of-policy refund issued after booking was non-refundable",
            "claim": None, "fix": None, "operational_failure": None,
            "root_cause": "The refund was a discretionary exception by CE.",
            "evidence": [{"text": "is_cancellable is false.", "source": "exp-page",
                          "ref": "https://x/22238"}]}
    base.update(over)
    if owner:
        base["fix"] = {"action": "Review the refund exception", "owner": owner}
    return base


def test_a_claim_less_ownerless_finding_leaves_guest_issues():
    """It arrived as numbered guest issue 04 with an empty Claim block, and
    leadership reads that as something the guest said. They did not."""
    out, notes = validate(_ok(what_went_wrong={"guest_issues": [
        _ok()["what_went_wrong"]["guest_issues"][0], _finding()]}))
    titles = [i["issue"] for i in out["what_went_wrong"]["guest_issues"]]
    assert "Out-of-policy refund issued after booking was non-refundable" not in titles
    assert any("moved to flags" in n for n in notes)


def test_the_finding_survives_as_a_flag():
    """Demoting must not delete it — it is a real finding, filed wrongly."""
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [_finding()]},
                          flags=[]))
    f = out["flags"][0]
    assert f["flag"] == "Out-of-policy refund issued after booking was non-refundable"
    assert f["team"] == "OTHER"
    # The flag carries the issue's EVIDENCE, not its root cause — the record
    # that showed it, which is what the team it lands on has to check.
    assert "is_cancellable is false" in f["evidence"]
    assert f["zd_ref"] == "https://x/22238"


def test_a_finding_that_duplicates_a_flag_is_dropped_not_doubled():
    """The model raises the same finding twice, once in each section."""
    out, notes = validate(_ok(
        what_went_wrong={"guest_issues": [_finding()]},
        flags=[{"team": "CE",
                "flag": "Out-of-policy refund issued after booking was non-refundable",
                "evidence": "Exception approved by agent Avi."}]))
    assert len(out["flags"]) == 1
    assert any("duplicated an existing flag" in n for n in notes)


def test_an_issue_with_a_claim_is_never_demoted():
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [
        _finding(claim="they refunded me but never said why")]}))
    assert len(out["what_went_wrong"]["guest_issues"]) == 1


def test_an_issue_with_an_owner_is_never_demoted():
    """An owner means somebody decided this is a guest issue with a fix. The
    verdict has to be diagnosable, because Inaccurate and Unknown null the fix
    — and a nulled fix takes its owner with it."""
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [
        _finding(owner="CE", claim_accuracy="Accurate")]}))
    assert len(out["what_went_wrong"]["guest_issues"]) == 1


def test_an_issue_with_an_operational_failure_is_never_demoted():
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [
        _finding(operational_failure="the agent skipped the check",
                 claim_accuracy="Accurate")]}))
    assert len(out["what_went_wrong"]["guest_issues"]) == 1


def test_an_undiagnosable_verdict_leaves_nothing_to_hold_a_claimless_issue():
    """The interaction the two tests above had to be corrected for, pinned so
    it is a decision rather than a surprise.

    Inaccurate and Unknown null root_cause, operational_failure, sop_gap and
    fix. An issue with no claim then has nothing marking it as the GUEST's —
    no claim, no owner, no operational failure — so it is filed as our finding
    instead. That is right: "we cannot tell, and the guest never said it" is
    not a guest issue. It matters that it is deliberate, because the same
    three nulls used to mean something else.
    """
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [
        _finding(owner="CE", claim_accuracy="Unknown")]}))
    assert out["what_went_wrong"]["guest_issues"] == []
    assert out["flags"], "the finding was deleted rather than re-filed"


def test_a_claim_keeps_an_undiagnosable_issue_where_it_is():
    """The guest DID say it, and we could not settle it. That is a guest issue
    with an Unknown verdict, not a finding about us."""
    out, _ = validate(_ok(what_went_wrong={"guest_issues": [
        _finding(claim="the guide was rude", claim_accuracy="Unknown")]}))
    assert len(out["what_went_wrong"]["guest_issues"]) == 1


def test_a_routed_scenario_coverage_row_is_exempt():
    """Rule 13 REQUIRES a claim-less issue for a routed scenario the data does
    not support, and it will often have no owner either — the same three nulls
    for the opposite reason. Demoting it deletes the audit trail."""
    out, _ = validate(
        _ok(what_went_wrong={"guest_issues": [
            {"issue": "Refund not processed", "claim": None, "fix": None,
             "operational_failure": None, "claim_accuracy": "Inaccurate",
             "root_cause": "The booking data shows the refund settled on 23 Jul."}]}),
        scenarios_routed=["Refund not processed"])
    assert [i["issue"] for i in out["what_went_wrong"]["guest_issues"]] \
        == ["Refund not processed"]


def test_the_demotion_is_never_silent():
    """Silently rewriting what the model returned is the thing the trail
    exists to prevent."""
    _, notes = validate(_ok(what_went_wrong={"guest_issues": [_finding()]}))
    assert notes and any("not the guest's" in n for n in notes)
