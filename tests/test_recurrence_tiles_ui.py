"""The recurrence tile, its booking ids, and the moved completion tiles.

The server half is driven in test_recurrence_scope_and_bids.py. This is the
other end: a payload carrying counts and ids and a renderer that draws only
the counts is the same defect one layer up, and it looks like a working panel.

Recurrence is TID+VID only. Completion is the separate case and shows two
scopes — TID+VID and VID — so the tests here also check the TGID completion
tile is gone rather than sitting alongside as a third.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


RECURRENCE = {
    "tidvid": {"reviews": 3, "reviews_total": 11, "review_ids": ["9001", "9002"],
               "support": 5, "support_total": 13, "support_ids": ["9003"],
               "scope": "TID + VID"},
}


def _inject(page, over=None):
    payload = {"recurrence": RECURRENCE,
               "tidvid_completion_rate": 0.82,
               "ff_tidvid": {"total": 240, "needs_attention": True},
               "vid_completion_rate": 0.97,
               "ff_vid": {"total": 900, "needs_attention": False},
               "_window_days": 30, "_window_label": "last 30 days"}
    payload.update(over or {})
    page.evaluate("""(p) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__insKeep === undefined)
        window.__insKeep = JSON.parse(JSON.stringify(r.liveInsights || null));
      r.liveInsights = {...(r.liveInsights || {}), ...p};
      renderReviewCol();
    }""", payload)
    page.wait_for_timeout(400)


def _restore(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__insKeep !== undefined) r.liveInsights = window.__insKeep;
      window.__insKeep = undefined;
      renderReviewCol(); }""")
    page.wait_for_timeout(250)


def _panel(page):
    """innerText, so what is asserted is what a reader sees. Note the group
    headings are upper-cased by CSS, so heading assertions fold case rather
    than matching the source string - matching the source would pass against a
    heading nobody can read."""
    return page.evaluate(
        "() => { const e = document.querySelector('.insights-grid');"
        "return e ? e.innerText : ''; }")


# ── both groups are drawn ──────────────────────────────────────────────────

def test_the_panel_renders_at_all(page):
    """The precondition. Every assertion below would pass vacuously against an
    empty panel."""
    try:
        _inject(page)
        assert "recurring" in _panel(page).lower(), _panel(page)[:400]
    finally:
        _restore(page)


def test_there_is_exactly_one_recurrence_group(page):
    """A second heading would answer a different question under the same
    tile."""
    try:
        _inject(page)
        low = _panel(page).lower()
        assert low.count("is this issue recurring") == 1, \
            f"{low.count('is this issue recurring')} recurrence groups on the panel"
    finally:
        _restore(page)


def test_the_counts_are_on_screen(page):
    try:
        _inject(page)
        txt = _panel(page)
        assert "of 11" in txt, "the review denominator is missing"
        assert "of 13" in txt, "the support denominator is missing"
    finally:
        _restore(page)


# ── booking ids ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bid", ["9001", "9002", "9003"])
def test_the_booking_ids_are_shown(page, bid):
    """"also give the BID if any found here." A count is a number; a booking
    id is something an associate can open."""
    try:
        _inject(page)
        assert bid in _panel(page), f"booking id {bid} is not on the panel"
    finally:
        _restore(page)


def test_no_ids_draws_no_id_row(page):
    """An empty id row reads as ids that failed to load."""
    try:
        _inject(page, {"recurrence": {"tidvid": {
            **RECURRENCE["tidvid"], "review_ids": [], "support_ids": []}}})
        txt = _panel(page)
        assert "9001" not in txt
        assert "of 11" in txt, "the counts stopped drawing too"
    finally:
        _restore(page)


# ── completion moved ───────────────────────────────────────────────────────

def test_completion_shows_tid_vid_and_vid(page):
    try:
        _inject(page)
        txt = _panel(page)
        assert "Completion · TID · VID" in txt, \
            "the TID+VID completion tile is missing"
        assert "Completion · VID" in txt
    finally:
        _restore(page)


def test_the_tgid_completion_tile_is_gone(page):
    """It was replaced, not added to. Leaving it would put three completion
    tiles on a panel that was asked for two."""
    try:
        _inject(page)
        assert "Completion · TGID" not in _panel(page)
    finally:
        _restore(page)


def test_the_flag_button_follows_a_tile_that_is_visible(page):
    """It used to fire on the TGID rate. With that tile gone the button would
    appear over two healthy-looking numbers with nothing on the card to
    explain it — flagging a vendor on evidence the associate never saw."""
    try:
        _inject(page, {"ff_tidvid": {"total": 240, "needs_attention": False},
                       "ff_vid": {"total": 900, "needs_attention": False},
                       "tidvid_completion_rate": 0.99,
                       "vid_completion_rate": 0.99})
        assert page.locator("[data-flag-biz]").count() == 0, \
            "the flag button is showing with both visible rates healthy"
        _inject(page, {"ff_tidvid": {"total": 240, "needs_attention": True},
                       "tidvid_completion_rate": 0.82})
        assert page.locator("[data-flag-biz]").count() == 1, \
            "a failing TID+VID rate raises no flag"
    finally:
        _restore(page)


def test_the_page_is_still_healthy(page):
    assert page.errors == [], page.errors
