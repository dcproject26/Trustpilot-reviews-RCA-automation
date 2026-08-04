"""The per-contact story renders, is editable, and survives a save.

Rule 10b asks the model for what the guest came with, what we said, and
whether we raised it internally. That last one exists nowhere else in the
pipeline — no Zendesk frame carries it — so if the card does not draw it the
model's answer has no reader at all.

This is the failure this repo keeps hitting from the other end: the validator
was dropping every one of these fields, so the section rendered exactly as it
would have if the model had answered nothing. Widening the projection is only
half the fix; a field that survives validation and is never drawn is the same
bug one layer up.

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
    "raised_internally": "Raised as an ops task after we promised 24 hours",
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

@pytest.mark.parametrize("field", list(NARR))
def test_each_narrative_field_is_drawn(page, field):
    try:
        _inject(page)
        _open_all(page)
        html = _section_html(page)
        assert NARR[field] in html, (
            f"{field} survived validation and is not drawn — the model's "
            f"answer has no reader")
    finally:
        _restore(page)


def test_the_summary_is_bullets_not_a_labelled_form(page):
    """The ask was to summarise the interaction in concise bullet points, one
    sentence each. A "Guest said: … / We said: …" grid turns a conversation
    into a form — it reads as five fields somebody has to fill rather than as
    an account of what happened."""
    try:
        _inject(page)
        _open_all(page)
        html = _section_html(page)
        assert "convo-bullet" in html, "the summary is not rendered as bullets"
        for label in ("Guest said", "We said", "Raised internally"):
            assert label not in html, (
                f"{label!r} is drawn as a field label — this is the labelled "
                f"form again, not a summary")
    finally:
        _restore(page)


def test_each_bullet_is_its_own_list_item(page):
    """Three sentences run into one bullet is not a summary of a contact."""
    try:
        _inject(page)
        _open_all(page)
        n = page.evaluate(
            "() => document.querySelectorAll('.convo-narr .convo-bullet').length")
        assert n >= len(NARR), f"expected {len(NARR)} bullets, found {n}"
    finally:
        _restore(page)


def test_the_bullets_keep_the_order_the_contact_happened_in(page):
    """Came with, we said, raised internally. Out of order it stops reading as
    an account of the contact."""
    try:
        _inject(page)
        _open_all(page)
        texts = page.evaluate(
            "() => [...document.querySelectorAll('.convo-narr .convo-bullet')]"
            ".map(e => e.textContent.trim())")
        want = [NARR[k] for k in ("guest_said", "we_said", "raised_internally")]
        got = [t for t in texts if t in want]
        assert got == want, f"bullets are out of order: {got}"
    finally:
        _restore(page)


def test_the_field_with_no_other_source_is_drawn(page):
    """raised_internally. No frame carries it, so undrawn means unreadable —
    and it is the one that says whether a promise to the guest was kept."""
    try:
        _inject(page)
        _open_all(page)
        assert NARR["raised_internally"] in _section_html(page)
    finally:
        _restore(page)


# The card has two branches — a contact joined to a Zendesk frame, and one
# with no frame behind it — and they build their markup separately. Every test
# above drives note 0, which is the MATCHED one. Deleting the narrative from
# the unmatched branch passed all of them and the whole 1081-test suite; a
# mutation run is what found it.
#
# It is the branch that matters more, too: an off-Zendesk contact has no frame
# supplying a per-event guestSaid, so the narrative is the only account of it
# anywhere on the card.
ORPHAN = 1                                      # ZD-99999 in the fixture


def test_the_fixture_orphan_is_really_unmatched(page):
    """If this note ever gains a frame the tests below would silently start
    driving the matched branch again — which is how the gap arose."""
    why = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      return (r.rca.supportNotes[%d] || {}).zd_ref; }""" % ORPHAN)
    assert why and "99999" in str(why), f"note {ORPHAN} is not the orphan: {why}"
    assert "unmatched ZD reference" in _section_html(page), \
        "the unmatched branch is not being rendered at all"


@pytest.mark.parametrize("field", list(NARR))
def test_an_unmatched_contact_draws_its_narrative_too(page, field):
    try:
        _inject(page, which=ORPHAN)
        _open_all(page)
        html = _section_html(page)
        assert NARR[field] in html, (
            f"{field} is drawn for a matched contact and not for an unmatched "
            f"one — and the unmatched branch has no frame to fall back on")
    finally:
        _restore(page)


def test_an_unmatched_contact_s_narrative_is_editable(page):
    """It is the branch whose edit indexing has already been wrong once, when
    it used a position in the filtered orphan list instead of the real one."""
    try:
        _inject(page, which=ORPHAN)
        _open_all(page)
        for f in NARR:
            paths = page.evaluate(
                """(f) => [...document.querySelectorAll('[data-v3p]')]
                     .map(e => e.dataset.v3p)
                     .filter(p => p.endsWith('.' + f))""", f)
            assert any(p == f"support_interaction_notes.{ORPHAN}.{f}"
                       for p in paths), (
                f"{f} on the unmatched contact is not editable at its own "
                f"index — found {paths}")
    finally:
        _restore(page)


def test_a_null_field_on_an_unmatched_contact_draws_nothing(page):
    try:
        _inject(page, {"raised_internally": None}, which=ORPHAN)
        _open_all(page)
        html = _section_html(page)
        assert NARR["raised_internally"] not in html
        assert NARR["we_said"] in html, "the fields that came back stopped drawing"
    finally:
        _restore(page)


def test_a_null_field_draws_nothing_at_all(page):
    """The rule tells the model to leave an undetectable field null and write
    nothing. A row of empty labels would report five blanks as five things we
    looked for and failed to find."""
    try:
        _inject(page, {"raised_internally": None})
        _open_all(page)
        html = _section_html(page)
        assert NARR["raised_internally"] not in html, \
            "a bullet was drawn for a field the model left blank"
        assert NARR["we_said"] in html, "the fields that DID come back stopped drawing"
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
        el = page.locator('[data-v3p$=".raised_internally"]').first
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
