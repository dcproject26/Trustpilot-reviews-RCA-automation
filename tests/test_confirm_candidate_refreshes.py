"""Confirming a candidate has to change what is on the screen.

The reported symptom: an associate picks the right booking, the booking
details and the RCA never appear, and the RCA column keeps saying "Locked
until a booking is confirmed" until the page is reloaded by hand.

The server half was fixed by 073ed6a — the run goes through the supervised
batch runner now and no longer disappears into Starlette's unguarded queue.
The client half had two faults of its own, and each alone is enough to
reproduce the report exactly:

  1. THE POLL WAITED FOR SOMETHING THAT HAD ALREADY HAPPENED. It polled for
     `!draft.candidate_state`, and the confirm request itself clears and
     commits that flag before it returns. The condition was true when the
     first poll fired three seconds later, so the poll stopped while the
     pipeline was at step 1 and nothing looked at the review again.

  2. THE REFRESH READ THE WRONG STORE. It called loadDraftOverlays(), which
     does not touch `r.type` — and `r.type` is what decides whether the RCA
     column draws the analysis or its gate. Only fetchInbox() recomputes it,
     from the server's own bucket rule. So the review stayed typed
     'candidates' for the rest of the session.

Driven in a real browser, because both faults are in what the page does with
a response and neither leaves a mark in the source. fetch is stubbed so the
pipeline's timing can be controlled: phase A is a run in flight (candidate
state already cleared, generated_at unchanged), phase B is the run landing.
A build with either fault never leaves the gate in phase B.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


CANDIDATES = [{"id": "44556677", "experience": "Colosseum Guided Tour",
               "score": 4.0, "matchReasons": ["venue", "date"]}]


def _install(page):
    """Make the selected review look like an unconfirmed Tier 2, and put a
    controllable fetch in front of the real one."""
    page.evaluate("""(cands) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      window.__phase = 'A';                       // run in flight
      window.__keep = {type: r.type, cs: r.candidateState, cl: r.candidatesList};
      r.type = 'candidates';
      r.candidateState = true;
      r.candidatesList = cands.map(c => ({
        bid: c.id, score: c.score, matchReasons: c.matchReasons,
        experience: c.experience, tgid: '', tid: '', vendorName: '',
        experienceDate: '', creationDate: '', status: '', leadTime: '',
        guestName: '', contactCount: 0, contactTags: ''}));

      if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
      window.fetch = async (url, opts) => {
        const u = String(url);
        if (u.includes('/select-candidate'))
          return new Response(JSON.stringify({ok: true}), {status: 200});
        if (u.includes('/progress'))
          return new Response(JSON.stringify({running: true, state: 'running',
            step: 3, total: 8, stage: 'zendesk', elapsed_s: 9,
            since_progress_s: 2, stalled_after_s: 600}), {status: 200});
        const res = await window.__realFetch(url, opts);
        // The single-review payload: the server clears candidate_state the
        // moment it accepts the confirmation, so it is ALREADY false in
        // phase A. generated_at is what moves when the run finishes.
        if (/\\/api\\/reviews\\/[^/?]+$/.test(u.split('?')[0])) {
          const body = await res.clone().json();
          if (body.draft) {
            body.draft.candidate_state = false;
            body.draft.generated_at = window.__phase === 'A'
              ? '2026-08-01T00:00:00' : '2026-08-01T09:30:00';
          }
          return new Response(JSON.stringify(body), {status: 200});
        }
        if (u.split('?')[0].endsWith('/api/reviews')) {
          const rows = await res.clone().json();
          rows.forEach(row => {
            row.candidate_state = false;
            row.bucket = window.__phase === 'A' ? 'candidates' : 'identified';
            row.has_booking = window.__phase !== 'A';
            row.confirmed = true;
          });
          return new Response(JSON.stringify(rows), {status: 200});
        }
        return res;
      };
      renderReviewCol(); renderRcaCol();
    }""", CANDIDATES)


def _restore(page):
    page.evaluate("""() => {
      if (window.__realFetch) window.fetch = window.__realFetch;
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__keep) {
        r.type = window.__keep.type;
        r.candidateState = window.__keep.cs;
        r.candidatesList = window.__keep.cl;
      }
      renderReviewCol(); renderRcaCol();
    }""")


def _locked(page):
    return page.evaluate(
        "() => !!document.querySelector('#rca-col .rca-gate')")


def test_the_card_updates_when_the_run_lands(page):
    _install(page)
    try:
        assert _locked(page), "fixture is wrong — the gate should be up first"
        assert page.locator(".candidate-confirm-btn").count() == 1
        page.locator(".candidate-confirm-btn").click()

        # Phase A: the run is going. candidate_state is already false — the
        # confirm request cleared it — so a poll keyed on that flag would stop
        # here, which is the bug.
        page.wait_for_timeout(4200)
        assert _locked(page), (
            "the card left the gate while the run was still going: the poll "
            "read the confirmation, not the result")

        # Phase B: the run writes its result.
        page.evaluate("() => { window.__phase = 'B'; }")
        page.wait_for_timeout(7000)
        assert not _locked(page), (
            "the run finished and the card is still 'Locked until a booking is "
            "confirmed' — the poll either stopped at the confirmation or "
            "refreshed a store the renderer does not read")
        assert page.evaluate(
            "() => REVIEWS.find(x => x.id === state.selected).type") == "identified"
    finally:
        _restore(page)


def test_the_refresh_recomputes_the_bucket_not_just_the_draft(page):
    """The second fault on its own.

    loadDraftOverlays() refreshes the RCA body and leaves r.type alone, so a
    refresh built from it cannot move a review between tabs however many times
    it runs. reloadFromServer() has to do both halves.
    """
    _install(page)
    try:
        page.evaluate("() => { window.__phase = 'B'; }")
        before = page.evaluate("""async () => {
          const r = REVIEWS.find(x => x.id === state.selected);
          await loadDraftOverlays();
          return r.type; }""")
        assert before == "candidates", (
            "loadDraftOverlays now updates r.type — this test is asserting the "
            "wrong mechanism, but check the refresh path before changing it")

        after = page.evaluate("""async () => {
          await reloadFromServer();
          return REVIEWS.find(x => x.id === state.selected).type; }""")
        assert after == "identified", (
            "reloadFromServer did not recompute the bucket, so a confirmed "
            "review keeps rendering as an unconfirmed one")
    finally:
        _restore(page)


def test_a_failed_refresh_says_so_rather_than_returning_quietly(page):
    """A refresh that swallows its own failure leaves the old card on screen
    with nothing to say the data underneath moved — the same picture as a
    successful refresh that found no change."""
    page.evaluate("""() => {
      if (!window.__realFetch) window.__realFetch = window.fetch.bind(window);
      window.fetch = () => Promise.reject(new Error('offline'));
    }""")
    try:
        assert page.evaluate("() => reloadFromServer()") is False
    finally:
        _restore(page)
