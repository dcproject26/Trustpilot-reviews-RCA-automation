"""Bulk selection and close from the inbox.

Checkboxes on inbox rows, a select-all in the header, a floating action bar
that calls POST /api/reviews/{id}/close for each selected review. Driven
with Playwright because the feature lives entirely in client JS and source
assertions cannot tell a wired checkbox from a dead one (CLAUDE.md §2).
"""
import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _on_the_inbox(page):
    page.evaluate("() => { state.screen = 'inbox'; state.selectedIds.clear(); applyScreen(); renderInbox(); }")
    page.wait_for_selector("#inbox-list .inbox-row", state="visible", timeout=15000)
    yield
    page.evaluate("() => { state.selectedIds.clear(); renderInbox(); }")


def test_every_inbox_row_has_a_checkbox(page):
    """Each row must carry a checkbox so the user can select it."""
    rows = page.evaluate(
        "() => document.querySelectorAll('#inbox-list .inbox-row').length")
    cbs = page.evaluate(
        "() => document.querySelectorAll('#inbox-list .inbox-cb[data-sel-id]').length")
    assert rows > 0, "no inbox rows rendered"
    assert cbs == rows, f"{rows} rows but {cbs} checkboxes"


def test_select_all_checkbox_exists_in_header(page):
    assert page.locator("#inbox-select-all").count() == 1


def test_clicking_a_checkbox_adds_to_selectedIds(page):
    page.evaluate("() => state.selectedIds.clear()")
    rid = page.evaluate(
        "() => document.querySelector('#inbox-list .inbox-cb[data-sel-id]').dataset.selId")
    page.locator("#inbox-list .inbox-cb[data-sel-id]").first.click()
    page.wait_for_timeout(200)
    got = page.evaluate("() => [...state.selectedIds]")
    assert rid in got, f"expected {rid} in selectedIds, got {got}"


def test_unchecking_removes_from_selectedIds(page):
    page.locator("#inbox-list .inbox-cb[data-sel-id]").first.click()
    page.wait_for_timeout(100)
    page.locator("#inbox-list .inbox-cb[data-sel-id]").first.click()
    page.wait_for_timeout(100)
    got = page.evaluate("() => state.selectedIds.size")
    assert got == 0, f"selectedIds still has {got} entries"


def test_bulk_action_bar_shows_when_selected(page):
    assert page.locator("#bulk-action-bar").is_hidden()
    page.locator("#inbox-list .inbox-cb[data-sel-id]").first.click()
    page.wait_for_timeout(200)
    assert page.locator("#bulk-action-bar").is_visible()
    assert "1 selected" in page.locator("#bulk-action-count").text_content()


def test_deselect_all_clears_selection(page):
    page.locator("#inbox-list .inbox-cb[data-sel-id]").first.click()
    page.wait_for_timeout(200)
    assert page.locator("#bulk-action-bar").is_visible()
    page.click("#bulk-deselect")
    page.wait_for_timeout(200)
    assert page.locator("#bulk-action-bar").is_hidden()
    got = page.evaluate("() => state.selectedIds.size")
    assert got == 0


def test_select_all_checks_every_visible_row(page):
    page.click("#inbox-select-all")
    page.wait_for_timeout(200)
    total = page.evaluate(
        "() => document.querySelectorAll('#inbox-list .inbox-cb[data-sel-id]').length")
    sel = page.evaluate("() => state.selectedIds.size")
    assert sel == total and sel > 0, f"select-all got {sel} of {total}"
    all_checked = page.evaluate("""() => [...document.querySelectorAll(
        '#inbox-list .inbox-cb[data-sel-id]')].every(c => c.checked)""")
    assert all_checked, "not every row checkbox is checked"


def test_select_all_then_uncheck_clears_all(page):
    page.click("#inbox-select-all")
    page.wait_for_timeout(100)
    page.click("#inbox-select-all")
    page.wait_for_timeout(200)
    sel = page.evaluate("() => state.selectedIds.size")
    assert sel == 0


def test_bulk_close_calls_close_endpoint(page):
    """Click the close button and verify it posts to /api/reviews/{id}/close
    for each selected review. Routes are intercepted so no real server call
    is made — the behaviour under test is that the client issues the right
    requests, not that the server processes them."""
    closed = []
    page.evaluate("() => { window.__bulkSave = JSON.stringify(REVIEWS); }")
    page.route("**/api/reviews/*/close", lambda route: (
        closed.append(route.request.url),
        route.fulfill(status=200, content_type="application/json",
                      body='{"ok": true}')))
    page.route("**/api/reviews?*", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=page.evaluate("() => window.__bulkSave")))
    page.route("**/api/reviews/*/draft-v2", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body='{"drafts":[]}'))
    try:
        page.locator("#inbox-list .inbox-cb[data-sel-id]").first.click()
        page.wait_for_timeout(200)
        page.click("#bulk-close-btn")
        page.wait_for_timeout(2000)
    finally:
        page.unroute("**/api/reviews/*/close")
        page.unroute("**/api/reviews?*")
        page.unroute("**/api/reviews/*/draft-v2")
        page.evaluate("() => { REVIEWS = JSON.parse(window.__bulkSave); renderInbox(); }")
    assert len(closed) == 1, f"expected 1 close call, got {len(closed)}"
    assert "/close" in closed[0]


def test_clicking_row_outside_checkbox_still_opens_case(page):
    """The checkbox intercepts its own click; clicking anywhere else on the
    row still navigates to the case screen as before."""
    page.locator("#inbox-list .inbox-row .ic-guest").first.click()
    page.wait_for_timeout(300)
    assert page.evaluate("() => state.screen") == "case"
