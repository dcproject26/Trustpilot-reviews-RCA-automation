"""The header chip says whether the REVIEW carried a booking id.

It read the routing bucket:

    ${r.type === 'identified' ? ' · BID ' + esc(b.bid) : ' · no BID in text'}

so every review outside that bucket claimed no BID — including one whose id
was extracted from the review, verified in BigQuery, and named in the trail
two panels down. The card contradicted itself because two elements derived one
fact from different stores.

AND THE FIRST FIX WAS WRONG IN A WAY ONLY A BROWSER COULD SHOW. Switching the
chip to `_bidFromReview(r)` looked right — that helper is the store, reading
`bid_source` and `reference_number`. But it is called elsewhere on the RAW api
row (`_matchTitle`), and the card's view-model carried neither field, so it
answered false for every review and the chip said "no BID in text" on all of
them. Worse than the bug it replaced, and invisible to any test that did not
render the page.

Hence this file: it drives the real client with a real row and reads the text
that ends up on screen.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _chip(page, **over):
    """Render the review column with these row fields and return the chip.

    The row is restored afterwards: `page` is module-scoped, and a test that
    leaves the selected review mutated is the flake this suite has already
    paid for once.
    """
    return page.evaluate("""(o) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = {t: r.type, s: r.bid_source, n: r.reference_number,
                    b: r.booking && r.booking.bid};
      Object.assign(r, o.row);
      if (o.bid !== undefined) { r.booking = r.booking || {}; r.booking.bid = o.bid; }
      renderReviewCol();
      const el = document.querySelector('.review-meta');
      const txt = el ? el.textContent.trim() : null;
      r.type = keep.t; r.bid_source = keep.s; r.reference_number = keep.n;
      if (r.booking) r.booking.bid = keep.b;
      renderReviewCol();
      return txt; }""", over)


def test_the_view_model_carries_what_the_helper_reads(page):
    """The defect the first fix shipped. Without these two fields
    `_bidFromReview` cannot answer anything but false, however correct the
    chip's logic is."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      return {source: 'bid_source' in r, ref: 'reference_number' in r}; }""")
    assert got["source"], "the row does not carry bid_source"
    assert got["ref"], "the row does not carry reference_number"


def test_a_matched_review_shows_its_booking_id(page):
    got = _chip(page, row={"type": "identified", "bid_source": "regex",
                           "reference_number": "33204378"}, bid="33204378")
    assert "33204378" in got, got
    assert "no BID" not in got, got


def test_a_bid_in_the_text_that_is_not_confirmed_says_BOTH_things(page):
    """The reported case, and the state that had no sentence at all. "BID
    present" and "match confirmed" are different facts; the chip used to
    collapse them and answer the second while appearing to answer the first."""
    got = _chip(page, row={"type": "candidates", "bid_source": "regex",
                           "reference_number": "33204378"}, bid="33204378")
    assert "33204378" in got, got
    assert "not confirmed" in got, got


def test_a_review_with_no_booking_id_still_says_so(page):
    """The honest case must survive the fix — otherwise this trades one wrong
    label for another."""
    got = _chip(page, row={"type": "candidates", "bid_source": "",
                           "reference_number": ""}, bid=None)
    assert "no BID in text" in got, got


def test_the_three_states_are_distinguishable(page):
    """If any two produce the same text the chip has stopped carrying the
    fact it exists for."""
    seen = {
        _chip(page, row={"type": "identified", "bid_source": "regex",
                         "reference_number": "33204378"}, bid="33204378"),
        _chip(page, row={"type": "candidates", "bid_source": "regex",
                         "reference_number": "33204378"}, bid="33204378"),
        _chip(page, row={"type": "candidates", "bid_source": "",
                         "reference_number": ""}, bid=None),
    }
    assert len(seen) == 3, seen


def test_an_id_reaching_the_row_only_as_a_reference_number_still_counts(page):
    """Rows drafted before bid_source existed carry only reference_number,
    and a review added by hand carries only that too."""
    got = _chip(page, row={"type": "candidates", "bid_source": "",
                           "reference_number": "33204378"}, bid=None)
    assert "33204378" in got, got
    assert "no BID" not in got, got
