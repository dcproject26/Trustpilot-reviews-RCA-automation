"""The inbox search, typed into.

A search box that only looked at the author would answer "no such review" to
somebody holding a ticket number — the same sentence as "it is not here", and
a completely different fact. So it searches everything a reader might have to
hand: name, booking id, ZD number, review text, classification, review id.

The other half is where a search goes wrong quietly: matches in a tab you are
not looking at. "No reviews in this tab" reads as "this review does not
exist", so the empty state has to tell the three cases apart — nothing typed,
nothing anywhere, and nothing HERE but some over there.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _search(page, q):
    page.fill("#inbox-search", q)
    page.wait_for_timeout(350)


def _shown(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('#inbox-list .review-item')].length")


def _empty_text(page):
    return page.evaluate(
        "() => { const e = document.querySelector('#inbox-list .empty-state');"
        "return e ? e.textContent.replace(/\\s+/g, ' ').trim() : null; }")


def test_the_box_is_there(page):
    assert page.locator("#inbox-search").count() == 1
    assert page.locator("#inbox-search").first.is_visible()


def test_an_empty_search_shows_everything(page):
    _search(page, "")
    assert _shown(page) == page.evaluate("() => REVIEWS.length")


def test_it_finds_a_review_by_the_guest_name(page):
    _search(page, "david")
    assert _shown(page) >= 1, _empty_text(page)


def test_it_finds_a_review_by_booking_id(page):
    _search(page, "32908218")
    assert _shown(page) >= 1, _empty_text(page)


def test_it_finds_a_review_by_ticket_number(page):
    """The case that motivated searching more than the author: an associate
    working from a Zendesk ticket has the number and nothing else."""
    _search(page, "34125496")
    assert _shown(page) >= 1, _empty_text(page)


def test_the_zd_prefix_is_optional(page):
    """Half the people searching will paste ZD-34125496 and half will paste
    34125496. The prefix is ours, not the ticket's."""
    _search(page, "ZD-34125496")
    with_prefix = _shown(page)
    _search(page, "34125496")
    assert with_prefix == _shown(page) >= 1


def test_it_finds_a_review_by_words_in_the_body(page):
    _search(page, "late tickets")
    assert _shown(page) >= 1, _empty_text(page)


def test_it_finds_a_review_by_classification(page):
    _search(page, "Ticket Issues")
    assert _shown(page) >= 1, _empty_text(page)


def test_terms_narrow_rather_than_widen(page):
    """AND across terms. Two words that each match a different review must
    return neither, or the search gets less useful the more you type."""
    _search(page, "david")
    one = _shown(page)
    _search(page, "david zzzznotpresent")
    assert _shown(page) == 0, f"adding a term widened the result from {one}"


def test_nothing_anywhere_says_so_and_says_what_it_searched(page):
    _search(page, "zzzznotpresentanywhere")
    txt = _empty_text(page)
    assert "any tab" in txt, txt
    assert "ticket number" in txt, \
        "it does not say what was searched, so 'no match' is unfalsifiable"


def test_a_match_in_another_tab_is_not_reported_as_no_such_review(page):
    """The bug this guards. The review is one tab across and the old empty
    state said "No reviews in this tab", which reads as "it does not exist"."""
    page.click('.inbox-tab[data-tab="sent"]')
    page.wait_for_timeout(300)
    _search(page, "david")
    if _shown(page):
        pytest.skip("the fixture review is in the Sent tab; nothing to prove")
    txt = _empty_text(page)
    assert "in this tab" in txt, txt
    assert page.locator("[data-goto-tab]").count() >= 1, \
        "it says there are matches elsewhere but offers no way to reach them"


def test_the_elsewhere_button_moves_you_there(page):
    page.click('.inbox-tab[data-tab="sent"]')
    page.wait_for_timeout(300)
    _search(page, "david")
    if not page.locator("[data-goto-tab]").count():
        pytest.skip("no cross-tab matches in this fixture")
    page.locator("[data-goto-tab]").first.click()
    page.wait_for_timeout(400)
    assert _shown(page) >= 1, "the jump landed on an empty tab"


def test_the_tab_counts_follow_the_search(page):
    """A tab reading 12 above an empty list reads as a broken list. While
    searching, the numbers count matches — which is also the answer to the
    question a search is asking."""
    page.click('.inbox-tab[data-tab="all"]')
    page.wait_for_timeout(300)
    _search(page, "")
    total = page.evaluate("() => +document.getElementById('cnt-all').textContent")
    _search(page, "zzzznotpresentanywhere")
    assert page.evaluate(
        "() => +document.getElementById('cnt-all').textContent") == 0, \
        f"the All tab still claims {total} while nothing matches"


def test_clearing_restores_everything(page):
    _search(page, "zzzznotpresentanywhere")
    page.click("#inbox-search-clear")
    page.wait_for_timeout(350)
    assert _shown(page) == page.evaluate("() => REVIEWS.length")
    assert page.evaluate("() => state.search") == ""


def test_escape_clears_it_too(page):
    _search(page, "david")
    page.focus("#inbox-search")
    page.keyboard.press("Escape")
    page.wait_for_timeout(350)
    assert page.evaluate("() => state.search") == ""


def test_the_clear_button_is_hidden_until_there_is_something_to_clear(page):
    _search(page, "")
    assert not page.locator("#inbox-search-clear").is_visible()
    _search(page, "david")
    assert page.locator("#inbox-search-clear").is_visible()
