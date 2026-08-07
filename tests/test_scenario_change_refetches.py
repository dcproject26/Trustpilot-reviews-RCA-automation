"""Changing the scenario re-reads everything the scenario decides.

"make sure if this is manually changed, everything related to it fetches again
like experience insights and the rca."

The scenario drives the checklists, the issue questions, the DSS routing and
the prompt. Change it and the RCA on screen is an answer to a question nobody
is asking any more — but it still reads as current, because nothing about a
stale analysis looks stale.

Two paths reach this, and before now each did half the job:

  * the chip add/remove re-ran the RCA and never refreshed the insights or the
    routing verdict;
  * the revert refreshed the draft and never re-ran the RCA — so it saved the
    routed scenario and left the analysis written for the overridden one,
    which is precisely the state the uncovered-scenario flag exists to report.

Driven in a browser with the network watched, because "it re-fetches" is a
claim about requests and nothing else can check it.
"""
import contextlib

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401

DIVERGED = {
    "primary": "Guest error",
    "routed_now": "Venue closure (weather/strike)",
    "source": "manual", "diverged": True,
    "overlays": [], "effective": ["Guest error"], "uncovered": [],
}


def _kick_refresh(page):
    """Start the refresh WITHOUT awaiting it.

    THE HANG THIS FIXES. `refreshAfterScenarioChange` is an async function, so
    `page.evaluate("() => refreshAfterScenarioChange(...)")` implicitly returns
    its promise and Playwright AWAITS it — and `evaluate` takes no timeout. A
    fetch that is merely slow when 28 modules share one uvicorn therefore
    became a permanent stall, at 15% of the batch, in a test that passes on
    its own every time.

    The braces make the arrow return undefined, so nothing is awaited. The
    callers already wait on `wait_for_timeout`, which is what they were
    relying on anyway — and a slow request now costs a failed assertion
    instead of a wedged run.
    """
    page.evaluate("() => { refreshAfterScenarioChange(state.selected); }")


@contextlib.contextmanager
def _watch(page):
    """Record every request the page makes inside this block.

    A CONTEXT MANAGER so the listener CANNOT outlive the test. The previous
    version returned an `off()` hook and trusted the caller to call it —
    nothing did, so `page.on` accumulated across the module and every later
    test paid for every earlier test's handler on every single request. An
    un-listen you have to remember is an un-listen that does not happen.
    """
    page.evaluate("() => { window.__reqs = []; }")
    seen = []
    handler = lambda r: seen.append(r.url)      # noqa: E731
    page.on("request", handler)
    try:
        yield seen
    finally:
        page.remove_listener("request", handler)


def _inject(page, routing=None):
    page.evaluate("""(sr) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__sKeep === undefined)
        window.__sKeep = JSON.parse(JSON.stringify(r.rca.scenarioRouting || null));
      r.rca.scenarioRouting = sr;
      renderReviewCol();
    }""", routing)
    page.wait_for_timeout(300)


def _restore(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__sKeep !== undefined) r.rca.scenarioRouting = window.__sKeep;
      window.__sKeep = undefined;
      renderReviewCol(); }""")
    page.wait_for_timeout(250)


# ── the shared refresh exists and does all three ───────────────────────────

def test_the_refresh_helper_is_reachable(page):
    """A helper nothing calls is the failure this file is about, one level
    up."""
    assert page.evaluate(
        "() => typeof refreshAfterScenarioChange === 'function'"), \
        "refreshAfterScenarioChange is not defined"


def test_the_refresh_asks_for_the_draft_and_the_insights(page):
    with _watch(page) as seen:
        _kick_refresh(page)
        page.wait_for_timeout(1500)
        rid = page.evaluate("() => state.selected")
    assert any(u.endswith(f"/api/reviews/{rid}") for u in seen), \
        f"the draft was not re-read: {seen}"
    assert any("/insights?window=" in u for u in seen), \
        f"the insights were not re-fetched: {seen}"


def test_the_refresh_sends_the_window_the_picker_is_showing(page):
    """Refetching on the server's default while the picker highlights another
    window is how the panel showed one window's figures under another's
    button — a defect this dashboard already had."""
    with _watch(page) as seen:
        _kick_refresh(page)
        page.wait_for_timeout(1500)
        want = page.evaluate("() => state.insightsWindow")
    ins = [u for u in seen if "/insights?window=" in u]
    assert ins, "no insights request at all"
    assert any(f"window={want}" in u for u in ins), (want, ins)


def test_the_refresh_updates_the_routing_verdict(page):
    """`diverged` is computed server-side. A local guess is how the card and
    the server end up disagreeing about the same scenario."""
    try:
        _inject(page, DIVERGED)
        assert page.locator(".scenario-reconcile").count() == 1
        _kick_refresh(page)
        page.wait_for_timeout(1500)
        page.evaluate("() => renderReviewCol()")
        page.wait_for_timeout(300)
        assert page.locator(".scenario-reconcile").count() == 0, (
            "the injected divergence survived a refresh — the routing verdict "
            "is not being re-read from the server")
    finally:
        _restore(page)


def test_a_failed_refresh_says_so_rather_than_blanking_the_panel(page):
    """A refresh that failed is not the same as figures that are empty."""
    try:
        page.route("**/insights?**", lambda route: route.abort())
        page.route("**/api/reviews/tp_ui", lambda route: route.abort())
        _kick_refresh(page)
        page.wait_for_timeout(1200)
        err = page.evaluate("() => state.insightsError || ''")
        assert "could not be refreshed" in err, f"silent failure: {err!r}"
    finally:
        page.unroute("**/insights?**")
        page.unroute("**/api/reviews/tp_ui")
        page.evaluate("() => { state.insightsError = ''; }")


# ── the revert path re-runs the RCA ────────────────────────────────────────

def test_reverting_re_runs_the_rca(page):
    """It saved the routed scenario and left the analysis written for the
    overridden one — the state the uncovered flag exists to report."""
    try:
        _inject(page, DIVERGED)
        with _watch(page) as seen:
            page.locator("[data-scenario-revert]").click()
            page.wait_for_timeout(2500)
        assert any("/regenerate-rca" in u for u in seen), (
            f"revert saved the scenario without re-running the RCA: {seen}")
    finally:
        _restore(page)
        page.reload(wait_until="load")
        page.wait_for_selector(".review-item", timeout=15000)
        page.locator(".review-item").first.click()
        page.wait_for_timeout(900)


def test_reverting_also_refreshes_the_insights(page):
    try:
        _inject(page, DIVERGED)
        with _watch(page) as seen:
            page.locator("[data-scenario-revert]").click()
            page.wait_for_timeout(2500)
        assert any("/insights?window=" in u for u in seen), (
            f"the insights were not refreshed after a revert: {seen}")
    finally:
        _restore(page)
        page.reload(wait_until="load")
        page.wait_for_selector(".review-item", timeout=15000)
        page.locator(".review-item").first.click()
        page.wait_for_timeout(900)


def test_a_failed_re_run_still_reports_rather_than_going_quiet(page):
    try:
        _inject(page, DIVERGED)
        page.route("**/regenerate-rca", lambda route: route.abort())
        page.locator("[data-scenario-revert]").click()
        page.wait_for_timeout(2000)
        txt = page.locator("[data-scenario-regen-status]").inner_text()
        assert "failed" in txt.lower(), f"a failed re-run said nothing: {txt!r}"
    finally:
        page.unroute("**/regenerate-rca")
        _restore(page)
        page.reload(wait_until="load")
        page.wait_for_selector(".review-item", timeout=15000)
        page.locator(".review-item").first.click()
        page.wait_for_timeout(900)


def test_the_page_is_still_healthy(page):
    assert page.errors == [], page.errors
