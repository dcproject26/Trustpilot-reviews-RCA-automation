"""A confirmed booking whose record could not be read says so on the panel.

`select_candidate` stores the candidate's own fields when the warehouse lookup
returns nothing or does not complete. Those fields are largely empty on the
shortlist path, so the Booking details panel renders a column of em-dashes —
which reads as a booking with no details rather than as a booking we could not
fetch. The two are opposite: the first is a fact about the trip, the second is
a fact about our run.

The trail carries the same news, but the panel is where the reader is looking
when they notice the dashes, so the sentence has to be there too.

CLIENT-SIDE JAVASCRIPT with no harness of its own — CLAUDE.md's stated
exception. These drive the real render through the browser rather than reading
the source, so an unreachable branch cannot pass them.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _panel(page, details_lookup):
    """Render the facts column with a booking in the given lookup state."""
    return page.evaluate("""(lookup) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__bKeep === undefined)
        window.__bKeep = {t: r.type, b: JSON.parse(JSON.stringify(r.booking || {}))};
      r.type = 'identified';
      r.booking = Object.assign({}, r.booking || {}, {
        bid: '32885089', detailsLookup: lookup || undefined});
      renderReviewCol();
      const el = document.querySelector('[data-booking-lookup]');
      return {note: el ? el.textContent.trim() : '',
              panel: !!document.querySelector('.details-grid')};
    }""", details_lookup)


def _restore(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__bKeep) {
        r.type = window.__bKeep.t; r.booking = window.__bKeep.b;
        window.__bKeep = undefined;
      }
      renderReviewCol(); }""")


def test_a_booking_the_warehouse_lacks_says_the_fields_are_from_the_ticket(page):
    try:
        got = _panel(page, "absent")
        assert got["panel"], "the details panel did not render at all"
        assert "from the Zendesk ticket" in got["note"], got
        assert "no booking with this id" in got["note"], got
    finally:
        _restore(page)


def test_a_lookup_that_did_not_complete_says_to_re_run(page):
    """The opposite response to the one above: the booking may well be there
    and nothing about it was ruled out."""
    try:
        got = _panel(page, "failed")
        assert "did not complete" in got["note"], got
        assert "Re-run" in got["note"], got
        assert "no booking with this id" not in got["note"], got
    finally:
        _restore(page)


def test_the_two_reasons_do_not_share_a_sentence(page):
    try:
        a = _panel(page, "absent")["note"]
        b = _panel(page, "failed")["note"]
        assert a and b and a != b, (a, b)
    finally:
        _restore(page)


def test_a_booking_that_WAS_read_carries_no_warning(page):
    """A note on every healthy booking is the noise that makes a reader stop
    reading the ones that mean something."""
    try:
        assert _panel(page, "found")["note"] == ""
    finally:
        _restore(page)


def test_a_booking_with_no_recorded_answer_carries_no_warning(page):
    """Drafts written before the field existed. Saying "not in the warehouse"
    about a booking nobody recorded an answer for is the inverse of the bug."""
    try:
        assert _panel(page, None)["note"] == ""
    finally:
        _restore(page)


def test_the_panel_still_renders_its_fields_either_way(page):
    """The note is added TO the panel, not instead of it. The ticket's own
    values are all the reader has and must still be on screen."""
    try:
        for lookup in ("absent", "failed", "found"):
            assert _panel(page, lookup)["panel"], lookup
    finally:
        _restore(page)


def test_the_booking_remap_does_not_drop_the_lookup_answer():
    """A SOURCE ASSERTION, and CLAUDE.md's stated exception: client-side
    JavaScript with no harness that can reach it.

    The remap that turns a draft's `booking` into `r.booking` names each field
    explicitly. A field it does not name never reaches the panel, so the
    branch above cannot fire however correct it is — and a mutation deleting
    this one line survived every browser test in this file, because they set
    `r.booking.detailsLookup` directly and never go through the remap."""
    src = open("client/index.html", encoding="utf-8").read()
    assert "r.booking.detailsLookup    = db.details_lookup" in src, (
        "the booking remap drops details_lookup, so a booking whose record "
        "could not be read renders as a booking with no details")
