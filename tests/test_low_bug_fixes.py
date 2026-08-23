"""Two low-severity client bugs from the reviews→RCA review, driven in a browser.

  * The "Similar complaints" tile (rendered only when live insights are absent)
    always printed "No similar complaints found in window" — a definitive result
    for a search that never ran, because the lists it reads come from the empty
    placeholder view-model. Found-nothing must not read as did-not-run (rule 1).
  * Changing "Raised with SP" on a LEGACY draft (records stored under
    sp_interaction, before the sp_interaction_notes split) created a fresh empty
    sp_interaction_notes that shadowed the legacy object on the next render,
    hiding its records. The select must migrate the legacy object first, the way
    the +Add SP record handler already does.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def test_the_similar_tile_says_not_computed_when_insights_did_not_load(page):
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.liveInsights;
      r.liveInsights = null;                 // the no-live-insights fallback branch
      renderReviewCol();
      const tile = [...document.querySelectorAll('#review-col .insight-tile')]
        .find(t => ((t.querySelector('.insight-tile-label') || {}).textContent || '')
                     .startsWith('Similar complaints'));
      const line = tile ? (tile.querySelector('.similar-summary-line') || {}).textContent : null;
      r.liveInsights = keep; renderReviewCol();
      return line; }""")
    assert got, "the Similar complaints tile did not render in the fallback branch"
    assert "Not computed" in got, f"the tile still claims a result: {got!r}"
    assert "No similar complaints found" not in got, got


def test_changing_raised_with_sp_on_a_legacy_draft_keeps_its_records(page):
    """The migration is synchronous at the top of the handler; fetch is stubbed
    so the persist does not touch the server, and rca.v3 is restored after."""
    got = page.evaluate("""async () => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const orig = JSON.stringify(r.rca.v3 || {});
      const origFetch = window.fetch;
      window.fetch = async () => ({ok: true, json: async () => ({draft: {}})});
      try {
        r.rca.v3 = {sp_interaction: {raised: 'No',
          records: [{summary: 'KEEP-ME-SP', time: '', zd_ref: ''}]}};
        renderRcaCol();
        const sel = document.querySelector('select[data-v3sel="sp_interaction_notes.raised"]');
        if (!sel) return {error: 'the Raised with SP select did not render'};
        sel.value = 'Yes';
        sel.dispatchEvent(new Event('change', {bubbles: true}));
        await new Promise(res => setTimeout(res, 60));
        const notes = r.rca.v3.sp_interaction_notes || {};
        return {records: (notes.records || []).map(x => x.summary), raised: notes.raised};
      } finally {
        window.fetch = origFetch;
        r.rca.v3 = JSON.parse(orig);
        renderRcaCol();
      }
    }""")
    assert not got.get("error"), got["error"]
    assert got["records"] == ["KEEP-ME-SP"], \
        f"the legacy SP records were lost when Raised with SP changed: {got}"
    assert got["raised"] == "Yes", f"the raised value was not written: {got}"


def test_the_booking_details_has_no_escalation_email_row(page):
    """Escalation email was removed from the Booking details field by request.
    Driven as an absence in the rendered DOM (not a source grep) so a build that
    brings the row back fails here."""
    n = page.evaluate("""() => [...document.querySelectorAll('#review-col .detail-row .k')]
        .filter(k => k.textContent.trim() === 'Escalation email').length""")
    assert n == 0, "the Escalation email row is still rendered in Booking details"
