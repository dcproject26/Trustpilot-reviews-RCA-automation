"""Three client bugs from the reviews→RCA bug review, driven in a browser.

  1. A just-added review is still PROCESSING, but the post-add tab switch had no
     processing branch and fell through to Matched — where a processing review
     does not render, so it appeared to vanish.
  2. Flag-to-Biz read a phantom camelCase key (vidCompletionRate) for the
     completion rate, so the flag field showed blank even when the real
     vid_completion_rate (a 0–1 fraction) was set. (The on-card completion tile
     was NOT affected — it renders from _rate(li.vid_completion_rate) in the
     live branch; the phantom read sits in a fallback arm where liveInsights is
     falsy and is never reached.)
  3. The Flag-to-Biz message labelled the numbers with the global picker window
     (state.insightsWindow) instead of the window the data was computed for — a
     supply flag telling Biz the wrong scope, with no stale-window warning to
     catch it the way the insights panel has.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def test_the_new_review_tab_mapping_includes_processing(page):
    """Bug 1. Each bucket maps to its own tab; processing is no longer dropped
    into Matched, and an unexpected type still falls back to identified."""
    got = page.evaluate(
        "() => [_tabForNewReview('processing'), _tabForNewReview('candidates'),"
        " _tabForNewReview('untraceable'), _tabForNewReview('identified'),"
        " _tabForNewReview('sent')]")
    assert got == ["processing", "candidates", "untraceable",
                   "identified", "identified"], got


def test_the_flag_modal_completion_field_is_populated_from_the_real_key(page):
    """Bug 2 (flag field). The field an associate flags from must carry the real
    rate as a percentage, not sit blank."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.liveInsights;
      r.liveInsights = Object.assign({}, keep || {}, {vid_completion_rate: 0.571});
      openFlagDraftModal(r);
      const comp = (document.querySelector('#flag-completion') || {}).value;
      const m = document.getElementById('flag-modal'); if (m) m.remove();
      r.liveInsights = keep;
      return comp; }""")
    assert got not in (None, ""), "the completion field was blank"
    assert abs(float(got) - 57.1) < 0.2, f"completion field: {got!r}"


def test_the_flag_message_uses_the_data_window_not_the_picker(page):
    """Bug 3. The message states the window the numbers are FOR, so a picker set
    to a different window cannot mislabel a leadership flag."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keepRaw = r.insightsRaw, keepWin = state.insightsWindow;
      r.insightsRaw = Object.assign({}, keepRaw || {}, {_window_days: 30});
      state.insightsWindow = '90d';
      openFlagDraftModal(r);
      const msg = (document.querySelector('#flag-message') || {}).value;
      const m = document.getElementById('flag-modal'); if (m) m.remove();
      r.insightsRaw = keepRaw; state.insightsWindow = keepWin;
      return msg; }""")
    assert "Window: 30d" in got, f"message did not use the data window: {got!r}"
    assert "Window: 90d" not in got, f"message used the picker window: {got!r}"
