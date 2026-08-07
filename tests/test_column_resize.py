"""The three columns can be resized, and the sizes stick.

Dragged, not asserted at source. A handle that renders and is bound to nothing
looks exactly like one that works — the failure this suite has hit four times
now — and a resize that does not survive a reload is the same class of thing:
it appears to work, once.

Two boundaries, not three. The RCA column is the flex remainder, so sizing the
two fixed columns sizes all three and there is no third number to keep in step.
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


def test_both_handles_are_there(page):
    assert page.locator('[data-resize="inbox"]').count() == 1
    assert page.locator('[data-resize="facts"]').count() == 1


def test_dragging_widens_the_inbox(page):
    try:
        before = _w(page, ".inbox")
        _drag(page, "inbox", 90)
        after = _w(page, ".inbox")
        assert after > before + 50, (
            f"the inbox did not move: {before} -> {after}. The handle renders "
            f"and is bound to nothing.")
    finally:
        _reset(page)


def test_dragging_narrows_it_too(page):
    try:
        _drag(page, "inbox", 80)
        wide = _w(page, ".inbox")
        _drag(page, "inbox", -80)
        assert _w(page, ".inbox") < wide - 40
    finally:
        _reset(page)


def test_the_facts_handle_moves_the_facts_column(page):
    try:
        before = _w(page, ".review-col")
        _drag(page, "facts", 100)
        assert _w(page, ".review-col") > before + 50
    finally:
        _reset(page)


def test_each_handle_moves_only_its_own_column(page):
    """Two handles that both resize the same thing is worse than one."""
    try:
        inbox_before = _w(page, ".inbox")
        _drag(page, "facts", 100)
        assert abs(_w(page, ".inbox") - inbox_before) < 5, \
            "the facts handle moved the inbox"
    finally:
        _reset(page)


def test_the_rca_column_takes_the_remainder(page):
    """It has no width of its own. If it stops absorbing the change, widening
    a column pushes content off screen instead of reflowing."""
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
    """Dragged past its minimum a column would vanish, and there would be no
    handle left to drag it back with."""
    try:
        _drag(page, "inbox", -900)
        assert _w(page, ".inbox") >= 195, \
            "the inbox collapsed past its minimum"
        _drag(page, "inbox", 2000)
        assert _w(page, ".inbox") <= 565, \
            "the inbox grew past its maximum and squeezed out the RCA"
    finally:
        _reset(page)


def test_the_width_survives_a_reload(page):
    """A resize that resets on every load is one nobody uses twice."""
    try:
        _drag(page, "inbox", 90)
        want = _w(page, ".inbox")
        page.reload(wait_until="load")
        page.wait_for_timeout(900)
        got = _w(page, ".inbox")
        assert abs(got - want) < 5, f"width was {want}, came back {got}"
    finally:
        _reset(page)
        page.reload(wait_until="load")
        page.wait_for_selector(".review-item", timeout=15000)
        page.locator(".review-item").first.click()
        page.wait_for_timeout(1200)


def test_arrow_keys_resize_it_too(page):
    """A drag-only control is unusable for anyone who cannot drag, and the
    handles are already focusable."""
    try:
        before = _w(page, ".inbox")
        page.focus('[data-resize="inbox"]')
        for _ in range(4):
            page.keyboard.press("ArrowRight")
        page.wait_for_timeout(200)
        assert _w(page, ".inbox") > before, "arrow keys do nothing"
    finally:
        _reset(page)


def test_double_click_restores_the_default(page):
    try:
        _drag(page, "inbox", 120)
        assert _w(page, ".inbox") > 380
        page.dblclick('[data-resize="inbox"]')
        page.wait_for_timeout(250)
        assert abs(_w(page, ".inbox") - 300) < 5, \
            "double-click did not restore the stylesheet default"
    finally:
        _reset(page)


def test_the_handle_is_wide_enough_to_hit(page):
    """A 1px target is a target people miss."""
    box = page.locator('[data-resize="inbox"]').bounding_box()
    assert box["width"] >= 4, f"the hit area is {box['width']}px"


def test_resizing_does_not_break_the_page(page):
    try:
        _drag(page, "inbox", 60)
        _drag(page, "facts", -60)
        assert page.errors == [], page.errors
        assert page.locator("#rca-col .section").count() > 0, \
            "the RCA column stopped rendering after a resize"
    finally:
        _reset(page)
