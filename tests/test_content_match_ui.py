"""The content-vs-booking warning draws on a mismatch and on nothing else.

The check itself is tested in test_content_match.py. This is whether the card
draws it — and, more importantly, whether it draws it ONLY when it should.

A mutation loosening the condition to `if (!cm) return ''` survived the whole
suite: every content-match test was server-side, so nothing knew what the card
did with the three states. That mutation would have put an amber "this review
reads like a different product" banner on nearly every review, since
`unchecked` is the common case — and a warning that appears on everything is
one people stop reading, which costs more than never having built it.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401

MISMATCH = {"state": "mismatch", "review_family": "city card",
            "booking_family": "guided tour", "experience": "Rome Guided Tour",
            "why": "the review describes a city card; this booking is a guided tour"}


def _set(page, cm):
    page.evaluate("""(cm) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__cmKeep === undefined)
        window.__cmKeep = r.contentMatch === undefined ? null : r.contentMatch;
      r.contentMatch = cm;
      renderReviewCol();
    }""", cm)
    page.wait_for_timeout(350)


def _restore(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__cmKeep !== undefined) r.contentMatch = window.__cmKeep;
      window.__cmKeep = undefined; renderReviewCol(); }""")
    page.wait_for_timeout(250)


def _row(page):
    return page.locator(".content-mismatch")


def test_it_draws_on_a_mismatch(page):
    try:
        _set(page, MISMATCH)
        assert _row(page).count() == 1, \
            "a review describing a different product raises nothing"
    finally:
        _restore(page)


def test_it_names_both_products(page):
    """"This looks wrong" without saying what it saw is a warning nobody can
    act on."""
    try:
        _set(page, MISMATCH)
        txt = _row(page).inner_text()
        assert "city card" in txt
        assert "guided tour" in txt
    finally:
        _restore(page)


def test_it_names_the_booked_experience(page):
    try:
        _set(page, MISMATCH)
        assert "Rome Guided Tour" in _row(page).inner_text()
    finally:
        _restore(page)


def test_it_says_what_probably_happened(page):
    """The actionable part: the guest may have quoted the wrong reference."""
    try:
        _set(page, MISMATCH)
        assert "different booking" in _row(page).inner_text().lower()
    finally:
        _restore(page)


# ── and on nothing else ────────────────────────────────────────────────────

@pytest.mark.parametrize("state", ["unchecked", "match"])
def test_it_does_not_draw_on_any_other_state(page, state):
    """`unchecked` is the COMMON case — most reviews name no product we
    recognise. A banner there appears on nearly every review, and a warning on
    everything is one people stop reading."""
    try:
        _set(page, {**MISMATCH, "state": state})
        assert _row(page).count() == 0, \
            f"the mismatch banner fired on state={state!r}"
    finally:
        _restore(page)


def test_it_does_not_draw_when_there_is_no_check_at_all(page):
    """Older drafts, and any payload predating the check."""
    try:
        _set(page, None)
        assert _row(page).count() == 0
    finally:
        _restore(page)


def test_the_booking_match_card_still_renders_without_it(page):
    try:
        _set(page, None)
        assert page.locator(".match-card").count() >= 1, \
            "the match card stopped rendering when there is no content check"
    finally:
        _restore(page)


def test_the_page_is_still_healthy(page):
    assert page.errors == [], page.errors
