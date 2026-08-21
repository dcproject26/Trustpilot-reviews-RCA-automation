"""A review being processed is not a review that could not be matched.

"all the new reviews are getting populated in untraceable tab again after
clicking on refresh to slack."

They were not untraceable. The draft row is written at step 5b of the
pipeline, after BID extraction and the BigQuery search, so from the moment a
review is ingested until its run reaches that step it has no draft — and
server/tiers.py filed that as UNTRACEABLE. Queue fifteen reviews and all
fifteen appear in Untraceable at once, then drain out as their runs finish.

Rule one of this codebase, in the place it costs most: "still working" and
"we looked and found nothing" were the same tab, named after the second one,
coloured red, and every one of those reviews turned out to be matched.

These tests drive the real page, because the counts, the tab, the card title
and the stage chip are four separate renderings of the same fact and any one
of them can be left behind.
"""
import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


@pytest.fixture(autouse=True)
def _on_the_inbox(page):
    """The shared `page` fixture opens a review, which now moves to the case
    screen. These tests read and click the queue (tabs, search, rows), so step
    back to the inbox surface first."""
    page.evaluate("() => { state.screen = 'inbox'; applyScreen(); renderInbox(); }")
    page.wait_for_selector("#inbox-search", state="visible", timeout=15000)
    yield


def _seed(page, rows):
    """Put rows through the client's own ingest, so the bucket is derived the
    way it is in production rather than assigned by the test."""
    return page.evaluate("""(rows) => {
      window.__saveReviews = REVIEWS;
      REVIEWS = rows.map(r => ({
        ...r,
        rca: null, stars: '★', lang: 'EN', original: r.snippet || '',
      }));
      renderInbox();
      return REVIEWS.map(x => x.type);
    }""", rows)


def _restore(page):
    page.evaluate("""() => { if (window.__saveReviews) {
        REVIEWS = window.__saveReviews; delete window.__saveReviews;
        renderInbox(); } }""")


def _count(page, tab):
    return page.evaluate(
        "(t) => +document.getElementById('cnt-' + t).textContent", tab)


def test_the_processing_tab_exists(page):
    assert page.locator('.inbox-tab[data-tab="processing"]').count() == 1


def test_a_queued_review_is_not_counted_as_untraceable(page):
    """The reported bug, at the count."""
    try:
        _seed(page, [{"id": "tp_q", "author": "Queued", "status": "new",
                      "type": "processing", "bucket": "processing",
                      "processingState": "running"}])
        assert _count(page, "processing") == 1
        assert _count(page, "untraceable") == 0, (
            "a review that has not been searched is being counted as one we "
            "searched for and could not find")
    finally:
        _restore(page)


def test_a_review_that_was_searched_and_missed_is_still_untraceable(page):
    """The other half. A fix that empties the Untraceable tab entirely would
    hide real misses somewhere nobody treats as a problem."""
    try:
        _seed(page, [{"id": "tp_u", "author": "Missed", "status": "new",
                      "type": "untraceable", "bucket": "untraceable"}])
        assert _count(page, "untraceable") == 1
        assert _count(page, "processing") == 0
    finally:
        _restore(page)


def test_the_two_are_in_different_tabs(page):
    try:
        _seed(page, [
            {"id": "tp_q", "author": "Queued", "status": "new",
             "type": "processing", "bucket": "processing"},
            {"id": "tp_u", "author": "Missed", "status": "new",
             "type": "untraceable", "bucket": "untraceable"},
        ])
        assert (_count(page, "processing"), _count(page, "untraceable")) == (1, 1)
        assert _count(page, "all") == 2
    finally:
        _restore(page)


def test_a_processing_review_is_not_coloured_as_stuck(page):
    """Red means a human is blocked. A review the pipeline is still working on
    asks for nothing but patience, and it was being painted red because it was
    being filed as Untraceable."""
    try:
        _seed(page, [{"id": "tp_q", "author": "Queued", "status": "new",
                      "type": "processing", "bucket": "processing",
                      "processingState": "running"}])
        page.click('.inbox-tab[data-tab="processing"]')
        page.wait_for_timeout(300)
        chips = page.evaluate("""() => [...document.querySelectorAll(
          '#inbox-list .inbox-row')].map(i => {
            const c = i.querySelector('.stage-chip, [class*=stage]');
            return c ? {text: c.textContent.trim(), cls: c.className} : null;
          })""")
        assert chips and chips[0], f"no stage chip rendered: {chips}"
        assert "bad" not in chips[0]["cls"], (
            f"a review still being processed is marked as stuck: {chips[0]}")
        assert "Untraceable" not in chips[0]["text"], chips[0]
    finally:
        _restore(page)


def test_a_stalled_run_IS_marked_as_stuck(page):
    """The complement. A run that ended without writing a draft is a bug, and
    must not be soothed into looking like normal progress."""
    try:
        _seed(page, [{"id": "tp_s", "author": "Died", "status": "new",
                      "type": "processing", "bucket": "processing",
                      "processingState": "stalled"}])
        page.click('.inbox-tab[data-tab="processing"]')
        page.wait_for_timeout(300)
        chips = page.evaluate("""() => [...document.querySelectorAll(
          '#inbox-list .inbox-row')].map(i => {
            const c = i.querySelector('.stage-chip, [class*=stage]');
            return c ? {text: c.textContent.trim(), cls: c.className} : null;
          })""")
        assert chips and chips[0], f"no stage chip rendered: {chips}"
        assert chips[0]["text"] != "Processing", (
            "a run that died reads as one still going, so nobody re-runs it")
    finally:
        _restore(page)


def test_the_processing_states_do_not_read_the_same(page):
    got = page.evaluate("""() => {
      const mk = s => ({id: 'x', bucket: 'processing', processing_state: s,
                        unverified: false, match_tier: null,
                        candidate_state: false, status: 'new'});
      return [ _matchTitle(mk('running'), null, false),
               _matchTitle(mk('stalled'), null, false),
               _matchTitle(mk('queued'), null, false),
               _matchTitle({id:'y', bucket:'untraceable', status:'new'},
                           null, false) ];
    }""")
    assert len(set(got)) == 4, f"two of these four read the same: {got}"
    for t in got[:3]:
        assert "Untraceable" not in t, got


def test_a_queued_review_is_not_reported_as_running(page):
    """The thirteen stranded reviews. A review the runner has been handed and
    has not started is not one it is working on, and saying "Still running"
    over it is how thirteen dead reviews looked healthy for an afternoon."""
    got = page.evaluate("""() => _matchTitle(
        {id: 'x', bucket: 'processing', processing_state: 'queued',
         status: 'new'}, null, false)""")
    assert "Still running" not in got, got
    assert "ueued" in got, got


def test_a_queued_review_is_not_coloured_as_stuck(page):
    """Queued is normal. Red is for a human who is blocked."""
    try:
        _seed(page, [{"id": "tp_q2", "author": "Waiting", "status": "new",
                      "type": "processing", "bucket": "processing",
                      "processingState": "queued"}])
        page.click('.inbox-tab[data-tab="processing"]')
        page.wait_for_timeout(300)
        chips = page.evaluate("""() => [...document.querySelectorAll(
          '#inbox-list .inbox-row')].map(i => {
            const c = i.querySelector('.stage-chip, [class*=stage]');
            return c ? {text: c.textContent.trim(), cls: c.className} : null;
          })""")
        assert chips and chips[0], f"no stage chip rendered: {chips}"
        assert "bad" not in chips[0]["cls"], chips[0]
        assert chips[0]["text"] != "Run stopped", chips[0]
        assert chips[0]["text"] == "Queued", (
            f"a review that has not started reads as one being worked on: "
            f"{chips[0]}")
    finally:
        _restore(page)


def test_the_fallback_derivation_agrees_with_the_server(page):
    """A client from before the bucket field derives its own. Two rules that
    disagree is what put confirmed candidates in the wrong tab for weeks."""
    got = page.evaluate("""() => _bucketFallback(
        {status: 'new', has_draft: false, has_booking: false,
         has_candidates: false}, null, false)""")
    assert got == "processing", (
        f"the client fallback still says {got!r} for a review with no draft "
        f"row, so a page that predates the bucket field shows the old bug")


def test_the_search_can_find_a_processing_review(page):
    try:
        _seed(page, [{"id": "tp_q", "author": "Queued Person", "status": "new",
                      "type": "processing", "bucket": "processing"}])
        page.fill("#inbox-search", "Queued")
        page.wait_for_timeout(350)
        assert _count(page, "processing") == 1
        page.fill("#inbox-search", "")
        page.wait_for_timeout(350)
    finally:
        _restore(page)


def test_clicking_the_tab_selects_a_review_in_it(page):
    """The tab-click handler had its OWN copy of the tab rule — a third one —
    with no 'processing' branch, so it returned false, selected nothing and
    left the panel blank. A tab that looks broken on the day it ships."""
    try:
        _seed(page, [{"id": "tp_q", "author": "Queued", "status": "new",
                      "type": "processing", "bucket": "processing"}])
        page.click('.inbox-tab[data-tab="processing"]')
        page.wait_for_timeout(400)
        assert page.evaluate("() => state.selected") == "tp_q", \
            "clicking Processing selected nothing"
    finally:
        _restore(page)
        page.click('.inbox-tab[data-tab="all"]')
        page.wait_for_timeout(300)


def test_every_tab_selects_something_that_is_in_it(page):
    """Not just the new one. Three copies of this rule existed; any tab whose
    branch is missing silently selects nothing."""
    bad = page.evaluate("""() => {
      const out = [];
      for (const tab of ['identified','candidates','processing','untraceable','sent']) {
        const hits = REVIEWS.filter(r => inTab(r, tab));
        if (hits.length) {
          const picked = hits[0];
          if (!inTab(picked, tab)) out.push(tab);
        }
      }
      return out; }""")
    assert bad == [], f"these tabs would select a review not in them: {bad}"
