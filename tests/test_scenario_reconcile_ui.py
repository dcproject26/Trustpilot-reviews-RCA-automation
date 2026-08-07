"""The disagreement between a hand-set scenario and routing reaches the card.

The server computes it (`test_scenario_override_api.py`); this is whether a
reader ever sees it. A `diverged` flag that nothing renders is the same defect
as one that is never computed, and it is the one this project keeps shipping —
`validate()` written, tested, and called by nothing.

Two things the row has to get right, and each is a way it could look finished
and not be:

  * silent on agreement. A badge that fires when the override matches routing
    is one people learn to ignore, and then it is not there when it counts;
  * the revert has to actually save. A button that renders and is bound to
    nothing looks exactly like one that works — this suite has caught that
    four times now.

Driven in a browser. The row is drawn by `renderReviewCol` while most of this
file's neighbours are drawn by `renderRcaCol`, which is precisely the split
that killed a set of handlers once already.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401

DIVERGED = {
    "primary": "Guest error",
    "routed_now": "Venue closure (weather/strike)",
    "source": "manual",
    "diverged": True,
    "overlays": [],
    "effective": ["Guest error"],
    "uncovered": [],
}


def _inject(page, routing):
    page.evaluate("""(sr) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__srKeep === undefined)
        window.__srKeep = r.rca.scenarioRouting === undefined
          ? null : JSON.parse(JSON.stringify(r.rca.scenarioRouting || null));
      r.rca.scenarioRouting = sr;
      renderReviewCol();
    }""", routing)
    page.wait_for_timeout(350)


def _restore(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__srKeep !== undefined) r.rca.scenarioRouting = window.__srKeep;
      window.__srKeep = undefined;
      renderReviewCol(); }""")
    page.wait_for_timeout(250)


def _row(page):
    return page.locator(".scenario-reconcile")


# ── it draws only when there is something to reconcile ─────────────────────

def test_nothing_is_drawn_when_the_override_agrees_with_routing(page):
    """"Override matches what routing would now produce → nothing to
    reconcile.\""""
    try:
        _inject(page, {**DIVERGED, "diverged": False,
                       "routed_now": "Guest error"})
        assert _row(page).count() == 0, \
            "a reconcile prompt is showing where there is nothing to reconcile"
    finally:
        _restore(page)


def test_nothing_is_drawn_for_a_routed_scenario(page):
    """A routed primary that fell behind is re-routed, not reconciled. A
    prompt here would appear on drafts nobody touched."""
    try:
        _inject(page, {**DIVERGED, "source": "routed", "diverged": False})
        assert _row(page).count() == 0
    finally:
        _restore(page)


def test_the_row_draws_when_they_disagree(page):
    try:
        _inject(page, DIVERGED)
        assert _row(page).count() == 1, \
            "the override and routing disagree and the card says nothing"
    finally:
        _restore(page)


def test_it_shows_both_values_not_just_a_manual_tag(page):
    """Provenance alone makes the reader reconstruct months later WHY it was
    set. The contradiction is the point."""
    try:
        _inject(page, DIVERGED)
        txt = _row(page).inner_text()
        assert "Guest error" in txt, "the override is not named"
        assert "Venue closure (weather/strike)" in txt, \
            "what routing would say is not shown"
    finally:
        _restore(page)


def test_it_says_which_one_was_set_by_hand(page):
    """Two scenario names side by side with no attribution is a puzzle."""
    try:
        _inject(page, DIVERGED)
        assert "set by hand" in _row(page).inner_text().lower()
    finally:
        _restore(page)


def test_nothing_draws_when_routing_has_no_opinion(page):
    """Showing "routing would say: —" beside an override is a contradiction
    with one side missing."""
    try:
        _inject(page, {**DIVERGED, "routed_now": None})
        assert _row(page).count() == 0
    finally:
        _restore(page)


# ── the revert works ───────────────────────────────────────────────────────

def test_the_revert_button_is_there(page):
    try:
        _inject(page, DIVERGED)
        assert page.locator("[data-scenario-revert]").count() == 1
    finally:
        _restore(page)


def test_the_revert_targets_what_routing_says(page):
    """A revert with no target, or the wrong one, is a button that moves the
    scenario somewhere nobody chose."""
    try:
        _inject(page, DIVERGED)
        assert page.locator("[data-scenario-revert]").get_attribute(
            "data-scenario-revert") == "Venue closure (weather/strike)"
    finally:
        _restore(page)


def test_clicking_revert_saves_the_routed_scenario(page):
    """The whole chain: click, PATCH, re-read, redraw. A button bound to
    nothing renders identically to one that works."""
    try:
        _inject(page, DIVERGED)
        page.locator("[data-scenario-revert]").click()
        page.wait_for_timeout(2000)
        stored = page.evaluate(
            "() => REVIEWS.find(x => x.id === state.selected).rca.primaryScenario")
        assert stored == "Venue closure (weather/strike)", (
            f"revert did not change the scenario: {stored!r}. The button "
            f"renders and is bound to nothing.")
    finally:
        _restore(page)
        page.reload(wait_until="load")
        page.wait_for_selector(".review-item", timeout=15000)
        page.locator(".review-item").first.click()
        page.wait_for_timeout(1500)


# ── the uncovered flag ─────────────────────────────────────────────────────

def test_an_uncovered_scenario_is_flagged(page):
    """Rule 13 holds at generation time. An override applied afterwards
    breaks it, and nothing else on the card would say so."""
    try:
        _inject(page, {**DIVERGED, "diverged": False,
                       "uncovered": ["Guest error"]})
        el = page.locator(".scenario-uncovered")
        assert el.count() == 1, "a scenario with no guest issue behind it is unflagged"
        assert "Guest error" in el.inner_text()
    finally:
        _restore(page)


def test_the_uncovered_flag_says_what_to_do(page):
    """A warning with no next step is one people learn to scroll past."""
    try:
        _inject(page, {**DIVERGED, "diverged": False,
                       "uncovered": ["Guest error"]})
        assert "re-run" in page.locator(".scenario-uncovered").inner_text().lower()
    finally:
        _restore(page)


def test_nothing_is_flagged_when_every_scenario_is_covered(page):
    try:
        _inject(page, {**DIVERGED, "diverged": False, "uncovered": []})
        assert page.locator(".scenario-uncovered").count() == 0
    finally:
        _restore(page)


def test_several_uncovered_scenarios_are_all_named(page):
    """"1 scenario uncovered" makes the reader go and find which."""
    try:
        _inject(page, {**DIVERGED, "diverged": False,
                       "uncovered": ["Guest error", "Meeting point issues"]})
        txt = page.locator(".scenario-uncovered").inner_text()
        assert "Guest error" in txt and "Meeting point issues" in txt
    finally:
        _restore(page)


def test_the_two_rows_are_independent(page):
    """They report different things — a stale override and a coverage gap. A
    draft can have either without the other."""
    try:
        _inject(page, {**DIVERGED, "uncovered": ["Guest error"]})
        assert _row(page).count() == 1
        assert page.locator(".scenario-uncovered").count() == 1
    finally:
        _restore(page)


def test_a_draft_with_no_routing_block_renders_normally(page):
    """Older drafts, and any payload predating this. Neither row should draw,
    and the classification block must still render."""
    try:
        _inject(page, None)
        assert _row(page).count() == 0
        assert page.locator(".scenario-uncovered").count() == 0
        assert page.locator(".classify-block").count() == 1
    finally:
        _restore(page)


def test_the_revert_is_not_bound_with_a_column_scoped_query():
    """The row is drawn by renderReviewCol. Binding it inside renderRcaCol
    with `col.querySelector` finds nothing, because `col` is the RCA column
    and the row is not in it — the button renders perfectly and does nothing.
    That is the §13 failure, and this is the fifth time it has happened here.

    A NEGATIVE source assertion, which is the one shape CLAUDE.md allows: a
    string that appears nowhere cannot be absent for the wrong reason. The
    positive half — that clicking it actually saves — is the browser test
    above."""
    client = open("client/index.html", encoding="utf-8").read()
    assert "col.querySelector('[data-scenario-revert]')" not in client, \
        "the revert is bound with a column-scoped query again"
    assert "data-scenario-revert]')" in client, \
        "the revert is not bound at all — the guard above is guarding nothing"


def test_the_page_is_still_healthy(page):
    assert page.errors == [], page.errors
