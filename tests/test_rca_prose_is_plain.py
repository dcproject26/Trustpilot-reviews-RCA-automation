"""System vocabulary must not reach a sentence a person reads.

WHAT LANDED ON A REAL CARD:

    "The booking was cancelled and the refund confirmed on 31 May;
     ticket_mail_seen is false, which is consistent with the guest not having
     received or opened the cancellation communication before calling."

    "11 same-day support contacts out of 66 bookings on vid 6057 in the 90-day
     window; 67 vendor-cancelled bookings on this variant in the same window."

A column name and an unnamed internal id, printed at a CX associate who then
decodes our storage mid-RCA. prompts.py rule 2h forbids it — and a prompt rule
is unverifiable, which is why the check exists as well: nothing in this tree
can prove a model obeyed an instruction, and "we told it not to" is not a
guarantee.

It REPORTS, it does not rewrite. A regex editing model prose would mangle the
sentence, and a silent rewrite is worse than the jargon it removes.
"""
from server.services.rca_v4_validate import jargon_hits, jargon_note, validate


def _issue(**kw):
    base = {"issue": "The guide never arrived",
            "claim": "the guide didn't show up",
            "root_cause": "The SP did not send a guide to the meeting point."}
    base.update(kw)
    return base


# ── the tokens from the real card ───────────────────────────────────────────

def test_a_column_name_in_an_analysis_sentence_is_caught():
    hits = jargon_hits([_issue(claim_accuracy_note=
        "ticket_mail_seen is false, which is consistent with the guest not "
        "having opened the cancellation.")])
    toks = {t for _w, t, _y in hits}
    assert "ticket_mail_seen" in toks, hits


def test_an_internal_id_with_no_name_on_it_is_caught():
    hits = jargon_hits([_issue(pattern="67 vendor-cancelled bookings on vid 6057.")])
    assert any("6057" in t for _w, t, _y in hits), hits


def test_boolean_field_talk_is_caught():
    hits = jargon_hits([_issue(root_cause="The refund flag is false.")])
    assert any(y == "boolean field talk" for _w, _t, y in hits), hits


# ── it says WHERE, because the point is to go and fix the line ──────────────

def test_the_note_names_the_field_not_just_the_count():
    note = jargon_note([_issue(sop_gap="No process set ticket_mail_seen.")])
    assert "sop_gap" in note, note
    assert "ticket_mail_seen" in note, note
    assert note.startswith("1 system token"), note


def test_the_issue_number_is_named_so_a_reader_can_find_the_block():
    hits = jargon_hits([_issue(), _issue(pattern="seen on vid 6057")])
    assert all(w.startswith("issue 2") for w, _t, _y in hits), hits


def test_more_hits_than_shown_are_counted_not_dropped():
    """Three examples and a count. Truncating to three and saying nothing
    about the rest is the silent-zero bug in miniature."""
    note = jargon_note([_issue(
        claim_accuracy_note="ticket_mail_seen and booking_ref and pax_count",
        pattern="on vid 6057", sop_gap="order_state was not set")])
    assert "more" in note, note


# ── it does not cry wolf ────────────────────────────────────────────────────

def test_ordinary_analysis_prose_is_left_alone():
    """Over-flagging trains a reader to ignore the trail, which costs more
    than the jargon does."""
    assert jargon_hits([_issue(
        claim_accuracy_note="The cancellation email was never opened.",
        pattern="67 of this variant's bookings were cancelled in 90 days.",
        sop_gap="Nobody was required to warn the guest when the SP cancelled.",
        fix={"action": "Contact the guest before they travel.",
             "because": "They flew to Rome and found out at the venue."},
    )]) == []


def test_hyphenated_english_is_not_a_field_name():
    assert jargon_hits([_issue(pattern="11 same-day, day-of-visit contacts.")]) == []


def test_a_reference_the_associate_can_search_is_a_fact_not_jargon():
    """Counts, dates and real references stay — they are what the finding is
    made of. Only OUR spelling of a value is the problem."""
    assert jargon_hits([_issue(
        root_cause="SP confirmed the 12:30 slot on 30 Jul; ref RSZV JK8.",
        pattern="ZD-34807896 was the only contact in the window.")]) == []


def test_fix_source_is_not_scanned():
    """fix.source records where a gap was READ and never renders. A field name
    there is correct, and flagging it would be noise about a line no reader
    ever sees."""
    assert jargon_hits([_issue(fix={"action": "Warn the guest.",
                                    "source": "booking.ticket_mail_seen"})]) == []


# ── the wiring, which is the half that has been missing before ──────────────

def test_validate_puts_the_finding_in_the_notes_it_returns(monkeypatch):
    """CLAUDE.md opens with a validator wired into no path. The notes are what
    pipeline.py turns into confidence-trail rows, so this is the join that
    matters — a green unit test on jargon_note proves nothing about the card.
    """
    rca = {"what_went_wrong": {"guest_issues": [
        _issue(claim_accuracy_note="ticket_mail_seen is false.")]}}
    _out, notes = validate(rca)
    assert any("ticket_mail_seen" in n for n in notes), notes


def test_clean_prose_adds_no_note_at_all():
    """The counterpart. A check that speaks on every run is one nobody reads."""
    rca = {"what_went_wrong": {"guest_issues": [_issue()]}}
    _out, notes = validate(rca)
    assert not any("system token" in n for n in notes), notes


def test_the_finding_reaches_the_confidence_trail_as_a_warning():
    """Not `pass`. "We are not happy with this line" reported as "a step
    succeeded" is the specific miswording CLAUDE.md calls out."""
    from server.pipeline import validation_trail_rows
    rows = validation_trail_rows(jargon_note(
        [_issue(pattern="on vid 6057")]) and
        [jargon_note([_issue(pattern="on vid 6057")])])
    assert rows and all(r["mark"] == "warn" for r in rows), rows


def test_a_malformed_issue_list_does_not_take_the_run_down():
    """Whatever the model returns, this is a scan and must never raise —
    losing an RCA to a check about wording would be absurd."""
    assert jargon_hits(None) == []
    assert jargon_hits(["not a dict", 7, None]) == []
    assert jargon_hits([{"evidence": ["not a dict"], "fix": "not a dict"}]) == []


# ── a fix that lands on two desks is two fixes ──────────────────────────────
# OFF THE SAME CARD, under fix.owner = CO:
#
#   "Escalate to SP for a written explanation of the same-day cancellation,
#    then raise the recurring pattern with BIZ for commercial review."
#
# The half after the comma is a commercial decision about the vendor. It fixes
# nothing that happened to this guest, and because it is welded to a fix owned
# by CO, nobody at BIZ is ever told. One tracked action, one that only looks
# tracked.

from server.services.rca_v4_validate import fix_scope_hits, fix_scope_note


def test_a_fix_naming_two_other_teams_is_flagged():
    hits = fix_scope_hits([_issue(fix={
        "owner": "CO",
        "action": "Escalate to SP for a written explanation, then raise the "
                  "recurring pattern with BIZ for commercial review."})])
    assert hits, "a two-desk fix went through as one"
    assert "BIZ" in hits[0][1] and "SP" in hits[0][1], hits


def test_addressing_one_other_team_is_normal_and_not_flagged():
    """CO escalating to SP is how an action gets addressed. Flagging it would
    train the reader to ignore the trail."""
    assert fix_scope_hits([_issue(fix={
        "owner": "CO",
        "action": "Escalate to SP for a written explanation of the "
                  "same-day cancellation."})]) == []


def test_naming_the_guest_is_not_a_second_desk():
    """"Offer the guest an alternative" names who the fix is FOR, not a team
    the work is assigned to.

    UPPER CASE ON PURPOSE. GUEST is one of the nine team tokens, and the model
    writes that vocabulary in caps — so this is the spelling that actually
    reaches the check. A lower-case "guest" never matched the pattern at all,
    which meant the exclusion was untested and a mutation deleting it survived:
    an untested guard and a missing one look identical the day they matter."""
    assert fix_scope_hits([_issue(fix={
        "owner": "CO",
        "action": "Alert the GUEST and offer a same-day alternative whenever "
                  "an SP cancels on the visit date."})]) == [], \
        "naming the guest was counted as a second desk"
    # ...and the ordinary lower-case sentence, which must also pass.
    assert fix_scope_hits([_issue(fix={
        "owner": "CO",
        "action": "Alert the guest and offer a same-day alternative whenever "
                  "an SP cancels on the visit date."})]) == []


def test_the_owners_own_team_does_not_count_against_it():
    assert fix_scope_hits([_issue(fix={
        "owner": "CO",
        "action": "CO contacts the guest before they travel."})]) == []


def test_the_note_says_which_teams_and_what_to_do():
    note = fix_scope_note([_issue(fix={
        "owner": "CO",
        "action": "Escalate to SP, then raise with BIZ for commercial review."})])
    assert "BIZ" in note and "SP" in note, note
    assert "Split it" in note, "the note reports without saying what to do"


def test_a_fix_that_is_not_a_dict_does_not_take_the_run_down():
    assert fix_scope_hits([{"fix": "not a dict"}, {"fix": None}, {}]) == []
    assert fix_scope_hits(None) == []


def test_the_finding_reaches_the_notes_validate_returns():
    """The wiring, again. A check nobody calls is the failure CLAUDE.md opens
    with."""
    # claim_accuracy has to be a verdict that KEEPS the fix: under Unknown with
    # no case side, validate nulls root_cause and fix outright (rule 2a), and a
    # fix that has already been dropped is not one worth flagging.
    rca = {"what_went_wrong": {"guest_issues": [_issue(
        claim_accuracy="Accurate",
        fix={"owner": "CO",
             "action": "Escalate to SP, then raise with BIZ for commercial "
                       "review."})]}}
    _out, notes = validate(rca)
    assert any("more than one team" in n for n in notes), notes


# ── an internal code used as a bare word ────────────────────────────────────

def test_a_code_used_as_a_noun_is_caught_even_with_no_number():
    """"against a TGID rate of 5.0%" reached a real card. The id pattern misses
    it because it wants a number after the code."""
    hits = jargon_hits([_issue(pattern=
        "The cancellation rate for this variant is 6.4% against a TGID rate of 5.0%.")])
    assert any(t.upper() == "TGID" for _w, t, _y in hits), hits


def test_ordinary_words_are_not_mistaken_for_codes():
    """The list is explicit and word-bounded on purpose: a check that fires on
    normal English is one the reader learns to skip."""
    assert jargon_hits([_issue(
        root_cause="The guide did not arrive at the meeting point.",
        pattern="Sixty-seven bookings were cancelled by the SP in 90 days.",
    )]) == []


# ── a check about wording must never cost an RCA ────────────────────────────
# THIS ONE ESCAPED INTO THE SUITE. fix_scope_hits did
# `_clean(fx.get("owner")).upper()`, and _clean returns None — not "" — for a
# non-value. An absent owner is not exotic: validate() DROPS an owner it cannot
# map to the nine teams, so the commonest way to reach that line is with owner
# already None. It raised, validate()'s caller logged "keeping raw output", and
# the card would have shown unvalidated model text because of a check about
# how a sentence is phrased.

def test_a_fix_with_no_owner_does_not_raise():
    """The exact shape validate() produces after dropping an invented owner."""
    assert fix_scope_hits([_issue(fix={"owner": None,
                                       "action": "Escalate to SP and BIZ."})])


def test_an_owner_that_is_a_non_value_string_does_not_raise():
    for junk in ("", "  ", "N/A", "-", "none", "null"):
        fix_scope_hits([_issue(fix={"owner": junk, "action": "Ask SP and BIZ."})])


def test_a_non_string_action_is_skipped_rather_than_matched():
    """A model returning a list where a sentence belongs must not reach a
    regex."""
    assert fix_scope_hits([_issue(fix={"owner": "CO", "action": ["a", "b"]})]) == []
    assert fix_scope_hits([_issue(fix={"owner": "CO", "action": 7})]) == []


def test_a_non_string_prose_field_is_skipped_by_the_jargon_scan():
    assert jargon_hits([_issue(root_cause=["a list"], pattern=7,
                               claim_accuracy_note={"a": 1})]) == []


def test_validate_survives_every_junk_fix_shape():
    """Driven through validate, because that is where the exception actually
    cost something: the caller catches it and keeps RAW MODEL OUTPUT."""
    for fx in (None, "a string", 7, [], {}, {"owner": None}, {"action": None},
               {"owner": None, "action": None}, {"owner": 1, "action": 2}):
        rca = {"what_went_wrong": {"guest_issues": [
            _issue(claim_accuracy="Accurate", fix=fx)]}}
        validate(rca)          # must not raise
