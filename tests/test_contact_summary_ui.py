"""The contact row: timestamp, channel pill, then the summary of it.

"customer interaction will be time stamp and pill followed by the summary of
of the interaction like i had stated in the first message."

So the head row carries the two facts and the body carries one account of what
happened. There are no per-field columns: what the summary has to cover — the
guest's issue, our reply, whether it was raised internally — is stated in the
prompt, not split into controls here.

Two things this file exists to catch:

  * an off-Zendesk contact losing its time and channel. Those were struck from
    the schema once, and a contact with no frame then rendered a dash: the same
    dash a broken lookup renders, on a contact where we knew the answer.
  * the model's values displacing a frame's. The frame is verifiable and the
    model's is not, so the frame wins wherever it exists — precedence, not
    absence.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401

ORPHAN = 1                                      # ZD-99999 in the fixture


def _set_note(page, over, which=0):
    page.evaluate("""([o, which]) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__cKeep === undefined)
        window.__cKeep = JSON.parse(JSON.stringify(r.rca.supportNotes));
      Object.assign(r.rca.supportNotes[which], o);
      r.rca.v3.support_interaction_notes = r.rca.supportNotes;
      renderRcaCol();
    }""", [over, which])
    page.wait_for_timeout(300)


def _restore(page):
    page.evaluate("""() => {
      if (window.__cKeep === undefined) return;
      const r = REVIEWS.find(x => x.id === state.selected);
      r.rca.supportNotes = window.__cKeep;
      r.rca.v3.support_interaction_notes = window.__cKeep;
      window.__cKeep = undefined;
      renderRcaCol(); }""")
    page.wait_for_timeout(200)


def _section(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('.interactions')]"
        ".map(e => e.innerHTML).join('')")


# ── the head row ───────────────────────────────────────────────────────────

def test_the_section_renders_at_all(page):
    """Every assertion below would pass vacuously against an empty section."""
    assert "convo-frame" in _section(page)


def test_a_matched_contact_shows_the_frame_s_time_and_channel(page):
    frame = page.evaluate(
        "() => REVIEWS.find(x => x.id === state.selected).rca.supportFrames[0] || {}")
    assert frame.get("time"), "the fixture has no frame time"
    html = _section(page)
    assert frame["time"] in html
    assert "convo-type-pill" in html, "no channel pill on the head row"


def test_the_summary_is_drawn(page):
    try:
        _set_note(page, {"summary": "Guest chased the voucher twice."})
        assert "Guest chased the voucher twice." in _section(page)
    finally:
        _restore(page)


def test_the_summary_is_editable(page):
    try:
        _set_note(page, {"summary": "x"})
        assert page.locator('[data-v3p$=".summary"]').count() >= 1, \
            "the summary is dead text — it cannot be corrected"
    finally:
        _restore(page)


# ── an off-Zendesk contact keeps its own time and channel ──────────────────

def test_the_fixture_orphan_is_really_unmatched(page):
    ref = page.evaluate(
        "() => (REVIEWS.find(x => x.id === state.selected)"
        ".rca.supportNotes[%d] || {}).zd_ref" % ORPHAN)
    assert ref and "99999" in str(ref), f"note {ORPHAN} is not the orphan: {ref}"


def test_an_unmatched_contact_shows_the_time_the_model_gave_it(page):
    """It has no frame to take one from. Before this it drew a dash."""
    try:
        _set_note(page, {"time": "23 Jul 09:14", "channel": "call"}, which=ORPHAN)
        html = _section(page)
        assert "23 Jul 09:14" in html, \
            "an off-Zendesk contact still renders a dash where its time is"
        assert "call" in html.lower()
    finally:
        _restore(page)


def test_it_is_still_marked_unverified(page):
    assert "unmatched ZD reference" in _section(page) or \
           "guest's account, unverified" in _section(page)


# ── precedence ─────────────────────────────────────────────────────────────

def test_the_frame_wins_over_the_model_on_a_matched_contact(page):
    """The frame's time is verifiable; the model's is not. Letting the model's
    override it is why these fields were struck from the schema once."""
    try:
        frame_time = page.evaluate(
            "() => (REVIEWS.find(x => x.id === state.selected)"
            ".rca.supportFrames[0] || {}).time")
        assert frame_time, "the fixture has no frame time to be overridden"
        _set_note(page, {"time": "01 Jan 00:00", "channel": "email"})
        html = _section(page)
        assert frame_time in html, "the frame's time stopped being shown"
        assert "01 Jan 00:00" not in html, (
            "the model's time overrode the ticket's — the frame is the fact "
            "and the model's is the fallback, not the other way round")
    finally:
        _restore(page)


# ── nothing invented in the layout ─────────────────────────────────────────

def test_no_field_labels_are_drawn(page):
    """The section is a summary, not a form. Labelled rows were mine, not the
    spec's, and they were removed for that reason."""
    html = _section(page)
    for label in ("Guest said", "We said", "Raised internally",
                  "Wait for human", "Guest replied", "Outcome"):
        assert label not in html, f"{label!r} is a field label on a summary"


def test_the_empty_state_is_untouched(page):
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
    for dressing in ("convo-num", "convo-type-pill", "convo-time"):
        assert dressing not in html, f"the empty state renders a {dressing}"


def test_the_page_is_still_healthy(page):
    assert page.errors == [], page.errors
