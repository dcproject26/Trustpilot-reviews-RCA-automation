"""The Experience-insights window picker, clicked.

"the figures should be updating on on the basis of the the date windo but its
not" — and nothing on the panel could settle whether the click, the request or
the query was at fault, because a switch that silently did nothing rendered
identically to one that worked. Three separate near-misses had already been
fixed in this handler (a guard on the wrong booking key, a ReferenceError into
a bare catch, two handlers on one route) and each time the only symptom was
numbers that would not change.

So the window now travels WITH the figures, from the same payload, and every
way the switch can fail says which one it was. These tests click the buttons
and read what the panel says afterwards — the only kind of test that can tell
a live control from a dead one.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _window_line(page):
    return page.evaluate(
        "() => { const e = document.querySelector('.insight-window');"
        "return e ? e.textContent.trim() : null; }")


def _note_texts(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('.insight-note')]"
        ".map(e => e.textContent.trim())")


def _click_window(page, w):
    """Click, then WAIT FOR THE ROUND TRIP — not for a fixed 700ms.

    The click fires a request to /insights and the panel re-renders when it
    answers. 700ms was enough on a quiet machine and not enough under a full
    suite sharing one uvicorn, so these tests failed in the batch and passed
    every time they were run on their own.

    A fixed sleep cannot tell a slow answer from an absent one, which is the
    precise distinction this module exists to make — the docstring above is
    about a switch that "silently did nothing" rendering identically to one
    that worked, and the test for it was itself timing-based.

    Settled means either the figures moved to the window we asked for, or a
    note appeared saying they could not. Both are answers; only "neither, yet"
    is worth waiting on.

    A timeout here is deliberately NOT raised: the assertions in each test
    already say what went wrong in the terms that test cares about, and a bare
    TimeoutError would replace "clicked 7d; the figures on screen are for 90
    days" with a stack trace naming this helper.
    """
    want = int(w.rstrip("d"))
    before = len(_note_texts(page))
    page.click(f'.window-btn[data-window="{w}"]')
    try:
        page.wait_for_function(
            """([want, before]) => {
                 const r = REVIEWS.find(x => x.id === state.selected);
                 const got = r && r.liveInsights ? r.liveInsights._window_days : null;
                 return got === want
                     || document.querySelectorAll('.insight-note').length > before;
               }""", arg=[want, before], timeout=10000)
    except Exception:
        pass
    # The render that reads the response runs on the next frame.
    page.wait_for_timeout(120)


def test_the_panel_states_the_window_its_figures_cover(page):
    """A count with no range is not checkable. The picker states an intention;
    this states the result.

    Read off the SCREEN, not out of the DOM. textContent is happily returned
    for an element carrying `hidden`, so an assertion on the string alone
    passed against a build where the line had been hidden outright — mutation
    testing caught that. What is in the document and what the reader can see
    are different claims.
    """
    line = _window_line(page)
    assert line, "the insights panel does not say which window it is showing"
    assert any(ch.isdigit() for ch in line), line
    assert page.locator(".insight-window").first.is_visible(), \
        "the window line is in the DOM but not on the screen"
    box = page.locator(".insight-window").first.bounding_box()
    assert box and box["height"] > 0 and box["width"] > 0, \
        f"the window line renders with no size: {box}"


def test_the_default_window_is_30d(page):
    """The initial window must match the server default (30d,
    insights.py::window_days(default=30)). They disagreed once — 90d was
    highlighted while the server computed 30d — so every first load showed one
    window's numbers under another window's button. Driven off the live state,
    so a reverted default is caught rather than spelled-checked."""
    assert page.evaluate("() => state.insightsWindow") == "30d", \
        "the dashboard's default insights window is not 30d — it disagrees " \
        "with the server's 30d default on first load"


@pytest.mark.parametrize("w,days", [("7d", 7), ("30d", 30), ("90d", 90)])
def test_clicking_a_window_moves_the_figures_to_it(page, w, days):
    """The payload's own _window_days has to follow the button. If the click,
    the request or the query does not happen, this is where it shows."""
    _click_window(page, w)
    got = page.evaluate(
        "() => { const r = REVIEWS.find(x => x.id === state.selected);"
        "return r && r.liveInsights ? r.liveInsights._window_days : null; }")
    assert got == days, (
        f"clicked {w}; the figures on screen are for {got} days. The switch "
        f"did not reach the server, or the server ignored the window.")
    assert str(days) in _window_line(page), _window_line(page)


def test_the_button_and_the_figures_cannot_disagree_silently(page):
    """Belt and braces: whatever the state says, the caption is built from the
    payload, so a stale figure can never sit under a fresh label without the
    panel marking it."""
    _click_window(page, "7d")
    stale = page.evaluate(
        "() => { const e = document.querySelector('.insight-window');"
        "return e ? e.classList.contains('stale') : null; }")
    want = page.evaluate("() => windowDays(state.insightsWindow)")
    got = page.evaluate(
        "() => { const r = REVIEWS.find(x => x.id === state.selected);"
        "return r && r.liveInsights ? r.liveInsights._window_days : null; }")
    assert stale is (want != got), (
        f"picked {want} days, showing {got}, marked stale={stale} — the mark "
        f"and the mismatch have come apart")


def test_a_failed_switch_says_so_instead_of_keeping_the_old_numbers(page):
    """The whole point. A 500 leaves the previous window's figures on screen,
    which is indistinguishable from a successful switch that happened to
    return the same numbers — unless the panel says which it was.
    """
    _click_window(page, "90d")
    before = _window_line(page)
    page.route("**/insights?**", lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"detail":"boom"}'))
    try:
        _click_window(page, "7d")
        notes = " ".join(_note_texts(page))
        assert "500" in notes or "Could not recompute" in notes, (
            "the request failed and the panel said nothing — a dead switch "
            f"looks exactly like a working one. Notes were: {notes!r}")
        line = _window_line(page)
        assert line.startswith(before), \
            f"the caption moved to a window that was never computed: {line!r}"
        assert "7d" in line, \
            "the caption does not say which window was asked for and refused"
    finally:
        page.unroute("**/insights?**")
        _click_window(page, "90d")


def test_windowDays_returns_nothing_for_a_string_it_cannot_parse(page):
    """It feeds a mismatch warning. Guessing a number for an unknown string
    would raise a false alarm, which is worse than no alarm here — the panel
    would accuse a working switch of being broken."""
    assert page.evaluate("() => windowDays('7d')") == 7
    assert page.evaluate("() => windowDays('4w')") == 28
    assert page.evaluate("() => windowDays('nonsense')") is None
    assert page.evaluate("() => windowDays('')") is None
    assert page.evaluate("() => windowDays(null)") is None
