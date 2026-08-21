"""The RCA slide-over — the case screen's third surface, driven in a browser.

The workbench replaced the always-visible third column with a slide-over. Two
structural facts came with it:

  * There is no draggable column boundary any more. The queue is a full-width
    page (Stage 2) and the RCA overlays the facts instead of sharing an edge
    with them (Stage 4), so nothing is resized. The old handle, its CSS
    variables and the width store were deleted; a test that still dragged them
    would be dragging a control that no longer renders — the phantom-mechanism
    trap of CLAUDE.md rule 1 — so this file replaces the resize suite outright.

  * The slide-over is NOT modal. The facts stay on screen and clickable beside
    it, because the associate works the facts and the RCA together. A backdrop
    over the facts would be the modal version and is exactly what was removed;
    the tests below prove a point inside the facts still belongs to the facts
    while the panel is open, not to an overlay.

Driven, not asserted at source: an open/close handler bound to nothing renders
identically to one that works, and "the facts are clickable" is a claim about
what sits on top of a pixel — neither survives a source grep.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _is_open(page):
    return page.evaluate("() => !!document.querySelector('.case-body.rca-open')")


def _close(page):
    """Return the case to the closed state the case screen starts in."""
    if _is_open(page):
        page.click("#rca-close")
        page.wait_for_selector(".case-body:not(.rca-open)", timeout=15000)
        page.wait_for_timeout(250)          # let the .18s slide finish


def _open(page):
    page.click("#open-rca")
    page.wait_for_selector(".case-body.rca-open #rca-col", timeout=15000)
    page.wait_for_timeout(250)


def _box(page, sel):
    return page.evaluate(
        "(s) => { const e = document.querySelector(s);"
        " if (!e) return null; const r = e.getBoundingClientRect();"
        " return {left: r.left, right: r.right, top: r.top, width: r.width,"
        "         height: r.height}; }", sel)


# ── the structural change: no resize, full-width queue ──────────────────────

def test_there_is_no_column_resize_handle(page):
    """THE STRUCTURAL CHANGE. The facts|RCA (and inbox|facts) drag boundaries
    are gone with the slide-over — neither the handle nor its class remains."""
    assert page.locator('[data-resize]').count() == 0, \
        "a resize handle survives — the drag boundary was supposed to go"
    assert page.locator('.col-resize').count() == 0, \
        "the resize-handle element is still in the DOM"


def test_the_resize_subsystem_left_no_globals(page):
    """The JS went too, not just the markup. A handler kept alive against a
    handle that no longer exists is the mechanism-wired-to-nothing trap. These
    were top-level `function`s, so they would be window properties if they
    still existed — their absence is real, not a const that never was one."""
    gone = page.evaluate("""() => ({
        setCol: typeof window._setCol,
        colWidth: typeof window._colWidth,
        save:   typeof window._saveColWidths }) """)
    assert gone == {"setCol": "undefined", "colWidth": "undefined",
                    "save": "undefined"}, f"resize globals still defined: {gone}"


def test_the_inbox_is_a_full_width_page(page):
    """The queue fills the surface; it is not a fixed column beside the case."""
    try:
        page.evaluate("() => { state.screen = 'inbox'; applyScreen(); renderInbox(); }")
        page.wait_for_selector("#inbox-search", state="visible", timeout=15000)
        inbox = _box(page, ".inbox")["width"]
        main = _box(page, ".main")["width"]
        assert inbox > main - 5, f"the inbox does not fill the page: {inbox}/{main}"
    finally:
        page.evaluate("() => { state.screen = 'case'; applyScreen(); }")
        page.wait_for_selector("#rca-col", timeout=15000)


# ── the slide-over opens and closes ─────────────────────────────────────────

def test_it_starts_closed_on_the_case_screen(page):
    """Opening a case shows the facts; the RCA is reached deliberately, not
    thrown up over the facts the moment the case loads."""
    _close(page)
    assert not _is_open(page), "the case opened with the RCA already over it"
    # the panel is off to the right, not on screen
    b = _box(page, "#rca-col")
    assert b["left"] >= _box(page, ".main")["right"] - 5, \
        f"the closed panel is still on screen at left={b['left']}"


def test_the_header_button_opens_it(page):
    _close(page)
    _open(page)
    assert _is_open(page)
    panel = _box(page, "#rca-col")
    main = _box(page, ".main")
    # it slid in: its right edge is at the viewport edge, its body is on screen
    assert panel["left"] < main["right"] - 100, \
        f"the panel did not slide on screen: left={panel['left']}"


def test_the_close_button_closes_it(page):
    _close(page)
    _open(page)
    page.click("#rca-close")
    page.wait_for_selector(".case-body:not(.rca-open)", timeout=15000)
    assert not _is_open(page)


def test_escape_closes_it(page):
    _close(page)
    _open(page)
    page.keyboard.press("Escape")
    page.wait_for_selector(".case-body:not(.rca-open)", timeout=15000)
    assert not _is_open(page), "Escape did not close the slide-over"


def test_leaving_for_the_inbox_does_not_leave_it_hanging_open(page):
    """A panel that stayed 'open' in state while its case was gone would spring
    back the moment another case is opened. Going back to the queue closes it."""
    try:
        _close(page)
        _open(page)
        page.evaluate("() => { state.screen = 'inbox'; applyScreen(); renderInbox(); }")
        page.wait_for_selector("#inbox-search", state="visible", timeout=15000)
        page.locator(".inbox-row").first.click()
        page.wait_for_selector("#open-rca", timeout=15000)
        page.wait_for_timeout(250)
        assert not _is_open(page), \
            "the next case opened with the RCA already flung over it"
    finally:
        _close(page)


# ── the slide-over is not modal ─────────────────────────────────────────────

def test_there_is_no_backdrop_over_the_facts(page):
    """The modal version had a backdrop intercepting clicks on the facts; the
    non-modal one has none. Its absence is the whole point of the redesign."""
    _close(page)
    _open(page)
    assert page.locator(".rca-backdrop").count() == 0, \
        "a backdrop is back over the facts — the slide-over went modal again"


def test_a_point_in_the_facts_still_belongs_to_the_facts(page):
    """The load-bearing non-modal claim: with the panel open, a pixel over the
    facts hits a facts element, not an overlay. If anything sat on top, the
    confidence trail, the timeline's + Add and the machinery toggle — all in
    this column — would stop taking clicks."""
    _close(page)
    _open(page)
    facts = _box(page, ".review-col")
    panel = _box(page, "#rca-col")
    # a point well inside the facts, safely left of the panel's left edge
    x = min(facts["left"] + 60, panel["left"] - 40)
    y = facts["top"] + 80
    owner = page.evaluate(
        "(p) => { const el = document.elementFromPoint(p.x, p.y);"
        " return el ? !!el.closest('.review-col') : null; }",
        {"x": x, "y": y})
    assert owner is True, \
        "a point over the facts is covered by something that is not the facts"


def test_the_facts_make_room_rather_than_hide_behind_the_panel(page):
    """Non-modal means the facts reflow left of the panel, not disappear under
    it. Their right edge clears the panel's left edge when it is open."""
    _close(page)
    _open(page)
    facts = _box(page, ".review-col")
    panel = _box(page, "#rca-col")
    assert facts["right"] <= panel["left"] + 2, (
        f"the facts run under the panel: facts right={facts['right']}, "
        f"panel left={panel['left']}")


def test_the_panel_is_a_fixed_width_overlay(page):
    """It is a slide-over, not the old flex remainder: a set width regardless
    of how wide the facts are."""
    _close(page)
    _open(page)
    w = _box(page, "#rca-col")["width"]
    assert 600 <= w <= 720, f"the panel is {w}px, not the fixed slide-over width"


def test_opening_it_does_not_throw(page):
    _close(page)
    before = list(page.errors)
    _open(page)
    page.click("#rca-close")
    page.wait_for_selector(".case-body:not(.rca-open)", timeout=15000)
    assert page.errors == before, page.errors
    # and the panel still has its content after a close/open cycle
    _open(page)
    assert page.locator("#rca-col .section").count() > 0, \
        "the RCA panel stopped rendering after an open/close cycle"
