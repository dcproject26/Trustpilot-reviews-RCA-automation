"""The RCA slide-over's six tabs (handoff §1).

The panel stopped being one long scroll and became six tabs — Diagnosis,
Interactions, Actions, Resolution, Reply, Slack — with Classification moved out
of the facts column into Diagnosis, and per-tab badges derived from the rows
each tab renders. All six panels stay mounted (hidden with CSS) so every
per-render control binding still attaches; switching tabs is a CSS toggle, not
a re-render, so an in-progress edit is not thrown away.

Driven against the real DOM: which panel a section lands in, and whether a
hidden panel's controls stay bound, are claims about layout and behaviour that
a source grep cannot make.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME, _rca_tab   # noqa: E402,F401


TABS = ["diag", "inter", "actions", "res", "reply", "slack"]


def _panel_of(page, sel):
    """Which tab-panel a selector's element sits in (or None)."""
    return page.evaluate("""(s) => {
      const el = document.querySelector('#rca-col ' + s);
      if (!el) return null;
      const p = el.closest('.rca-tab-panel');
      return p ? p.dataset.tab : 'OUTSIDE'; }""", sel)


# ── the bar and the panels exist ────────────────────────────────────────────

def test_the_bar_has_the_six_tabs_in_order(page):
    got = page.evaluate(
        "() => [...document.querySelectorAll('#rca-col .rca-tab')]"
        ".map(b => b.dataset.rcaTab)")
    assert got == TABS, got


def test_all_six_panels_are_mounted_even_when_hidden(page):
    """They must all be in the DOM, not created on demand — the per-render
    binders attach to every panel at render time; a panel created only when its
    tab is first clicked would have unbound controls."""
    got = page.evaluate(
        "() => [...document.querySelectorAll('#rca-col .rca-tab-panel')]"
        ".map(p => p.dataset.tab)")
    assert sorted(got) == sorted(TABS), got
    # exactly one is visible at a time
    vis = page.evaluate("""() => [...document.querySelectorAll('#rca-col .rca-tab-panel')]
        .filter(p => getComputedStyle(p).display !== 'none').length""")
    assert vis == 1, f"{vis} panels visible, expected 1"


def test_diagnosis_is_the_default_tab(page):
    active = page.evaluate(
        "() => (document.querySelector('#rca-col .rca-tab-panel.active')||{}).dataset?.tab")
    assert active == "diag"


# ── the section→tab mapping (handoff TAB_OF + the reorder) ───────────────────

def test_classification_moved_into_diagnosis_and_left_the_facts(page):
    """Handoff §3: Classification is a decision from the findings, so it lives
    in Diagnosis, not the facts column."""
    assert _panel_of(page, ".classify-block") == "diag", "classification not in Diagnosis"
    assert page.locator(".review-col .classify-block").count() == 0, \
        "classification is still in the facts column"


@pytest.mark.parametrize("sel,tab", [
    (".stated-issue", "diag"),
    (".classify-block", "diag"),
    ("#rca-casefindings-section", "diag"),
    ("#rca-wwr5-section", "diag"),
    ("#rca-flags-section", "inter"),
    (".action-block", "actions"),
    (".dss-block", "res"),
    ("#rca-reply-section", "reply"),
    ("#rca-slack-section", "slack"),
])
def test_each_section_sits_in_its_handoff_tab(page, sel, tab):
    where = _panel_of(page, sel)
    if where is None:
        pytest.skip(f"{sel} not present for this review")
    assert where == tab, f"{sel} is in {where!r}, handoff puts it in {tab!r}"


# ── switching is CSS, not a re-render ────────────────────────────────────────

def test_clicking_a_tab_shows_its_panel_and_hides_the_rest(page):
    _rca_tab(page, "res")
    seen = page.evaluate("""() => {
      const active = document.querySelector('#rca-col .rca-tab-panel.active').dataset.tab;
      const diag = getComputedStyle(document.querySelector(
        '#rca-col .rca-tab-panel[data-tab=\\"diag\\"]')).display;
      return {active, diag}; }""")
    assert seen["active"] == "res" and seen["diag"] == "none", seen
    _rca_tab(page, "diag")   # leave it as the fixture found it


def test_a_switch_does_not_throw_and_keeps_an_in_progress_edit(page):
    """The point of CSS-hiding over re-rendering: type into a Diagnosis field,
    switch away and back, the unsaved text is still there — a re-render on every
    tab click would have discarded it."""
    errs_before = list(page.errors)
    page.evaluate("""() => {
      const el = document.querySelector('#rca-col [data-v3p="stated_issue"]');
      el.focus(); el.textContent = 'WIP edit not yet blurred'; }""")
    _rca_tab(page, "slack")
    _rca_tab(page, "diag")
    kept = page.evaluate(
        "() => document.querySelector('#rca-col [data-v3p=\"stated_issue\"]').textContent")
    assert kept == "WIP edit not yet blurred", f"the in-progress edit was lost: {kept!r}"
    assert page.errors == errs_before, page.errors


# ── the badge is derived from the rows, not a constant ──────────────────────

def test_the_diagnosis_badge_counts_findings_plus_issues(page):
    """Handoff: badges are derived from the rows the tab renders, never
    hand-written. The Diagnosis badge is guest issues + case findings — proven
    by reading both the badge and the underlying data and requiring they agree,
    so a hard-coded number cannot pass."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const w = ((r.rca||{}).v3||{}).what_went_wrong || {};
      const want = (w.guest_issues||[]).length + (w.case_findings||[]).length;
      const badge = document.querySelector('#rca-col .rca-tab[data-rca-tab="diag"] .tab-n');
      const shown = badge ? parseInt(badge.textContent, 10) : 0;
      return {want, shown}; }""")
    assert got["shown"] == got["want"], \
        f"Diagnosis badge shows {got['shown']}, rows say {got['want']}"


def test_a_zero_count_tab_shows_no_badge(page):
    """Handoff: no badge at all when there are none — a '0' pill is noise."""
    got = page.evaluate("""() => [...document.querySelectorAll('#rca-col .rca-tab')]
      .map(b => ({tab: b.dataset.rcaTab, n: b.querySelector('.tab-n')
        ? b.querySelector('.tab-n').textContent : null}))""")
    for t in got:
        if t["n"] is not None:
            assert int(t["n"]) > 0, f"tab {t['tab']} shows a badge of {t['n']}"


def test_the_interactions_badge_counts_the_rendered_rows_not_the_model_lists(page):
    """The Interactions badge must match what the panel draws — guest↔support
    contacts (frame-groups + orphan notes), SP rows, and flags — not the raw
    model note/record lists. Counting the model lists showed 0 over rows that
    were on screen: the exact drift the panel header comment already had to fix,
    reintroduced in the badge. Read the badge and the rendered rows and require
    they agree."""
    got = page.evaluate("""() => {
      const panel = document.querySelector('.rca-tab-panel[data-tab="inter"]');
      const badge = document.querySelector('#rca-col .rca-tab[data-rca-tab="inter"] .tab-n');
      const shown = badge ? parseInt(badge.textContent, 10) : 0;
      // the panel's own header states the contact count off _rows.length
      const hint = panel.querySelector('.section-label .hint');
      const m = hint ? hint.textContent.match(/(\\d+)\\s+contact/) : null;
      const contacts = m ? parseInt(m[1], 10) : 0;
      // The SP panel always draws one "Raised with SP" summary frame that is
      // NOT a data row (spRowCount excludes it), so SP records = frames minus
      // that one header frame.
      const sp = Math.max(0, panel.querySelectorAll('.sp-frame').length - 1);
      const flags = panel.querySelectorAll('.chk-flag').length;
      return {shown, want: contacts + sp + flags, contacts, sp, flags}; }""")
    assert got["contacts"] or got["sp"] or got["flags"], \
        "the fixture renders no interactions at all, so this proves nothing"
    assert got["shown"] == got["want"], \
        f"Interactions badge shows {got['shown']}, rendered rows say {got['want']} ({got})"


def test_the_resolution_badge_is_derived_from_content_not_a_constant_three(page):
    """The Resolution tab renders one section with three parts (DSS / Resolution
    / Takedown) that are always present. A constant badge of 3 claimed "3 items
    recorded" on a brand-new review with all three empty. The badge must count
    the parts that carry content — so blanking all three drops it to no badge."""
    after = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keepRes = r.rca.resolution;
      const keepV3 = JSON.parse(JSON.stringify(r.rca.v3 || {}));
      r.rca.resolution = '';
      r.rca.v3 = r.rca.v3 || {};
      r.rca.v3.takedown = {};
      r.rca.v3.dss = {};
      renderRcaCol();
      const b = document.querySelector('#rca-col .rca-tab[data-rca-tab="res"] .tab-n');
      const n = b ? parseInt(b.textContent, 10) : 0;
      r.rca.resolution = keepRes; r.rca.v3 = keepV3; renderRcaCol();
      return n; }""")
    assert after == 0, (
        f"Resolution badge stayed at {after} after blanking DSS, resolution and "
        f"takedown — it is a constant, not derived from content")
