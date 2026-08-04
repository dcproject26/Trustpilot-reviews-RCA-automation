"""Both recurrence scopes reach the panel, and the completion tiles moved.

The server half is driven in test_recurrence_two_scopes.py. This is the other
end: a payload carrying both scopes and a renderer that draws one of them is
the same defect one layer up, and it looks like a working panel.

Two specific ways this goes wrong quietly:

  * both groups render but read the SAME numbers, because the second was
    copy-pasted and left pointing at the first scope's keys. Two identical
    tiles under two headings look like a vendor whose problem is experience-
    wide, which is a real finding — just not this one.
  * the TGID group renders and the TID+VID one is silently dropped, or the
    other way round. Either way the panel looks complete.

So the fixture gives the two scopes DIFFERENT numbers and the tests check the
right ones land under the right heading.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


RECURRENCE = {
    "tidvid": {"reviews": 3, "reviews_total": 11, "review_ids": ["9001", "9002"],
               "support": 5, "support_total": 13, "support_ids": ["9003"],
               "scope": "TID + VID"},
    "tgid":   {"reviews": 7, "reviews_total": 29, "review_ids": ["9101", "9102"],
               "support": 2, "support_total": 31, "support_ids": ["9103"],
               "scope": "TGID"},
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


def test_both_scopes_get_their_own_heading(page):
    try:
        _inject(page)
        txt = _panel(page)
        low = txt.lower()
        assert "recurring · tid · vid" in low, \
            "the vendor-version scope has no heading"
        assert "recurring · tgid" in low, \
            "the experience scope has no heading"
    finally:
        _restore(page)


def test_the_two_groups_do_not_show_the_same_numbers(page):
    """Copy-paste leaving the second group pointed at the first scope's keys
    renders two identical tiles under two headings — which reads as a finding
    rather than as a bug."""
    try:
        _inject(page)
        txt = _panel(page)
        assert "of 11" in txt, "the TID+VID review denominator is missing"
        assert "of 29" in txt, "the TGID review denominator is missing"
    finally:
        _restore(page)


@pytest.mark.parametrize("value", ["3", "5", "7", "2"])
def test_every_count_from_both_scopes_is_on_screen(page, value):
    try:
        _inject(page)
        assert value in _panel(page)
    finally:
        _restore(page)


def test_the_headings_match_the_panel_s_existing_style(page):
    """The rest of the panel labels scope with a middot — "Completion · TGID",
    "Average rating · TID · VID". A recurrence group phrased as a sentence
    would be the only one, and inconsistency reads as two different kinds of
    number."""
    try:
        _inject(page)
        low = _panel(page).lower()
        assert "is this issue recurring · tid · vid" in low
        assert "is this issue recurring · tgid" in low
    finally:
        _restore(page)


# ── booking ids ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bid", ["9001", "9002", "9003", "9101", "9102", "9103"])
def test_the_booking_ids_are_shown(page, bid):
    """"also give the BID if any found here." A count is a number; a booking
    id is something an associate can open."""
    try:
        _inject(page)
        assert bid in _panel(page), f"booking id {bid} is not on the panel"
    finally:
        _restore(page)


def test_a_scope_with_no_ids_draws_no_id_row(page):
    """An empty id row reads as ids that failed to load."""
    try:
        _inject(page, {"recurrence": {
            **RECURRENCE,
            "tgid": {**RECURRENCE["tgid"], "review_ids": [], "support_ids": []}}})
        txt = _panel(page)
        assert "9101" not in txt
        assert "9001" in txt, "the other scope's ids stopped drawing too"
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
