"""The two fixes, checked where the reader sees them.

Both were verified against the functions that produce the data. Neither had
been checked against the CARD, and this session has already had one fix that
worked everywhere except the render (`evRow`, no callers) and one diagnostic
that reported a working fix as broken because it rebuilt the path instead of
driving it.

So these drive the browser: the real sort, the real §1 template, with the
exact row shapes from the traces on booking 32885089.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


# ── the timeline order, through the client's own sort ─────────────────────
#
# From scripts/trace_shaping.py, the rows that sorted correctly plus the two
# bookends that did not:
#
#     unknown   Booking created   <-- NO READABLE TIME, sinks to the end
#     unknown   Review posted     <-- NO READABLE TIME, sinks to the end

TRACE_ROWS = [
    {"time": "21 Jul 15:28 IST", "label": "Booking intimation sent to the SP"},
    {"time": "01 Aug 21:56 IST", "label": "Reschedule automation failed"},
    {"time": "02 Aug 15:36 IST", "label": "Guest chat — pickup time conflict"},
    {"time": "03 Aug 12:45 IST", "label": "ORM escalation actioned"},
]


def _order(page, rows):
    """Sort through the client's own `_tlSortValue`, not a copy of it."""
    return page.evaluate("""(rows) => {
        const y = 2026;
        return rows
          .map((r, i) => [r, i, _tlSortValue(r, y)])
          .sort((A, B) => {
            const a = A[2], b = B[2];
            if (a === null && b === null) return A[1] - B[1];
            if (a === null) return 1;
            if (b === null) return -1;
            return a - b || A[1] - B[1];
          })
          .map(([r]) => r.label); }""", rows)


def test_an_unstamped_bookend_sinks_which_is_the_reported_bug(page):
    """The behaviour that produced the screenshot. Kept as a test because the
    fix is a STAMP upstream — the sort is working as designed, and a change
    here would be fixing the wrong thing."""
    rows = [{"time": "unknown", "label": "Booking created"}] + TRACE_ROWS
    assert _order(page, rows)[-1] == "Booking created", _order(page, rows)


def test_a_stamped_bookend_sorts_to_the_top(page):
    """What `_shape_via_claude` now produces: the bookend carries the
    booking's own creation time instead of "unknown"."""
    rows = [{"time": "21 Jul 15:00 IST", "label": "Booking created"}] + TRACE_ROWS
    assert _order(page, rows)[0] == "Booking created", _order(page, rows)


def test_a_stamped_review_bookend_sorts_to_the_bottom(page):
    rows = TRACE_ROWS + [{"time": "05 Aug 04:56 IST", "label": "Review posted"}]
    assert _order(page, rows)[-1] == "Review posted", _order(page, rows)


def test_the_whole_traced_timeline_comes_out_in_order(page):
    """Both bookends stamped, every row from the trace, one assertion."""
    rows = ([{"time": "21 Jul 15:00 IST", "label": "Booking created"}]
            + TRACE_ROWS
            + [{"time": "05 Aug 04:56 IST", "label": "Review posted"}])
    got = _order(page, rows)
    assert got == ["Booking created",
                   "Booking intimation sent to the SP",
                   "Reschedule automation failed",
                   "Guest chat — pickup time conflict",
                   "ORM escalation actioned",
                   "Review posted"], got


# ── §1 after the fold, through the real template ──────────────────────────

def _seed_findings(page, findings, issues=None):
    page.evaluate("""async ([cf, gi]) => {
        const cur = (await (await fetch('/api/reviews/tp_ui')).json()).draft;
        const v3  = Object.assign({}, cur.rca_v3 || {});
        v3.what_went_wrong = Object.assign({}, v3.what_went_wrong || {},
                                           {case_findings: cf, guest_issues: gi || []});
        await fetch('/api/reviews/tp_ui/draft-v2', {
            method: 'PATCH', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({rca_v3: v3})});
    }""", [findings, issues or []])
    page.reload(wait_until="load")
    page.wait_for_selector(".review-item", timeout=15000)
    page.locator(".review-item").first.click()
    page.wait_for_selector("#rca-casefindings-section", timeout=15000)


FOLDED = [{"text": "Agent confirmed the rescheduling window had closed and no "
                   "time change was possible",
           "source": "zendesk", "ref": "ZD-34335318"}]


def test_the_folded_finding_renders_once(page):
    """The fold puts the evidence row's ref onto the narrative row instead of
    writing a second row. One row on the card is the visible half of that."""
    _seed_findings(page, FOLDED)
    n = page.evaluate("() => document.querySelectorAll("
                      "'#rca-casefindings-section .cf-row').length")
    assert n == 1, n


def test_the_folded_ticket_ref_renders_as_a_link(page):
    """The whole reason to fold rather than drop: the evidence row carried the
    ZD ref and the narrative row did not. If it does not reach the card, the
    fold has thrown away the only thing that made it worth keeping."""
    _seed_findings(page, FOLDED)
    got = page.evaluate("""() => {
        const a = document.querySelector('#rca-casefindings-section a.cf-ref');
        return a ? {text: a.textContent.trim(), href: a.getAttribute('href')} : null; }""")
    assert got, "the ZD reference did not render"
    assert "34335318" in got["text"], got
    assert got["href"].startswith("http"), got


def test_every_rendered_finding_can_be_deleted(page):
    """The format asked for: deletable pointers. A folded row is still a row
    somebody can remove."""
    _seed_findings(page, FOLDED)
    got = page.evaluate("""() => ({
        rows: document.querySelectorAll('#rca-casefindings-section .cf-row').length,
        dels: document.querySelectorAll('#rca-casefindings-section [data-cf-del]').length})""")
    assert got["rows"] == got["dels"] and got["rows"] > 0, got


def test_no_clock_time_renders_in_the_findings(page):
    """§1 is the reading of the case; the events timeline is the record with
    the clock on it."""
    _seed_findings(page, FOLDED)
    assert page.evaluate("() => document.querySelectorAll("
                         "'#rca-casefindings-section .cf-time').length") == 0
