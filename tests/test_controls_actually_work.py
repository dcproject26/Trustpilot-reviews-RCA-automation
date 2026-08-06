"""Every control in the RCA column does what it looks like it does.

Three of them did not, and all three failed the same way: the control
rendered, looked correct, and was bound to nothing. There is no visual
difference between a wired button and an unwired one, so none of them were
noticed until someone clicked.

  * the section collapse — step 5 put a hardcoded chevron in most section
    templates, and the post-render pass read "already has a chevron" as
    "already wired" and returned before binding;
  * the internal-events toggle — §13 moved the timeline into the RCA column
    and left the binding querying the review column;
  * "+ Add" — see below.

This file clicks things. It is the only kind of test that can tell a live
control from a dead one.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


# Issue-specific answers is not here because the section is gone (§3), not
# because its collapse was flaky. A selector left in this list after its
# section stopped rendering fails on a null and reads as a broken control.
SECTIONS = ["#rca-events-timeline-section", "#rca-flags-section",
            "#rca-wwr5-section"]


def _collapsed(page, sel):
    return page.evaluate(
        "(s) => { const e = document.querySelector(s);"
        "return e ? e.classList.contains('is-collapsed') : null; }", sel)


@pytest.mark.parametrize("sel", SECTIONS)
def test_every_section_label_collapses_when_clicked(page, sel):
    page.evaluate("(s) => { const e = document.querySelector(s);"
                  "e.classList.remove('is-collapsed'); }", sel)
    page.click(f"{sel} .section-label")
    page.wait_for_timeout(200)
    got = _collapsed(page, sel)
    page.click(f"{sel} .section-label")
    page.wait_for_timeout(200)
    back = _collapsed(page, sel)
    assert got is True, f"{sel} did not collapse — the chevron is decoration"
    assert back is False, f"{sel} collapsed and would not reopen"


def test_a_hardcoded_chevron_is_not_duplicated(page):
    """The guard that broke the binding was there to stop a second chevron
    being appended. Splitting the two jobs must not bring the duplicate back."""
    n = page.evaluate("""() => Math.max(...[...document.querySelectorAll(
      '#rca-col .section-label')].map(l => l.querySelectorAll('.section-chev').length))""")
    assert n == 1, f"a section label carries {n} chevrons"


def test_every_section_in_the_rca_column_is_collapsible(page):
    """Not a sample — all of them. The bug hit every section with a template
    chevron at once, so testing three would have looked like bad luck."""
    dead = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('#rca-col .section').forEach(sec => {
        const label = sec.querySelector('.section-label');
        if (!label) return;
        const before = sec.classList.contains('is-collapsed');
        label.click();
        if (sec.classList.contains('is-collapsed') === before)
          out.push((label.innerText || '').split('\\n')[0].trim());
        else label.click();
      });
      return out; }""")
    assert dead == [], f"these section labels are inert: {dead}"


# ── the + Add paths ─────────────────────────────────────────────────────────

ADDS = [
    ("[data-contact-add]", ".convo-frame"),
    ("[data-flag-add]", "#rca-flags-section .chk-row.chk-flag"),
    ("[data-aoi-add]", ".rca-point"),
    ("[data-sp-rec-add]", ".sp-frame"),
]


@pytest.mark.parametrize("btn,row", ADDS)
def test_every_add_button_produces_a_row(page, btn, row):
    """"+ Add" that silently does nothing is the same failure as a validator
    wired to nothing: the click is accepted, and the absence of a new row is
    indistinguishable from not having clicked."""
    if not page.evaluate("(s) => !!document.querySelector(s)", btn):
        pytest.skip(f"{btn} is not on this fixture")
    before = page.evaluate("(s) => document.querySelectorAll(s).length", row)
    page.click(btn)
    page.wait_for_timeout(700)
    after = page.evaluate("(s) => document.querySelectorAll(s).length", row)
    assert after == before + 1, \
        f"{btn} left the row count at {after} (was {before})"


def test_the_contact_count_matches_the_contacts_under_it(page):
    """The heading read "0 contacts" above eight rendered rows: it counted
    v3d.support_interaction while the rows came from the frames plus notes."""
    got = page.evaluate("""() => {
      const sec = [...document.querySelectorAll('#rca-col .section')].find(
        s => /guest . support/i.test(s.querySelector('.section-label')?.innerText || ''));
      if (!sec) return null;
      const hint = sec.querySelector('.hint');
      return {said: parseInt((hint?.innerText || '').trim()),
              shown: sec.querySelectorAll('.convo-frame').length}; }""")
    assert got, "the guest ↔ support section did not render"
    assert got["said"] == got["shown"], \
        f"the heading says {got['said']} contacts over {got['shown']} rows"


# ── an absent value says which lookup came up empty ─────────────────────────

def _guest_row(page):
    return page.evaluate("""() => {
      const rows = [...document.querySelectorAll('#review-col .detail-row')];
      const row = rows.find(r => /Primary guest/.test(r.querySelector('.k')?.innerText||''));
      if (!row) return null;
      const v = row.querySelector('.v');
      const abs = v.querySelector('.v-absent');
      return {text: v.innerText.trim(), absent: !!abs,
              colour: abs ? getComputedStyle(abs).color : null}; }""")


def _set_guest(page, name, note):
    page.evaluate("""([n, note]) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r._kG = r.booking.primaryGuestName; r._kN = r.booking.guestNameNote;
      r.booking.primaryGuestName = n; r.booking.guestNameNote = note;
      renderReviewCol(); }""", [name, note])


def _reset_guest(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r.booking.primaryGuestName = r._kG; r.booking.guestNameNote = r._kN;
      renderReviewCol(); }""")


def test_a_missing_guest_name_says_which_lookup_failed(page):
    """"[Guest name in Zendesk ticket]" was a sentence in the value column: it
    looked like data, and it made three situations identical — the warehouse
    holds a hash, the linked ticket has no requester, and no ticket was ever
    matched. Only the first two are worth opening Zendesk for."""
    _set_guest(page, "", "no Zendesk ticket was matched to this booking")
    got = _guest_row(page)
    _reset_guest(page)
    assert got, "the Primary guest row did not render"
    assert "[Guest name in Zendesk ticket]" not in got["text"]
    assert "no Zendesk ticket was matched" in got["text"]
    assert got["absent"], "the reason is styled as if it were the guest's name"


def test_the_reason_is_not_styled_as_a_value(page):
    _set_guest(page, "", "the warehouse stores this as a hash")
    absent = _guest_row(page)
    _set_guest(page, "Lewis MacAndrew", "")
    real = _guest_row(page)
    _reset_guest(page)
    assert absent["colour"] != real["colour"], \
        "an absence and a name are the same colour"
    assert not real["absent"]
    assert real["text"] == "Lewis MacAndrew"


def test_a_stale_placeholder_from_an_older_draft_is_not_shown_as_a_name(page):
    """Drafts written before this change hold the literal placeholder string in
    primaryGuestName. Rendering it verbatim would put the old bug back on
    screen for every one of them."""
    _set_guest(page, "[Guest name in Zendesk ticket]", "")
    got = _guest_row(page)
    _reset_guest(page)
    assert "[Guest name in Zendesk ticket]" not in got["text"], got


# ── + Add SP record, on the branch that actually decides it ─────────────────
#
# Counting rows was not enough. The handler mutates the records array in
# place, so once sp_interaction_notes exists on the draft it does not matter
# which key the assignment names — both spellings render. Two things separate
# them, and neither is a row count:
#
#   * a draft with NO notes object, where `|| {}` builds a fresh one and the
#     assignment target is the only thing that decides where it lands; and
#   * whether `raised` and `reason` survive, since replacing the object
#     wholesale loses them while leaving the count correct.

def _sp_state(page):
    return page.evaluate("""() => {
      const v = REVIEWS.find(x => x.id === state.selected).rca.v3;
      return {notes: v.sp_interaction_notes || null,
              frames: v.sp_interaction || null,
              rendered: document.querySelectorAll('.sp-frame').length}; }""")


def _sp_restore(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r.rca.v3.sp_interaction_notes = r.rca.v3._kSP;
      delete r.rca.v3._kSP; renderRcaCol(); }""")


def test_a_record_added_to_a_draft_with_no_notes_lands_where_it_renders(page):
    """Drafts written before the facts/interpretation split have no
    sp_interaction_notes at all. That is the case the handler was getting
    wrong, and the only one where the write target matters."""
    page.evaluate("""() => {
      const v = REVIEWS.find(x => x.id === state.selected).rca.v3;
      v._kSP = v.sp_interaction_notes;
      delete v.sp_interaction_notes; renderRcaCol(); }""")
    before = _sp_state(page)["rendered"]
    page.click("[data-sp-rec-add]")
    page.wait_for_timeout(700)
    got = _sp_state(page)
    _sp_restore(page)
    assert got["rendered"] == before + 1, \
        f"the record was written to a key nothing renders ({got['rendered']} rows)"
    assert got["notes"] and got["notes"]["records"], \
        "the record did not land under sp_interaction_notes"


def test_adding_a_record_does_not_discard_raised_and_reason(page):
    """"raised: N/A" with no reason is indistinguishable from a skipped
    section — the bug this section already had once. Replacing the notes
    object with a fresh one carrying only records brings it back, and leaves
    the row count correct while doing it."""
    page.evaluate("""() => {
      const v = REVIEWS.find(x => x.id === state.selected).rca.v3;
      v._kSP = v.sp_interaction_notes;
      v.sp_interaction_notes = {raised: 'Yes', reason: 'Vendor is not partnered.',
                                records: [{time: '', summary: 'first', zd_ref: ''}]};
      renderRcaCol(); }""")
    page.click("[data-sp-rec-add]")
    page.wait_for_timeout(700)
    got = _sp_state(page)
    _sp_restore(page)
    n = got["notes"]
    assert n["raised"] == "Yes", f"raised was dropped: {n!r}"
    assert n["reason"] == "Vendor is not partnered.", f"reason was dropped: {n!r}"
    assert [r["summary"] for r in n["records"]][0] == "first", \
        "the existing record was replaced rather than appended to"
    assert len(n["records"]) == 2


def test_a_record_is_never_written_to_the_frames_key(page):
    """sp_interaction is the pipeline's Zendesk-derived facts. An operator's
    record is by definition one there is no ticket for; writing it there makes
    the model's account and the warehouse's disagree."""
    page.evaluate("""() => {
      const v = REVIEWS.find(x => x.id === state.selected).rca.v3;
      v._kSP = v.sp_interaction_notes;
      v.sp_interaction_notes = {raised: 'N/A', reason: 'r', records: []};
      renderRcaCol(); }""")
    before = page.evaluate(
        "() => JSON.stringify(REVIEWS.find(x=>x.id===state.selected).rca.v3.sp_interaction ?? null)")
    page.click("[data-sp-rec-add]")
    page.wait_for_timeout(700)
    after = page.evaluate(
        "() => JSON.stringify(REVIEWS.find(x=>x.id===state.selected).rca.v3.sp_interaction ?? null)")
    _sp_restore(page)
    assert after == before, \
        f"the facts key was written by the client: {before} -> {after}"


def test_the_sp_section_survives_a_draft_that_says_nothing_about_the_sp(page):
    """The block was null when the model had no SP account and no frames came
    back, so the section — and its "+ Add SP record" — was absent rather than
    empty. The brief's rule is that none of the nine add paths may be inert; a
    button that never renders is further from working than one that does
    nothing."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const kN = r.rca.v3.sp_interaction_notes, kI = r.rca.v3.sp_interaction;
      const kF = r.rca.spFrames, kS = r.rca.spInteraction;
      delete r.rca.v3.sp_interaction_notes; delete r.rca.v3.sp_interaction;
      r.rca.spFrames = []; r.rca.spInteraction = [];
      renderRcaCol();
      const sec = [...document.querySelectorAll('#rca-col .section')].find(
        s => /SP INTERACTION/i.test(s.querySelector('.section-label')?.innerText || ''));
      const out = {section: !!sec,
                   add: !!(sec && sec.querySelector('[data-sp-rec-add]')),
                   raised: !!(sec && sec.querySelector('[data-v3sel="sp_interaction_notes.raised"]')),
                   text: sec ? sec.innerText : ''};
      r.rca.v3.sp_interaction_notes = kN; r.rca.v3.sp_interaction = kI;
      r.rca.spFrames = kF; r.rca.spInteraction = kS; renderRcaCol();
      return out; }""")
    assert got["section"], "the SP section vanished entirely"
    assert got["add"], "+ Add SP record is not on the page at all"
    assert got["raised"], "there is nowhere to record whether the SP was raised"
    assert "No SP contact on record" in got["text"], \
        "an absent section and an empty one still read the same"


# ── a timeline of dashes says why ───────────────────────────────────────────
#
# Every `time` came back null on a real card, so the column rendered eight
# dashes. That is what a failed timestamp lookup looks like too. The cause was
# prompt rule 10: with no system events, the model builds the sequence from the
# guest's own account — and a guest narrates an order, not a clock. Rule 10b
# now has it say "undated" for exactly those, so the two can be told apart.

NARRATED = [{"time": "undated", "what": "Guest arrived", "detail": "Turned up at 12:30. (guest's account, unverified)"},
            {"time": "undated", "what": "Slot cancelled", "detail": "Told it was unavailable. (guest's account, unverified)"}]
MIXED = [{"time": "22 Jul 15:41", "what": "Booking created", "detail": None},
         {"time": "undated", "what": "Guest arrived", "detail": "(guest's account, unverified)"},
         {"time": None, "what": "Voucher issued", "detail": "No time recorded."}]
SILENT = [{"time": None, "what": "Voucher issued", "detail": None},
          {"time": None, "what": "Refund raised", "detail": None}]


def _timeline(page, logs):
    return page.evaluate("""(logs) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.rca.v3.booking_logs;
      r.rca.v3.booking_logs = logs; renderReviewCol();
      const sec = document.querySelector('#rca-booking-logs-section');
      const note = sec && sec.querySelector('.tl-undated-note');
      const out = {note: note ? note.innerText : null,
                   times: [...sec.querySelectorAll('.tl-time')].map(t => t.innerText.trim()),
                   colour: note ? getComputedStyle(note).color : null};
      r.rca.v3.booking_logs = keep; renderReviewCol();
      return out; }""", logs)


def test_undated_is_not_rendered_as_a_timestamp(page):
    """It is the model's answer to "when", not an answer to when."""
    got = _timeline(page, NARRATED)
    assert got["times"] == ["—", "—"], got["times"]


def test_a_wholly_narrated_timeline_says_there_was_never_a_clock(page):
    got = _timeline(page, NARRATED)
    assert got["note"], "eight dashes and no explanation, again"
    assert "guest's own account" in got["note"]
    assert "Not a failed lookup" in got["note"]


def test_a_mixed_timeline_splits_the_two_kinds_of_missing(page):
    """A narrated entry and a system event with no recorded time are different
    facts. Counting them together would make the honest half look broken."""
    got = _timeline(page, MIXED)
    assert got["note"], MIXED
    assert "1 of 2" in got["note"], got["note"]
    assert "not recorded" in got["note"]


def test_nulls_with_no_stated_reason_are_not_dressed_up_as_narration(page):
    """The inverse bug. If a bare null reads as "the guest narrated this", a
    genuinely broken timestamp field looks like a working RCA."""
    got = _timeline(page, SILENT)
    narrated = _timeline(page, NARRATED)["note"]
    assert got["note"] != narrated, \
        "a bare null and a guest-narrated entry got the same sentence"
    assert "gave no reason" in got["note"], got["note"]
    assert "confidence trail" in got["note"]
    # It may mention narration only to rule it out, never to claim it.
    assert "the guest's own account" not in got["note"], got["note"]


def test_a_fully_dated_timeline_gets_no_note(page):
    """Every timeline carrying a footnote is the same as none of them doing."""
    got = _timeline(page, [{"time": "22 Jul 15:41", "what": "Booking created",
                            "detail": None}])
    assert got["note"] is None, got["note"]
    assert got["times"] == ["22 Jul 15:41"]


def test_the_note_is_not_styled_as_data(page):
    got = _timeline(page, NARRATED)
    row = page.evaluate("""() => {
      const t = document.querySelector('#rca-booking-logs-section .tl-what');
      return t ? getComputedStyle(t).color : null; }""")
    assert got["colour"] != row, "the footnote reads as another timeline row"


# ── a blank reply is a decision, and has to read as one ─────────────────────
#
# Prompt rule 20: with no approved macro for the issue, the model returns null
# rather than writing a reply, because an invented one is indistinguishable
# from an approved one on this card — same register, same length, same shape —
# and Send puts it on a public review. That only works if the empty box says
# so. An unexplained blank reads as a step that failed.

def _reply_state(page, text):
    return page.evaluate("""(t) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.rca.reply;
      r.rca.reply = t; renderRcaCol();
      const ta = document.querySelector('.reply-text');
      const meta = document.querySelector('.reply-meta');
      const out = {placeholder: ta ? ta.placeholder : null,
                   value: ta ? ta.value : null,
                   meta: meta ? meta.innerText : ''};
      r.rca.reply = keep; renderRcaCol();
      return out; }""", text)


def test_a_blank_reply_says_to_write_it_yourself(page):
    got = _reply_state(page, "")
    assert "curate the response yourself" in got["placeholder"], got["placeholder"]
    assert "AI-drafted" not in got["placeholder"], \
        "an empty box still invites the reader to expect a draft that is not coming"


def test_a_blank_reply_says_why_it_is_blank(page):
    got = _reply_state(page, "")
    assert "no approved macro" in got["meta"].lower(), got["meta"]
    assert "Write it yourself" in got["meta"], got["meta"]
    assert "confidence trail" in got["meta"], \
        "it does not point at where the reason is recorded"


def test_a_drafted_reply_is_not_told_to_write_itself(page):
    """The inverse. If every reply carries the instruction it stops meaning
    anything, and the deliberate blanks are lost among them."""
    got = _reply_state(page, "Hi Lewis, I'm sorry about the meeting point.")
    assert "curate the response yourself" not in got["placeholder"]
    assert "Write it yourself" not in got["meta"]
    assert "Editable" in got["meta"]


def test_whitespace_is_not_a_reply(page):
    """A reply of spaces renders an empty box either way; only the placeholder
    can tell the reader which kind of empty it is."""
    got = _reply_state(page, "   \n  ")
    assert "curate the response yourself" in got["placeholder"], got["placeholder"]


# ── the blank reply is a decision, and has to read as one ───────────────────
#
# Prompt rule 20: with no approved macro, the model returns null rather than
# writing a reply, because an invented one is indistinguishable from an
# approved one on this card and Send puts it on a public review. That only
# works if the empty box SAYS so — otherwise it reads as a step that failed,
# which is the bug this whole branch is about.

def _reply_box(page, text):
    return page.evaluate("""(t) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.rca.reply;
      r.rca.reply = t; renderRcaCol();
      const ta = document.querySelector('.reply-text');
      const meta = document.querySelector('.reply-meta');
      const out = {value: ta ? ta.value : null,
                   placeholder: ta ? ta.placeholder : null,
                   meta: meta ? meta.innerText : null};
      r.rca.reply = keep; renderRcaCol();
      return out; }""", text)


def test_a_blank_reply_says_to_write_it_yourself(page):
    got = _reply_box(page, "")
    assert got["value"] == ""
    assert "curate the response yourself" in got["placeholder"], got["placeholder"]


def test_a_blank_reply_says_why_it_is_blank(page):
    """"Nothing was drafted" is a fact about the box. "No approved macro covers
    this issue" is the reason, and it is the difference between a working
    pipeline and a broken one."""
    got = _reply_box(page, "")
    assert "no approved macro" in got["meta"].lower(), got["meta"]
    assert "Write it yourself" in got["meta"]


def test_a_blank_reply_points_at_the_trail(page):
    got = _reply_box(page, "")
    assert "confidence trail" in got["meta"].lower(), got["meta"]


def test_whitespace_is_not_treated_as_a_drafted_reply(page):
    """A model that returns " " instead of null must not turn the deliberate
    blank into an editable box with nothing in it."""
    got = _reply_box(page, "   \n ")
    assert "curate the response yourself" in got["placeholder"]


def test_a_drafted_reply_keeps_the_normal_editing_prompt(page):
    """The inverse. If every reply carries the "write it yourself" line, the
    line stops meaning anything."""
    got = _reply_box(page, "Hey Lewis, I'm sorry about the meeting point.")
    assert "curate the response yourself" not in (got["placeholder"] or "")
    assert "Editable" in got["meta"]
    assert "no approved macro" not in got["meta"].lower()


# ── the census: no control ships without a test that clicks it ─────────────
#
# "Make sure all buttons throughout the dashboard work" is not a thing one
# assertion can prove. What CAN be proved is that no control reaches the card
# unaccounted for: every `data-*` control rendered anywhere is either driven by
# a test in this suite or named here as deliberately not driven, with a reason.
#
# This is a coverage guard, not a claim that each control works — it says
# "nothing new appeared unnoticed". A control added tomorrow with no test fails
# this immediately, by name, which is the only cheap defence against the
# failure this whole file exists for: a dead control and a live one look
# identical.

# Driven by a test somewhere in this suite. The comment names where.
DRIVEN = {
    "data-log-add",            # test_rca_ui_rendered::test_add_event_still_works…
    "data-log-del",            # same
    "data-log-field",          # …::test_editing_a_booking_log_row_persists
    "data-log-idx",            # same (the row index that handler reads)
    "data-tl-toggle",          # …::test_the_machinery_toggle_works…
    "data-raw-err",            # …::test_the_raw_error_is_behind_a_toggle_and_capped
    "data-slack-customize",    # …::test_customize_reveals_the_chips…
    "data-slack-section",      # …::test_the_collapsed_line_still_states…
    "data-slack-edit",         # …::test_the_post_has_one_block_per_guest_issue
    "data-aoi-idx",            # …::test_editing_a_pointer_keeps_the_finding…
    "data-flag-idx",           # test_rca_edits_and_slack_post
    "data-flag-del",           # same
    "data-takedown-rec",       # test_recent_changes_rendered::…colour_roles
    "data-takedown-reason",    # test_takedown_reason
    "data-v3p",                # this file, the add-row tests
    "data-v3p-mark",           # test_recent_changes_rendered::…marked…
    "data-dss-edit",           # …::test_the_dss_block_exists_and_toggles_into_edit
    "data-res-type",           # …::test_every_control_shares_one_border…
    "data-res-amount",         # test_resolution_card
    "data-close-out",          # …::test_close_out_arms_before_it_fires…
    "data-close-reason",       # same (an attribute that handler reads)
    "data-scenario-add",       # test_recent_changes_rendered::…delete…
    "data-scenario-remove",    # …::test_the_primary_delete_is_live_too
    "data-overlay-add",        # …::test_the_scenario_delete_removes_it…
    "data-overlay-remove",     # same
    "data-subtheme-add",       # test_classification_selects
    "data-subtheme-remove",    # same
    "data-classify",           # same
    "data-rerun-match",        # test_confirm_candidate_refreshes
    "data-wwr-issue-add",      # this file, test_every_add_button_produces_a_row
    "data-aline-add",          # same
    "data-sp-rec-add",         # …::test_a_record_added_to_a_draft…
    "data-add-action",         # test_rca_ui_rendered::…renders_under_its_team
    "data-scenario-revert",    # test_scenario_override_api
    "data-tab",                # …::test_an_empty_tab_says_which_kind_of_empty…
    "data-window",             # test_insights_window_picker
    "data-resize",             # test_column_resize
    "data-mark-sent",          # this file, test_mark_sent_moves_the_review…
    # The guest response. `data-outgoing-reply` is the box that goes to the
    # guest and the one Copy and Send read; the English panel beside it is a
    # projection. All driven in this file.
    "data-outgoing-reply",     # …::test_the_outgoing_reply_box_is_the_one_copy_reads
    "data-english-reply",      # …::test_the_apply_control_says_the_edit_was_not_applied…
    "data-apply-english",      # …::test_the_apply_control_says_the_edit_was_not_applied…
    "data-english-panel",      # …::test_an_english_review_draws_one_box_and_no_translation
    "data-english-stale",      # …::test_a_stale_english_copy_says_so_on_screen
    "data-english-status",     # …::test_the_boxes_say_they_disagree_while_a_translation…
}

# Not controls: status targets, stamps and identifiers the handlers read.
NOT_CONTROLS = {
    "data-scenario-regen-status", "data-build", "data-stale", "data-id",
    "data-ph", "data-flag-field", "data-chk-section",
    # A provenance marker span, not a control: it carries the source in its
    # title so a reader who doubts a point can check it.
    "data-aoi-src",
    # The collapse key stamped on each section. The CONTROL is the section
    # label, handled by one delegated listener so it works in every column —
    # a per-render binding only ever reached the root it was given, which is
    # how the booking timeline's chevron came to be drawn and dead. This
    # attribute is the state key that listener reads, not a thing to click.
    # Driven end to end by tests/test_sections_collapse.py.
    "data-sec-key",
    # An empty container that showPostError fills when Slack refuses the post.
    # Nothing to click, and empty is its normal state — an always-present box
    # would read as a warning that never clears. What it renders is the
    # server's {message, slack_error, verdict}, and WHICH refusal produces
    # which sentence is driven in tests/test_slack_post_refused.py; the
    # guarantee that a refused post leaves the review where it is has its own
    # test there too.
    "data-slack-post-err",
}

# Deliberately not driven, each with the reason.
UNDRIVEN_BY_DESIGN = {
    "data-print",      # opens a native dialog the harness cannot dismiss
    "data-open-bms",   # navigates away, ending the session for every later test
}

# A KNOWN, NAMED GAP. These render and no test clicks them. Listed rather than
# folded into DRIVEN, because a census that quietly counts an undriven control
# as covered is worth less than no census: it would report full coverage of a
# card with dead buttons on it, which is the exact failure this file exists
# for. Anything moved out of here must be moved because a test now drives it.
NOT_YET_DRIVEN = {
    "data-aoi-add", "data-chk-group-toggle", "data-claim-del",
    "data-contact-add", "data-ev-del", "data-flag-add", "data-ix-toggle",
    "data-rca-only-regen", "data-refresh-slack", "data-reply-copy",
    "data-reply-edit", "data-rerun-all", "data-slack-post", "data-slack-regen",
    "data-slack-sec-all", "data-slack-sec-none", "data-trail-toggle",
    "data-v3sel", "data-wwr-all", "data-wwr-ev-add", "data-wwr-toggle",
    "data-aoi-del",
    # The guest's-language input, which gates Apply in the unknown-language
    # case. A real control and a NAMED GAP: it only renders for a review whose
    # text was translated inbound with no language code recorded, and the
    # harness fixture has no such review. Listed rather than folded into
    # DRIVEN, because a census that counts an undriven control as covered is
    # worth less than no census.
    "data-reply-lang-name",
}


def test_no_control_is_unaccounted_for(page):
    """Enumerate every data-* control on the card and account for all of them.

    Not a claim that each works — the tests named above are that. This is the
    guard that stops a NEW control arriving with no test at all, which is
    exactly how the three dead ones in this file's docstring shipped.
    """
    found = page.evaluate("""() => {
      const out = new Set();
      document.querySelectorAll('*').forEach(el => {
        for (const a of el.attributes)
          if (a.name.startsWith('data-') && a.name !== 'data-ph') out.add(a.name);
      });
      return [...out].sort(); }""")
    assert len(found) > 15, (
        f"only {len(found)} data-* attributes found on the whole card — the "
        f"enumeration is looking at the wrong document, and this test would "
        f"pass by checking nothing")
    accounted = DRIVEN | UNDRIVEN_BY_DESIGN | NOT_CONTROLS | NOT_YET_DRIVEN
    unknown = sorted(set(found) - accounted)
    assert not unknown, (
        f"{len(unknown)} control(s) render and are accounted for NOWHERE: "
        f"{unknown}. A dead control and a live one look identical — add a test "
        f"that clicks it, or name it in NOT_YET_DRIVEN / UNDRIVEN_BY_DESIGN "
        f"with the reason.")


def test_the_driven_list_has_not_gone_stale(page):
    """The other direction. A name left here after its control stopped
    rendering makes the census look more complete than it is."""
    found = set(page.evaluate("""() => {
      const out = new Set();
      document.querySelectorAll('*').forEach(el => {
        for (const a of el.attributes)
          if (a.name.startsWith('data-')) out.add(a.name);
      });
      return [...out]; }"""))
    # Only checked for controls that the seeded card is expected to show. The
    # rest render in buckets this fixture is not in (untraceable, processing),
    # so their absence here is not staleness.
    # data-tl-toggle is NOT here: it renders only when the timeline has
    # internal events to hide, which this fixture's does not.
    always_on = {"data-log-add", "data-slack-customize",
                 "data-v3p", "data-dss-edit", "data-scenario-add",
                 "data-takedown-rec"}
    missing = sorted(always_on - found)
    assert not missing, (
        f"{missing} are listed as driven but no longer render on a normal "
        f"card — either the control was removed and this list did not follow, "
        f"or it stopped rendering by accident")


def test_every_rendered_button_has_a_cursor_that_says_it_is_live(page):
    """A cheap, real signal. A disabled or decorative element must not present
    itself as clickable, and a live one must."""
    bad = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('#rca-col button, #review-col button').forEach(b => {
        const cs = getComputedStyle(b);
        if (b.disabled) { if (cs.cursor === 'pointer') out.push(b.textContent.trim() + ' [disabled but pointer]'); }
        else if (cs.cursor !== 'pointer') out.push(b.textContent.trim() + ' [live but ' + cs.cursor + ']');
      });
      return out; }""")
    assert not bad, f"controls whose cursor contradicts their state: {bad}"


def test_the_known_gap_is_reported_not_hidden(page):
    """The census must SAY how much of the card it does not cover.

    A coverage guard that reports only "nothing unknown" reads identically
    whether it covers every control or three of them. This is the count, in
    words, so the gap cannot quietly grow.
    """
    found = set(page.evaluate("""() => {
      const out = new Set();
      document.querySelectorAll('*').forEach(el => {
        for (const a of el.attributes)
          if (a.name.startsWith('data-')) out.add(a.name);
      });
      return [...out]; }"""))
    rendered_gap = sorted(found & NOT_YET_DRIVEN)
    driven_here = sorted(found & DRIVEN)
    assert driven_here, "no driven control rendered — the census found nothing"
    # Not an assertion that the gap is empty; an assertion that it is KNOWN.
    assert set(rendered_gap) <= NOT_YET_DRIVEN
    print(f"\ncontrol census: {len(driven_here)} driven, "
          f"{len(rendered_gap)} rendered but not yet driven: {rendered_gap}")


def test_every_toggle_control_actually_changes_something(page):
    """Drive the toggles generically: click, and require the DOM to move.

    A toggle is the cheapest control to check and the commonest to find dead —
    two of the three in this file's docstring were toggles. Clicking every one
    and requiring the card's markup to differ afterwards catches an unbound
    handler without needing to know what each toggle means.
    """
    result = page.evaluate("""async () => {
      const sels = ['[data-tl-toggle]', '[data-trail-toggle]', '[data-wwr-toggle]',
                    '[data-ix-toggle]', '[data-chk-group-toggle]',
                    '[data-slack-customize]', '[data-dss-edit]'];
      const out = [];
      // Watch BOTH columns. A control in the review column re-renders that
      // column, and comparing only the RCA column reported it dead — which is
      // the same false alarm, one level up, as a lookup that cannot say it
      // ran and found nothing.
      const snap = () => (document.querySelector('#rca-col') || {}).innerHTML
                       + '||' + ((document.querySelector('#review-col') || {}).innerHTML || '');
      for (const s of sels) {
        const el = document.querySelector(s);
        if (!el) { out.push([s, 'absent']); continue; }
        const before = snap();
        el.click();
        await new Promise(r => setTimeout(r, 250));
        out.push([s, before === snap() ? 'DEAD' : 'live']);
        const again = document.querySelector(s);
        if (again) { again.click(); await new Promise(r => setTimeout(r, 250)); }
      }
      return out; }""")
    dead = [s for s, v in result if v == "DEAD"]
    present = [s for s, v in result if v != "absent"]
    assert present, "none of the toggles rendered — NOT BUILT, not passing"
    assert not dead, (
        f"{len(dead)} toggle(s) rendered and changed nothing when clicked: "
        f"{dead}")


# ── Mark sent, beside Post to thread ───────────────────────────────────────

def test_mark_sent_is_disabled_until_the_rca_is_in_the_thread(page):
    """Enabled earlier it would call /send with nothing posted — and /send
    posts the RCA when rca_posted_at is unset, which is the second-copy
    problem the button beside it already guards.

    A disabled control must be visibly different from a broken one, so it
    carries a title saying what to do first.
    """
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.rcaPostedAt;
      r.rcaPostedAt = null; renderRcaCol();
      const off = document.querySelector('[data-mark-sent]');
      const a = off ? {disabled: off.disabled, title: off.title,
                       cursor: getComputedStyle(off).cursor} : null;
      r.rcaPostedAt = '2026-08-05T10:00:00'; renderRcaCol();
      const on = document.querySelector('[data-mark-sent]');
      const b = on ? {disabled: on.disabled, title: on.title,
                      cursor: getComputedStyle(on).cursor} : null;
      r.rcaPostedAt = keep; renderRcaCol();
      return {a, b}; }""")
    assert got["a"], "the Mark sent button does not render at all — NOT BUILT"
    assert got["a"]["disabled"] is True, (
        "Mark sent is live before the RCA has been posted — pressing it would "
        "post the RCA rather than only marking the review finished")
    assert "Post the RCA" in got["a"]["title"], got["a"]
    assert got["a"]["cursor"] != "pointer", (
        "a disabled control still presents itself as clickable")
    assert got["b"]["disabled"] is False, (
        "Mark sent stays disabled after the RCA was posted, so the review "
        "still cannot be finished from where the work happens")
    assert "nothing is posted again" in got["b"]["title"], got["b"]


def test_mark_sent_calls_send_once_and_posts_nothing(page):
    """Driven. It must reach /send — one endpoint, not a second path — and the
    response must report that nothing was posted."""
    got = page.evaluate("""async () => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.rcaPostedAt;
      r.rcaPostedAt = '2026-08-05T10:00:00'; renderRcaCol();
      if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
      const calls = [];
      window.fetch = async (url, opts) => {
        const u = String(url);
        if (u.includes('/send')) {
          calls.push({url: u, method: (opts || {}).method});
          return new Response(JSON.stringify(
            {ok: true, ts: null, posted: false, why: 'already posted to the thread',
             sent_route: 'rca_posted'}), {status: 200});
        }
        if (u.includes('/post-rca')) { calls.push({url: u, method: 'POST'}); }
        return window.__realFetch(url, opts);
      };
      document.querySelector('[data-mark-sent]').click();
      await new Promise(x => setTimeout(x, 1200));
      const label = (document.querySelector('[data-mark-sent]') || {}).textContent || '';
      const route = r.sentRoute;
      window.fetch = window.__realFetch;
      r.rcaPostedAt = keep; r.status = 'draft'; r.sentRoute = '';
      renderRcaCol();
      return {calls, label, route, status: r.status}; }""")
    sends = [c for c in got["calls"] if "/send" in c["url"]]
    posts = [c for c in got["calls"] if "post-rca" in c["url"]]
    assert len(sends) == 1, f"Mark sent did not call /send exactly once: {got['calls']}"
    assert sends[0]["method"] == "POST", sends[0]
    assert not posts, (
        f"Mark sent also hit /post-rca — that is the second copy in the "
        f"thread this control exists to avoid: {posts}")
    assert got["route"] == "rca_posted", (
        f"the route the server derived did not reach the card: {got['route']!r}")


def test_mark_sent_survives_a_rerender(page):
    """The handler is delegated. The Slack block re-renders on every edit to
    the post, and a handler bound after one render is dead after the next —
    which is the failure this whole file exists for."""
    got = page.evaluate("""async () => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.rcaPostedAt;
      r.rcaPostedAt = '2026-08-05T10:00:00';
      renderRcaCol(); renderRcaCol(); renderRcaCol();   // three redraws
      if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
      let hit = 0;
      window.fetch = async (url, opts) => {
        if (String(url).includes('/send')) {
          hit++;
          return new Response(JSON.stringify(
            {ok: true, posted: false, sent_route: 'rca_posted'}), {status: 200});
        }
        return window.__realFetch(url, opts);
      };
      document.querySelector('[data-mark-sent]').click();
      await new Promise(x => setTimeout(x, 1000));
      window.fetch = window.__realFetch;
      r.rcaPostedAt = keep; r.status = 'draft'; r.sentRoute = '';
      renderRcaCol();
      return hit; }""")
    assert got == 1, (
        f"after three re-renders the button fired {got} times — a per-render "
        f"binding is either dead or stacked")


def test_mark_sent_shares_the_post_buttons_box(page):
    """It sits beside Post to thread, so the pair must read as one row rather
    than as a primary button with something bolted next to it."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.rcaPostedAt;
      r.rcaPostedAt = '2026-08-05T10:00:00'; renderRcaCol();
      const box = el => { const c = getComputedStyle(el), b = el.getBoundingClientRect();
        return {h: Math.round(b.height), r: c.borderTopLeftRadius,
                p: c.paddingTop + '/' + c.paddingLeft, f: c.fontSize}; };
      const a = document.querySelector('[data-slack-post]');
      const b = document.querySelector('[data-mark-sent]');
      const out = (a && b) ? {post: box(a), sent: box(b),
                              sameRow: Math.abs(a.getBoundingClientRect().top
                                              - b.getBoundingClientRect().top) < 3} : null;
      r.rcaPostedAt = keep; renderRcaCol();
      return out; }""")
    assert got, "one of the two buttons did not render — NOT BUILT"
    assert got["sameRow"], "the two buttons are not on the same line"
    assert got["post"]["h"] == got["sent"]["h"], (
        f"different heights: {got['post']['h']} vs {got['sent']['h']}")
    assert got["post"]["r"] == got["sent"]["r"], "different corner radii"
    assert got["post"]["p"] == got["sent"]["p"], "different padding"
    assert got["post"]["f"] == got["sent"]["f"], "different font sizes"


# ── the stated-issue box ────────────────────────────────────────────────────

def test_the_stated_issue_box_saves_what_is_typed_into_it(page):
    """It was the one editable in the RCA column with no data-v3p.

    The generic path saver binds on `[data-v3p]`, so this box rendered
    editable, accepted the text, flashed nothing and wrote nothing — the same
    invisible failure as an unbound "+ Add" or a decorative chevron. Driven
    with a stubbed PATCH so the assertion is on what the page SENT, not on
    what a server happened to accept.
    """
    got = page.evaluate("""async () => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const el = document.querySelector('.stated-issue-text');
      if (!el) return {err: 'the stated-issue box does not render'};
      const keep = JSON.parse(JSON.stringify(r.rca.v3.stated_issue ?? null));
      let body = null;
      window.__realFetch = window.__realFetch || window.fetch;
      window.fetch = async (url, opts) => {
        if (String(url).includes('/draft-v2')) {
          body = JSON.parse(opts.body);
          return new Response(JSON.stringify({ok: true, draft: {}}), {status: 200});
        }
        return window.__realFetch(url, opts);
      };
      el.focus();
      el.textContent = 'TYPED BY A PERSON';
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('blur', {bubbles: true}));
      el.blur();
      await new Promise(x => setTimeout(x, 600));
      window.fetch = window.__realFetch;
      const sent = body && body.rca_v3 ? body.rca_v3.stated_issue : undefined;
      r.rca.v3.stated_issue = keep; renderRcaCol();
      return {sent, patched: !!body}; }""")
    assert not got.get("err"), got["err"]
    assert got["patched"], "typing in the stated-issue box sent nothing to the server"
    assert got["sent"] == "TYPED BY A PERSON", \
        f"the box saved {got['sent']!r} — it is not wired to rca_v3.stated_issue"


def test_the_stated_issue_survives_the_next_render(page):
    """A save the next render undoes is a save nobody keeps.

    The box writes rca.v3.stated_issue; the section used to render
    r.statedIssue, a snapshot taken when the review was loaded. So the typed
    sentence went to the server and was replaced on screen by the old one at
    the very next renderRcaCol() — saved and reverted, which looks exactly
    like never having saved.
    """
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.rca.v3.stated_issue ?? null;
      r.rca.v3.stated_issue = 'EDITED IN THE BLOB';
      renderRcaCol();
      const shown = (document.querySelector('.stated-issue-text') || {}).textContent;
      r.rca.v3.stated_issue = keep; renderRcaCol();
      return shown; }""")
    assert got == "EDITED IN THE BLOB", \
        (f"the section rendered {got!r} — it reads a different store from the "
         f"one the edit box writes")


def test_an_emptied_stated_issue_stays_empty(page):
    """Presence, not truthiness. Clearing the box must not let the load-time
    value reappear — that is the delete-undoes-itself bug one field over."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.rca.v3.stated_issue ?? null;
      const keepR = r.statedIssue;
      r.statedIssue = 'THE VALUE FROM THE COLUMN';
      r.rca.v3.stated_issue = '';
      renderRcaCol();
      const shown = (document.querySelector('.stated-issue-text') || {}).textContent;
      const empty = !!document.querySelector('.stated-issue .rca-empty');
      r.rca.v3.stated_issue = keep; r.statedIssue = keepR; renderRcaCol();
      return {shown, empty}; }""")
    assert got["shown"] == "", \
        f"a cleared stated issue rendered {got['shown']!r} from the other store"
    assert got["empty"], "a cleared stated issue shows no empty state at all"


# ── The guest response: which box goes out, and in which language ───────────

def test_the_outgoing_reply_box_is_the_one_copy_reads(page):
    """`data-outgoing-reply` is the reply that goes to the guest. Copy must
    read THAT box and not the English working copy, which carries the same
    CSS class — a positional `.reply-text` lookup is one DOM reorder away from
    copying the text that must never be sent."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keepLang = r.lang, keepRl = r.rca.responseLanguage;
      const keepEv = r.rca.englishView, keepReply = r.rca.reply;
      r.lang = 'IT';
      r.rca.responseLanguage = {state: 'translated', language: 'IT', why: ''};
      r.rca.reply = 'TESTO ITALIANO';
      r.rca.englishView = {state: 'current', text: 'ENGLISH COPY', why: ''};
      renderRcaCol();
      const out = document.querySelector('[data-outgoing-reply]');
      const eng = document.querySelector('[data-english-reply]');
      const res = {out: out ? out.value : null, eng: eng ? eng.value : null,
                   sameClass: !!(out && eng && out.className === eng.className)};
      r.lang = keepLang; r.rca.responseLanguage = keepRl;
      r.rca.englishView = keepEv; r.rca.reply = keepReply; renderRcaCol();
      return res; }""")
    assert got["out"] == "TESTO ITALIANO", (
        "the outgoing box does not hold the guest-language reply — it rendered "
        f"{got['out']!r}")
    assert got["eng"] == "ENGLISH COPY", "the English working copy did not render"
    assert got["sameClass"], (
        "the two boxes no longer share a class, so this test is no longer "
        "checking that they are told apart by NAME rather than by position")


def test_an_english_review_draws_one_box_and_no_translation(page):
    """An English review must not imply a translation happened — no English
    panel, no apply control, nothing hinting the visible reply is a copy of
    some other 'real' one."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keepLang = r.lang, keepRl = r.rca.responseLanguage;
      const keepEv = r.rca.englishView;
      r.lang = 'EN';
      r.rca.responseLanguage = {state: 'english', language: 'EN', why: ''};
      r.rca.englishView = {state: 'same', text: r.rca.reply, why: ''};
      renderRcaCol();
      const res = {
        out:   !!document.querySelector('[data-outgoing-reply]'),
        eng:   !!document.querySelector('[data-english-reply]'),
        apply: !!document.querySelector('[data-apply-english]'),
        panel: !!document.querySelector('[data-english-panel]'),
      };
      r.lang = keepLang; r.rca.responseLanguage = keepRl;
      r.rca.englishView = keepEv; renderRcaCol();
      return res; }""")
    assert got["out"], "an English review renders no response box at all"
    assert not got["eng"], "an English review drew a second, English box"
    assert not got["apply"], "an English review offers a translate control"
    assert not got["panel"], "an English review drew the translation panel"


def test_a_stale_english_copy_says_so_on_screen(page):
    """The outgoing reply was edited directly, so the English beside it is
    from before that edit. Showing it without saying so presents a superseded
    translation as the reply about to go out."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keepLang = r.lang, keepRl = r.rca.responseLanguage;
      const keepEv = r.rca.englishView;
      r.lang = 'IT';
      r.rca.responseLanguage = {state: 'translated', language: 'IT', why: ''};
      r.rca.englishView = {state: 'stale', text: 'OLD ENGLISH',
                           why: 'the outgoing response was edited directly'};
      renderRcaCol();
      const el = document.querySelector('[data-english-stale]');
      const res = {shown: !!el, text: el ? el.textContent : ''};
      r.lang = keepLang; r.rca.responseLanguage = keepRl;
      r.rca.englishView = keepEv; renderRcaCol();
      return res; }""")
    assert got["shown"], (
        "a stale English copy renders with nothing saying it is behind the "
        "outgoing reply")
    assert "BEHIND" in got["text"] or "behind" in got["text"], got["text"]


def test_the_apply_control_says_the_edit_was_not_applied_when_it_fails(page):
    """THE contract. A failed translation must leave the outgoing reply
    untouched AND say the English edit was not applied — an edit that appears
    to save and did not is what this codebase punishes hardest."""
    got = page.evaluate("""async () => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keepLang = r.lang, keepRl = r.rca.responseLanguage;
      const keepEv = r.rca.englishView, keepReply = r.rca.reply;
      r.lang = 'IT';
      r.rca.responseLanguage = {state: 'translated', language: 'IT', why: ''};
      r.rca.reply = 'TESTO ORIGINALE';
      r.rca.englishView = {state: 'current', text: 'original english', why: ''};
      renderRcaCol();
      if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
      window.fetch = async (u, o) => {
        if (String(u).indexOf('apply-english-reply') !== -1)
          return new Response(JSON.stringify({detail: 'translation returned nothing'}),
                              {status: 502, headers: {'Content-Type': 'application/json'}});
        return window.__realFetch(u, o);
      };
      const box = document.querySelector('[data-english-reply]');
      box.value = 'a completely new english edit';
      document.querySelector('[data-apply-english]').click();
      await new Promise(x => setTimeout(x, 800));
      const status = (document.querySelector('[data-english-status]') || {}).textContent || '';
      const outNow = (document.querySelector('[data-outgoing-reply]') || {}).value;
      window.fetch = window.__realFetch;
      r.lang = keepLang; r.rca.responseLanguage = keepRl;
      r.rca.englishView = keepEv; r.rca.reply = keepReply; renderRcaCol();
      return {status, outNow}; }""")
    assert "NOT APPLIED" in got["status"], (
        f"a failed translation reported {got['status']!r} — it does not say the "
        f"English edit was not applied")
    assert "translation returned nothing" in got["status"], (
        f"the failure does not say WHY: {got['status']!r}")
    assert got["outNow"] == "TESTO ORIGINALE", (
        f"the outgoing reply changed to {got['outNow']!r} after a FAILED "
        f"translation — the one thing that must never happen")


def test_the_boxes_say_they_disagree_while_a_translation_is_in_flight(page):
    """While the call is out, the English shows an edit the outgoing reply
    does not carry. That disagreement is stated rather than left to look
    settled."""
    got = page.evaluate("""async () => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keepLang = r.lang, keepRl = r.rca.responseLanguage;
      const keepEv = r.rca.englishView;
      r.lang = 'IT';
      r.rca.responseLanguage = {state: 'translated', language: 'IT', why: ''};
      r.rca.englishView = {state: 'current', text: 'original english', why: ''};
      renderRcaCol();
      if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
      window.fetch = async (u, o) => {
        if (String(u).indexOf('apply-english-reply') !== -1) {
          await new Promise(x => setTimeout(x, 1500));   // still in flight
          return new Response('{}', {status: 500});
        }
        return window.__realFetch(u, o);
      };
      document.querySelector('[data-english-reply]').value = 'new english';
      document.querySelector('[data-apply-english]').click();
      await new Promise(x => setTimeout(x, 300));        // mid-flight
      const status = (document.querySelector('[data-english-status]') || {}).textContent || '';
      window.fetch = window.__realFetch;
      await new Promise(x => setTimeout(x, 1500));
      r.lang = keepLang; r.rca.responseLanguage = keepRl;
      r.rca.englishView = keepEv; renderRcaCol();
      return status; }""")
    assert "IN FLIGHT" in got or "disagree" in got, (
        f"mid-translation the card said {got!r} — the two boxes disagree and "
        f"nothing on screen admits it")
