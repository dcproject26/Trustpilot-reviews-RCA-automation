""""How we built this match" starts shut.

It is the working-out, not the answer. On a card where the match is obvious it
is eight lines of pipeline vocabulary sitting above the thing the reader came
for, and they scroll past it every time. On a card where the match is in doubt
it is the first thing they open. Shut by default and one click from open
serves both; a permanently open panel serves only the second.

WHAT MUST NOT HAPPEN: a collapsed section and a review with no trail at all
looking the same. The closed header carries the step count for exactly that
reason — a reader has to be able to see there IS working-out without expanding
it.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _trail(page):
    return page.evaluate("""() => {
      const t = document.querySelector('.confidence-trail');
      if (!t) return null;
      return {
        shut: t.classList.contains('is-shut'),
        steps: t.querySelectorAll('.conf-step').length,
        // Lower-cased: the label is upper-cased by CSS, so innerText comes
        // back shouting and a case-sensitive assertion tests the stylesheet.
        header: ((t.querySelector('.confidence-trail-label') || {}).innerText || '').toLowerCase(),
        clickable: !!t.querySelector('[data-trail-sec]'),
      }; }""")


def test_the_section_renders_at_all(page):
    """NOT BUILT guard for everything below."""
    assert _trail(page), "there is no match trail on the card"


def test_it_starts_shut(page):
    got = _trail(page)
    assert got["shut"] is True, got
    assert got["steps"] == 0, (
        f"{got['steps']} trail steps are on screen before anyone opened it")


def test_the_closed_header_says_there_is_working_out_behind_it(page):
    """Otherwise a collapsed trail and a review whose matching never ran are
    the same blank line."""
    got = _trail(page)
    assert "step" in got["header"], got["header"]
    assert any(c.isdigit() for c in got["header"]), got["header"]


def test_the_header_is_a_control(page):
    got = _trail(page)
    assert got["clickable"], "the header is not bound to anything"


def test_clicking_opens_it_and_clicking_again_shuts_it(page):
    """A dead control looks exactly like a working one until it is clicked."""
    page.click(".confidence-trail [data-trail-sec]")
    page.wait_for_timeout(250)
    opened = _trail(page)
    page.click(".confidence-trail [data-trail-sec]")
    page.wait_for_timeout(250)
    shut = _trail(page)
    assert opened["steps"] > 0, "opening it revealed no steps"
    assert opened["shut"] is False, opened
    assert shut["steps"] == 0, "it would not shut again"


def test_a_review_with_no_trail_renders_no_section(page):
    """An empty header with a chevron is a promise of content that is not
    there."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.confidenceTrail;
      r.confidenceTrail = [];
      renderReviewCol(); renderRcaCol();
      const n = document.querySelectorAll('.confidence-trail').length;
      r.confidenceTrail = keep;
      renderReviewCol(); renderRcaCol();
      return n; }""")
    assert got == 0, f"{got} empty trail sections rendered"


def test_the_notable_count_is_shown_when_there_is_something_to_read(page):
    """A trail that is all passes and a trail with three warnings are
    different things to a reader deciding whether to open it."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.confidenceTrail;
      r.confidenceTrail = [{mark: 'pass', text: 'a'},
                           {mark: 'warn', text: 'b'},
                           {mark: 'fail', text: 'c'}];
      renderReviewCol();
      const h = ((document.querySelector('.confidence-trail-label') || {}).innerText || '').toLowerCase();
      r.confidenceTrail = keep;
      renderReviewCol();
      return h; }""")
    assert "2 to read" in got, got
