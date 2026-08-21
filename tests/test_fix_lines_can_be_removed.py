"""Fix and Closes get the same × as every other line in the block.

Operational failure and SOP gap render as add-chips with × removers. Fix and
Closes rendered as plain rows with no control at all, so a fix the model
invented could be edited into different words but never taken off the card.

Clearing the text is NOT the same as removing the row: it leaves
`{action: null, because: null}` in storage, which renders as nothing while the
stored draft still carries a `fix` object. The next reader then cannot tell
"this issue has no fix" from "this issue's fix was cleared", and `_fix_rows`
already treats those as the same thing on the way in. So the × drops the whole
object once nothing is left in it.

Driven through the real card, because a delete control that renders and saves
nothing is indistinguishable from one that works until the page is reloaded.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _seed_fix(page, fix):
    """Write an issue carrying `fix` through the real endpoint, and reload."""
    page.evaluate("""async (fix) => {
        const cur = (await (await fetch('/api/reviews/tp_ui')).json()).draft;
        const v3  = Object.assign({}, cur.rca_v3 || {});
        v3.what_went_wrong = Object.assign({}, v3.what_went_wrong || {}, {
          guest_issues: [{issue: 'Tickets arrived late', claim: 'no tickets',
                          claim_accuracy: 'Accurate', root_cause: 'vendor delay',
                          fix: fix}]});
        await fetch('/api/reviews/tp_ui/draft-v2', {
            method: 'PATCH', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({rca_v3: v3})});
    }""", fix)
    page.reload(wait_until="load")
    page.wait_for_selector(".inbox-row", timeout=15000)
    page.locator(".inbox-row").first.click()
    page.wait_for_selector("#rca-casefindings-section", timeout=15000)


def _stored_fix(page):
    return page.evaluate("""async () => {
        const cur = (await (await fetch('/api/reviews/tp_ui')).json()).draft;
        const gi = ((cur.rca_v3 || {}).what_went_wrong || {}).guest_issues || [];
        return gi.length ? (gi[0].fix === undefined ? null : gi[0].fix) : null;
    }""")


def _del_buttons(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('[data-fix-del]')]"
        ".map(b => b.dataset.fixDel)")


FIX = {"action": "Re-issue the tickets", "because": "The guest never got them"}


def test_both_lines_render_a_remover(page):
    _seed_fix(page, FIX)
    assert sorted(_del_buttons(page)) == ["0:action", "0:because"], _del_buttons(page)


def test_removing_one_line_reaches_the_server(page):
    """The failure this guards: the × renders, the row disappears from the
    DOM, and a reload brings it back because nothing was saved."""
    _seed_fix(page, FIX)
    page.click('[data-fix-del="0:because"]')
    page.wait_for_timeout(800)
    got = _stored_fix(page)
    assert got and not got.get("because"), got
    assert got.get("action") == "Re-issue the tickets", \
        f"removing Closes took the Fix with it: {got}"


def test_the_other_line_still_renders_after_one_is_removed(page):
    _seed_fix(page, FIX)
    page.click('[data-fix-del="0:because"]')
    page.wait_for_timeout(800)
    assert _del_buttons(page) == ["0:action"], _del_buttons(page)


def test_removing_the_last_line_drops_the_whole_fix(page):
    """NOT an emptied husk. `{action: null, because: null}` left in storage
    means `fix` being present says nothing about whether there is a fix, and
    `_fix_rows` already treats "no action" and "no fix" as the same thing when
    it migrates a pre-restructure draft."""
    _seed_fix(page, {"action": "Re-issue the tickets"})
    page.click('[data-fix-del="0:action"]')
    page.wait_for_timeout(800)
    assert _stored_fix(page) is None, _stored_fix(page)


def test_removing_the_last_line_takes_the_block_off_the_card(page):
    """The stored shape and the screen have to agree. A `fix` object left
    behind as `{action: null, because: null}` renders as nothing — the row
    filter drops both lines — so the card would look right while storage
    carried a husk, and the next reader could not tell "no fix" from "a fix
    whose text was cleared"."""
    _seed_fix(page, {"action": "Re-issue the tickets"})
    assert page.evaluate("() => document.querySelectorAll('.wwr-fix').length") == 1
    page.click('[data-fix-del="0:action"]')
    page.wait_for_timeout(800)
    assert page.evaluate("() => document.querySelectorAll('.wwr-fix').length") == 0
    assert _stored_fix(page) is None, _stored_fix(page)


def test_a_removed_line_stays_removed_across_a_reload(page):
    """The whole point of routing it through persistV3 rather than hiding the
    row. An edit that looks saved and is not is what this codebase punishes
    hardest."""
    _seed_fix(page, FIX)
    page.click('[data-fix-del="0:because"]')
    page.wait_for_timeout(800)
    page.reload(wait_until="load")
    page.wait_for_selector(".inbox-row", timeout=15000)
    page.locator(".inbox-row").first.click()
    page.wait_for_selector("#rca-casefindings-section", timeout=15000)
    assert _del_buttons(page) == ["0:action"], \
        f"the removed line came back on reload: {_del_buttons(page)}"


def test_an_issue_with_no_fix_renders_no_remover(page):
    """Nothing to remove, and a × on an absent row is a control that does
    nothing when pressed."""
    _seed_fix(page, None)
    assert _del_buttons(page) == [], _del_buttons(page)
