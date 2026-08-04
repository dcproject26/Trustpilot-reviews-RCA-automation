"""The per-contact story renders, is editable, and survives a save.

Rule 10b asks the model for what the guest came with, what we said, how long
they waited for a human, what they said back and how it ended. Three of those
five exist nowhere else in the pipeline — no Zendesk frame carries them — so
if the card does not draw them the model's answer has no reader at all.

This is the failure this repo keeps hitting from the other end: the validator
was dropping all seven fields, so the section rendered exactly as it would
have if the model had answered nothing. Widening the projection is only half
the fix; a field that survives validation and is never drawn is the same bug
one layer up.

Driven in a browser rather than asserted against the source, because the
markup is built by one function and the save handler bound by another, and
this file has already recorded one control that rendered perfectly and was
wired to nothing.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401

NARR = {
    "guest_said": "Wants to cancel, unwell",
    "we_said": "Skylar sent the policy link",
    "wait_for_human": "18 minutes",
    "guest_replied": "Asked for a human",
    "outcome": "Escalated to CE",
}


def _inject(page, over=None, which=0):
    """Put narrative fields on one note and re-render. Returns the section."""
    note = dict(NARR)
    note.update(over or {})
    page.evaluate("""([note, which]) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      window.__narrKeep = JSON.parse(JSON.stringify(r.rca.supportNotes));
      Object.assign(r.rca.supportNotes[which], note);
      r.rca.v3.support_interaction_notes = r.rca.supportNotes;
      renderRcaCol();
    }""", [note, which])
    page.wait_for_timeout(300)


def _restore(page):
    page.evaluate("""() => {
      if (!window.__narrKeep) return;
      const r = REVIEWS.find(x => x.id === state.selected);
      r.rca.supportNotes = window.__narrKeep;
      r.rca.v3.support_interaction_notes = window.__narrKeep;
      renderRcaCol();
    }""")
    page.wait_for_timeout(200)


def _open_all(page):
    for h in page.locator(".convo-frame-head").all():
        if "open" not in (h.evaluate("e => e.parentElement.className") or ""):
            h.click()
    page.wait_for_timeout(250)


def _section_html(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('.interactions')]"
        ".map(e => e.innerHTML).join('')")


# ── it draws ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field,label", [
    ("guest_said",     "Guest said"),
    ("we_said",        "We said"),
    ("wait_for_human", "Wait for human"),
    ("guest_replied",  "Guest replied"),
    ("outcome",        "Outcome"),
])
def test_each_narrative_field_is_drawn(page, field, label):
    try:
        _inject(page)
        _open_all(page)
        html = _section_html(page)
        assert label in html, f"{field} has no label on the card"
        assert NARR[field] in html, (
            f"{field} survived validation and is not drawn — the model's "
            f"answer has no reader")
    finally:
        _restore(page)


def test_the_three_fields_with_no_other_source_are_drawn(page):
    """wait_for_human, guest_replied and outcome. A frame carries none of
    them, so undrawn means unreadable."""
    try:
        _inject(page)
        _open_all(page)
        html = _section_html(page)
        for f in ("wait_for_human", "guest_replied", "outcome"):
            assert NARR[f] in html, f
    finally:
        _restore(page)


def test_a_null_field_draws_nothing_at_all(page):
    """The rule tells the model to leave an undetectable field null and write
    nothing. A row of empty labels would report five blanks as five things we
    looked for and failed to find."""
    try:
        _inject(page, {"wait_for_human": None, "guest_replied": None})
        _open_all(page)
        html = _section_html(page)
        assert "Wait for human" not in html, \
            "an empty label was drawn for a field the model left blank"
        assert "Guest replied" not in html
        assert "Outcome" in html, "the fields that DID come back stopped drawing"
    finally:
        _restore(page)


def test_a_contact_with_no_narrative_draws_no_empty_block(page):
    try:
        _inject(page, {k: None for k in NARR})
        _open_all(page)
        assert "convo-narr" not in _section_html(page)
    finally:
        _restore(page)


# ── it is editable, and the edit goes to the right note ────────────────────

def test_the_narrative_fields_are_editable(page):
    try:
        _inject(page)
        _open_all(page)
        for f in NARR:
            n = page.locator(f'[data-v3p$=".{f}"]').count()
            assert n >= 1, f"{f} is drawn as dead text — it cannot be corrected"
    finally:
        _restore(page)


def test_the_edit_path_indexes_the_real_notes_list(page):
    """The orphan branch once used its position in a FILTERED array, so an
    edit to one contact rewrote a different one. Every path must carry the
    index into rca_v3.support_interaction_notes."""
    try:
        _inject(page)
        _open_all(page)
        paths = page.evaluate(
            """() => [...document.querySelectorAll('[data-v3p]')]
                 .map(e => e.dataset.v3p)
                 .filter(p => p.startsWith('support_interaction_notes.'))""")
        assert paths, "no editable contact fields at all"
        n = page.evaluate(
            "() => REVIEWS.find(x => x.id === state.selected).rca.supportNotes.length")
        for p in paths:
            idx = int(p.split(".")[1])
            assert 0 <= idx < n, f"{p} points outside the notes list of {n}"


    finally:
        _restore(page)


def test_editing_a_narrative_field_reaches_the_store(page):
    """The whole chain: type, blur, PATCH, reload, still there. A field that
    renders and does not save is the TL;DR bug again."""
    try:
        _inject(page)
        _open_all(page)
        el = page.locator('[data-v3p$=".outcome"]').first
        el.click()
        page.keyboard.press("ControlOrMeta+a")
        el.type("Refunded in full after escalation")
        page.locator(".rca-col").first.click()
        page.wait_for_timeout(1200)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(900)
        page.locator(".review-item").first.click()
        page.wait_for_timeout(1500)
        _open_all(page)
        assert "Refunded in full after escalation" in _section_html(page), \
            "the edit was accepted, ticked green, and is not there on reload"
    finally:
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(900)
        page.locator(".review-item").first.click()
        page.wait_for_timeout(1500)


# ── time and channel: precedence, not absence ──────────────────────────────

def test_an_unmatched_contact_shows_the_time_the_model_gave_it(page):
    """It has no frame to take one from. Before this it drew a dash — the
    same dash the card draws when a lookup breaks."""
    try:
        page.evaluate("""() => {
          const r = REVIEWS.find(x => x.id === state.selected);
          window.__narrKeep = JSON.parse(JSON.stringify(r.rca.supportNotes));
          const orphan = r.rca.supportNotes.find(n => /99999/.test(n.zd_ref || ''));
          orphan.time = '23 Jul 09:14'; orphan.channel = 'call';
          r.rca.v3.support_interaction_notes = r.rca.supportNotes;
          renderRcaCol();
        }""")
        page.wait_for_timeout(300)
        html = _section_html(page)
        assert "23 Jul 09:14" in html, \
            "an off-Zendesk contact still renders a dash where its time is"
        assert "call" in html.lower()
    finally:
        _restore(page)


def test_the_frame_still_wins_on_a_matched_contact(page):
    """Precedence is the point. The frame's time is verifiable; the model's
    is not, and letting the model's override it is why these fields were
    struck from the schema in the first place."""
    try:
        frame_time = page.evaluate(
            "() => (REVIEWS.find(x => x.id === state.selected).rca.supportFrames[0] || {}).time")
        assert frame_time, "the fixture has no frame time to be overridden"
        _inject(page, {"time": "01 Jan 00:00", "channel": "email"})
        html = _section_html(page)
        assert frame_time in html, "the frame's time stopped being shown"
        assert "01 Jan 00:00" not in html, (
            "the model's time overrode the ticket's — the frame is the fact "
            "and the model's is the fallback, not the other way round")
    finally:
        _restore(page)


# ── nothing else broke ─────────────────────────────────────────────────────

def test_the_empty_state_is_untouched(page):
    """A contact section with no contacts must still say so plainly, with no
    numbered row and no pills."""
    html = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = [r.rca.supportFrames, r.rca.supportNotes,
                    r.rca.v3.support_interaction_notes];
      r.rca.supportFrames = []; r.rca.supportNotes = [];
      r.rca.v3.support_interaction_notes = [];
      renderRcaCol();
      const el = [...document.querySelectorAll('.interactions')]
        .find(e => e.querySelector(':scope > .interactions-empty'));
      const out = el ? el.innerHTML : null;
      [r.rca.supportFrames, r.rca.supportNotes,
       r.rca.v3.support_interaction_notes] = keep;
      renderRcaCol();
      return out; }""")
    assert html and "never reached support" in html
    assert "convo-narr" not in html


def test_the_page_still_works_after_all_this(page):
    assert page.errors == [], page.errors
