"""What the card draws for the indicator check, and where Close out appears.

Both are client behaviour, so both are driven in a real browser rather than
asserted against the file. The content-family warning lost a mutation to
exactly this gap: every test for it was server-side, so a mutation loosening
`if (state !== 'mismatch')` to `if (!cm)` survived — and that mutation puts an
amber warning on nearly every review, since "unchecked" is the common answer.
A warning that appears on everything is one people stop reading.

Close out is tested here for the opposite reason: the endpoint works (see
tests/test_route_to_sent.py) and the whole reported problem was that no button
reached it. A test of the endpoint alone would have passed against the build
the user was complaining about.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401

MISMATCH = {"state": "mismatch",
            "signals": [{"name": "city", "state": "mismatch", "review": "Paris",
                         "booking": "Rome",
                         "why": "the review is about Paris; this booking is in Rome"},
                        {"name": "guest", "state": "match", "review": "A",
                         "booking": "A", "why": "the names agree (1.0)"}],
            "contradictions": ["city"], "agreements": ["guest"], "checked": 2,
            "why": "the review is about Paris; this booking is in Rome"}


def _set_im(page, im):
    return page.evaluate("""(im) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__imKeep === undefined)
        window.__imKeep = r.indicatorMatch === undefined ? null : r.indicatorMatch;
      r.indicatorMatch = im;
      renderReviewCol();
      const els = [...document.querySelectorAll('.content-mismatch')];
      return els.map(e => e.textContent.replace(/\\s+/g, ' ').trim());
    }""", im)


def _restore_im(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r.indicatorMatch = window.__imKeep;
      renderReviewCol();
    }""")


def test_a_contradiction_is_drawn_on_the_match_card(page):
    try:
        rows = _set_im(page, MISMATCH)
        hit = [t for t in rows if "does not match what the review says" in t]
        assert hit, f"no indicator warning rendered; rows={rows}"
        assert "Paris" in hit[0] and "Rome" in hit[0], hit[0]
    finally:
        _restore_im(page)


def test_the_warning_says_the_match_was_not_undone(page):
    """It never unmatches. A warning that reads as an automatic un-match sends
    the associate looking for a booking that is still attached."""
    try:
        rows = _set_im(page, MISMATCH)
        hit = next(t for t in rows if "does not match what the review says" in t)
        assert "has NOT been undone" in hit
    finally:
        _restore_im(page)


@pytest.mark.parametrize("state", ["match", "unchecked"])
def test_the_other_two_states_draw_nothing(page, state):
    """"unchecked" is the common case. Drawing it would put a warning on
    nearly every card; the pipeline writes both states onto the confidence
    trail instead, which is where a reader goes to see what ran."""
    try:
        rows = _set_im(page, {"state": state, "signals": [], "contradictions": [],
                              "agreements": [], "checked": 0, "why": "x"})
        assert not [t for t in rows if "does not match what the review says" in t], \
            f"the {state!r} state drew a warning"
    finally:
        _restore_im(page)


def test_no_result_at_all_draws_nothing(page):
    try:
        assert not [t for t in _set_im(page, None)
                    if "does not match what the review says" in t]
    finally:
        _restore_im(page)


def test_only_the_contradicting_signals_are_named(page):
    """The agreeing signals are on the trail. Listing them in the warning
    turns a one-line alert into a table nobody reads."""
    try:
        rows = _set_im(page, MISMATCH)
        hit = next(t for t in rows if "does not match what the review says" in t)
        assert "city" in hit
        assert "the names agree" not in hit
    finally:
        _restore_im(page)


# ── Close out reaches every bucket that had no route ───────────────────────

def _close_buttons(page, type_, extra="{}"):
    return page.evaluate("""([t, extra]) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (!window.__tKeep) window.__tKeep = {
        type: r.type, cs: r.candidateState, cl: r.candidatesList,
        ps: r.processingState};
      Object.assign(r, JSON.parse(extra));
      r.type = t;
      renderReviewCol(); renderRcaCol();
      return [...document.querySelectorAll('[data-close-out]')].length;
    }""", [type_, extra])


def _restore_type(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const k = window.__tKeep;
      if (k) { r.type = k.type; r.candidateState = k.cs;
               r.candidatesList = k.cl; r.processingState = k.ps; }
      renderReviewCol(); renderRcaCol();
    }""")


def test_the_untraceable_panel_offers_close_out(page):
    """Send ↑ is not on this screen at all — the RCA column is replaced by the
    ask-the-guest panel — so this review had no way to be finished."""
    try:
        assert _close_buttons(page, "untraceable") >= 1
    finally:
        _restore_type(page)


def test_the_candidate_picker_offers_close_out(page):
    try:
        n = _close_buttons(page, "candidates", """{"candidateState": true,
          "candidatesList": [{"bid": "1", "experience": "X", "matchReasons": []}]}""")
        assert n >= 1
    finally:
        _restore_type(page)


def test_a_review_with_no_draft_offers_close_out(page):
    """It used to fall through to the full RCA layout, whose Send ↑ calls an
    endpoint that needs a draft — the one bucket guaranteed not to have one."""
    try:
        assert _close_buttons(page, "processing",
                              '{"processingState": "stalled"}') >= 1
    finally:
        _restore_type(page)


def test_the_processing_gate_does_not_offer_send(page):
    """Offering a button that can only 404 is worse than offering none."""
    try:
        _close_buttons(page, "processing", '{"processingState": "stalled"}')
        assert page.evaluate(
            "() => document.querySelectorAll('#rca-col .send-btn').length") == 0
    finally:
        _restore_type(page)


def test_close_out_takes_two_clicks(page):
    """It moves the card out of every working tab. A one-shot button means a
    mis-click quietly removes a review from the queue."""
    try:
        _close_buttons(page, "untraceable")
        got = page.evaluate("""async () => {
          const btn = document.querySelector('[data-close-out]');
          let posted = 0;
          const real = window.fetch.bind(window);
          window.fetch = (u, o) => {
            if (String(u).includes('/close')) { posted++;
              return Promise.resolve(new Response(JSON.stringify({ok: true,
                reason: 'r'}), {status: 200})); }
            return real(u, o);
          };
          btn.click();
          await new Promise(r => setTimeout(r, 150));
          const afterOne = {posted, text: btn.textContent};
          window.fetch = real;
          return afterOne;
        }""")
        assert got["posted"] == 0, "one click closed the review"
        assert "again" in got["text"].lower(), (
            f"the button gave no sign it was armed: {got['text']!r}")
    finally:
        _restore_type(page)
