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


SECTIONS = ["#rca-events-timeline-section", "#rca-flags-section",
            "#rca-wwr5-section", "#rca-tldr-section", "#rca-sop-section",
            "#rca-issue-answers-section"]


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
