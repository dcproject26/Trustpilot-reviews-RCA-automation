"""Column resize — the facts ↔ RCA boundary on the case screen.

The workbench made the inbox a full-width page, so there is no inbox column to
resize any more. The one draggable boundary left is between the facts column
and the RCA column, and it lives on the case screen (a review must be open for
either column to exist). Dragged, not asserted at source: a handle bound to
nothing renders exactly like one that works — the failure this suite has hit
before — and a width that does not survive a reload appears to work once.

The RCA column is the flex remainder, so sizing the facts column sizes both.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _w(page, sel):
    return page.evaluate(
        "(s) => { const e = document.querySelector(s);"
        "return e ? Math.round(e.getBoundingClientRect().width) : null; }", sel)


def _drag(page, which, dx):
    h = page.locator(f'[data-resize="{which}"]')
    box = h.bounding_box()
    assert box, f"the {which} handle has no box — it is not laid out"
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 + dx, box["y"] + box["height"] / 2,
                    steps=8)
    page.mouse.up()
    page.wait_for_timeout(250)


def _reset(page):
    page.evaluate("""() => {
      document.documentElement.style.removeProperty('--col-inbox');
      document.documentElement.style.removeProperty('--col-facts');
      localStorage.removeItem('orm.colWidths'); }""")
    page.wait_for_timeout(150)


def _to_inbox(page):
    page.evaluate("() => { state.screen = 'inbox'; applyScreen(); renderInbox(); }")
    page.wait_for_selector("#inbox-search", state="visible", timeout=15000)


def _to_case(page):
    """The page fixture already opens a case; this restores it after a test
    that stepped back to the inbox."""
    if page.locator('.main.screen-case').count() == 0:
        page.evaluate("() => { state.screen = 'case'; applyScreen(); }")
        page.wait_for_selector('[data-resize="facts"]', state="visible", timeout=15000)


# ── the inbox is a full-width page now, not a resizable column ───────────────

def test_the_inbox_is_a_full_width_page_not_a_resizable_column(page):
    """THE STRUCTURAL CHANGE. The queue used to be a 300px column with a drag
    handle; it is now the whole surface, and its old handle is not usable."""
    try:
        _to_inbox(page)
        inbox = _w(page, ".inbox")
        main = _w(page, ".main")
        assert inbox > main - 5, f"the inbox does not fill the page: {inbox}/{main}"
        assert not page.locator('[data-resize="inbox"]').is_visible(), \
            "the inbox still shows a resize handle"
    finally:
        _to_case(page)


# ── the facts handle, on the case screen ────────────────────────────────────

def test_the_facts_handle_is_there(page):
    assert page.locator('[data-resize="facts"]').count() == 1
    assert page.locator('[data-resize="facts"]').is_visible()


def test_dragging_widens_the_facts_column(page):
    try:
        before = _w(page, ".review-col")
        _drag(page, "facts", 100)
        after = _w(page, ".review-col")
        assert after > before + 50, (
            f"the facts column did not move: {before} -> {after}. The handle "
            f"renders and is bound to nothing.")
    finally:
        _reset(page)


def test_dragging_narrows_it_too(page):
    try:
        _drag(page, "facts", 100)
        wide = _w(page, ".review-col")
        _drag(page, "facts", -100)
        assert _w(page, ".review-col") < wide - 40
    finally:
        _reset(page)


def test_the_rca_column_takes_the_remainder(page):
    """It has no width of its own. If it stops absorbing the change, widening
    the facts column pushes content off screen instead of reflowing."""
    try:
        rca_before = _w(page, ".rca-col")
        _drag(page, "facts", 120)
        rca_after = _w(page, ".rca-col")
        assert rca_after < rca_before - 50, (
            f"the RCA column did not give up the space: {rca_before} -> "
            f"{rca_after}")
    finally:
        _reset(page)


def test_a_column_cannot_be_dragged_to_nothing(page):
    """Dragged past its minimum the facts column would vanish, and there would
    be no handle left to drag it back with. Min is 280."""
    try:
        _drag(page, "facts", -900)
        assert _w(page, ".review-col") >= 275, \
            "the facts column collapsed past its minimum"
    finally:
        _reset(page)


def test_the_width_survives_a_reload(page):
    """A resize that resets on every load is one nobody uses twice."""
    try:
        _drag(page, "facts", 100)
        want = _w(page, ".review-col")
        page.reload(wait_until="load")
        page.wait_for_timeout(900)
        # A reload lands on the inbox screen; open a case to see the columns.
        page.wait_for_selector(".inbox-row", timeout=15000)
        page.locator(".inbox-row").first.click()
        page.wait_for_timeout(600)
        got = _w(page, ".review-col")
        assert abs(got - want) < 6, f"width was {want}, came back {got}"
    finally:
        _reset(page)
        page.reload(wait_until="load")
        page.wait_for_selector(".inbox-row", timeout=15000)
        page.locator(".inbox-row").first.click()
        page.wait_for_timeout(1200)


def test_arrow_keys_resize_it_too(page):
    """A drag-only control is unusable for anyone who cannot drag, and the
    handle is already focusable."""
    try:
        before = _w(page, ".review-col")
        page.focus('[data-resize="facts"]')
        for _ in range(4):
            page.keyboard.press("ArrowRight")
        page.wait_for_timeout(200)
        assert _w(page, ".review-col") > before, "arrow keys do nothing"
    finally:
        _reset(page)


def test_double_click_restores_the_default(page):
    try:
        _drag(page, "facts", 140)
        assert _w(page, ".review-col") > 480
        page.dblclick('[data-resize="facts"]')
        page.wait_for_timeout(250)
        assert abs(_w(page, ".review-col") - 410) < 6, \
            "double-click did not restore the stylesheet default"
    finally:
        _reset(page)


def test_the_handle_is_wide_enough_to_hit(page):
    """A 1px target is a target people miss."""
    box = page.locator('[data-resize="facts"]').bounding_box()
    assert box["width"] >= 4, f"the hit area is {box['width']}px"


def test_resizing_does_not_break_the_page(page):
    try:
        _drag(page, "facts", 60)
        _drag(page, "facts", -60)
        assert page.errors == [], page.errors
        assert page.locator("#rca-col .section").count() > 0, \
            "the RCA column stopped rendering after a resize"
    finally:
        _reset(page)
