"""The three cards that replace the single What-went-wrong section.

Driven in a real browser, because every previous client change in this file
that was checked by reading source shipped broken: a handler spliced inside
another handler parses clean and never binds, and a section that renders an
empty block looks exactly like a case with nothing in it.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _q(page, js):
    return page.evaluate(js)


def _patch_and_reload(page, wwr=None, dss=None):
    """Write through the real endpoint and reload, the way a user gets there.

    `rca` is not a page global, and reaching into module scope to fake state
    would test a path no user walks. This is a round trip: PATCH, reload,
    read the DOM.
    """
    page.evaluate("""async ([wwr, dss]) => {
        const cur = (await (await fetch('/api/reviews/tp_ui')).json()).draft;
        const v3  = Object.assign({}, cur.rca_v3 || {});
        if (wwr) v3.what_went_wrong = Object.assign({}, v3.what_went_wrong || {}, wwr);
        if (dss !== undefined) v3.dss = Object.assign({}, v3.dss || {}, dss);
        await fetch('/api/reviews/tp_ui/draft-v2', {
            method: 'PATCH', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({rca_v3: v3})});
    }""", [wwr, dss])
    page.reload(wait_until="load")
    page.wait_for_selector(".review-item", timeout=15000)
    page.locator(".review-item").first.click()
    page.wait_for_selector("#rca-fixes-section", timeout=15000)


# ── §1 case findings ───────────────────────────────────────────────────────

def test_the_case_findings_card_renders(page):
    got = _q(page, "() => !!document.querySelector('#rca-casefindings-section')")
    assert got, "§1 did not render at all"


def test_it_carries_the_evidence_merged_out_of_the_issues(page):
    got = _q(page, """() => [...document.querySelectorAll(
        '#rca-casefindings-section .cf-text')].map(e => e.textContent.trim())""")
    assert got, "§1 rendered with no findings"


def test_no_source_chip_or_time_column_is_drawn(page):
    """The handoff withholds both by name: they are carried for ordering and
    export, not shown."""
    got = _q(page, """() => ({
        rails: document.querySelectorAll('#rca-casefindings-section .ev-src').length,
        times: document.querySelectorAll('#rca-casefindings-section .cf-time').length})""")
    assert got["rails"] == 0 and got["times"] == 0, got


def test_a_zendesk_ref_is_a_real_ticket_url(page):
    """A bare ZD-nnnn in href resolves against the dashboard's own path, so a
    reference the model DID supply looks exactly like one it had not."""
    got = _q(page, """() => [...document.querySelectorAll(
        '#rca-casefindings-section a.cf-ref')].map(a => a.getAttribute('href'))""")
    bad = [h for h in got if h and not h.startswith("http")]
    assert not bad, f"a ref rendered as a relative link: {bad}"


def test_the_evidence_no_longer_renders_inside_an_issue(page):
    """NEGATIVE assertion on the DOM: one fact cited by two claims rendered
    twice, which is what §1 exists to stop."""
    got = _q(page, "() => document.querySelectorAll('.wwr-issue .ev-row').length")
    assert got == 0, f"{got} evidence rows are still inside the issues"


# ── §3 fixes ───────────────────────────────────────────────────────────────

def test_the_fixes_card_renders(page):
    assert _q(page, "() => !!document.querySelector('#rca-fixes-section')")


def test_an_unowned_fix_is_visibly_unrouted(page):
    """A row nobody picks up, hidden, is worse than one shown as unassigned.
    Driven by writing one into the draft and re-rendering."""
    _patch_and_reload(page, wwr={"fixes": [
        {"action": "Nobody owns this", "owner": None, "because": ""}]})
    got = page.evaluate("""() => ({
        note: (document.querySelector('#rca-fixes-section .fx-unrouted')
               || {}).textContent || '',
        hint: (document.querySelector('#rca-fixes-section .hint')
               || {}).textContent || ''})""")
    assert "nobody will pick it up" in got["note"], got
    assert "unrouted" in got["hint"], got["hint"]


def test_an_owned_fix_shows_no_unrouted_callout(page):
    _patch_and_reload(page, wwr={"fixes": [
        {"action": "Alert on failed fulfilment", "owner": "TECH",
         "because": "no alert existed"}]})
    got = page.evaluate(
        "() => document.querySelectorAll('#rca-fixes-section .fx-unrouted').length")
    assert got == 0, "an owned fix rendered as unrouted"


def test_the_add_fix_button_is_bound(page):
    """A handler spliced inside another handler parses clean and never binds.
    Four such failures in this file; this drives the click."""
    _patch_and_reload(page, wwr={"fixes": []})
    got = page.evaluate("""async () => {
        const before = document.querySelectorAll('#rca-fixes-section .fx-row').length;
        document.querySelector('[data-fx-add]').click();
        await new Promise(r => setTimeout(r, 900));
        return {before, after: document.querySelectorAll('#rca-fixes-section .fx-row').length};
    }""")
    assert got["after"] == got["before"] + 1, got


def test_the_add_finding_button_is_bound(page):
    got = page.evaluate("""async () => {
        const before = document.querySelectorAll('#rca-casefindings-section .cf-row').length;
        document.querySelector('[data-cf-add]').click();
        await new Promise(r => setTimeout(r, 600));
        return {before, after: document.querySelectorAll('#rca-casefindings-section .cf-row').length};
    }""")
    assert got["after"] == got["before"] + 1, got


# ── dss.followed ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,chip,phrase", [
    ("followed",      "Yes",           "we took it"),
    ("not_followed",  "No",            "did not take it"),
    ("unestablished", "Unestablished", "does not show what we did"),
    (None,            "Not applicable", "does not apply"),
])
def test_each_dss_verdict_renders_its_own_chip_and_sentence(page, value, chip, phrase):
    """`null` must read as neither a pass nor a miss — hence a sentence and
    not a blank or a dash."""
    _patch_and_reload(page, dss={"followed": value})
    got = page.evaluate("""() => {
        const el = document.querySelector('.dss-followed');
        return el ? el.textContent.replace(/\\s+/g, ' ').trim() : '';
    }""")
    assert chip in got, got
    assert phrase in got, got


def test_the_delete_finding_button_is_bound(page):
    """A × that does nothing is worse than no ×: the reader believes the row
    is gone and it is still on the card."""
    _patch_and_reload(page, wwr={"case_findings": [
        {"text": "Row one", "source": None, "time": None},
        {"text": "Row two", "source": None, "time": None}]})
    got = page.evaluate("""async () => {
        const before = document.querySelectorAll('#rca-casefindings-section .cf-row').length;
        document.querySelector('[data-cf-del]').click();
        await new Promise(r => setTimeout(r, 900));
        return {before, after: document.querySelectorAll('#rca-casefindings-section .cf-row').length};
    }""")
    assert got["after"] == got["before"] - 1, got


def test_the_delete_fix_button_is_bound(page):
    _patch_and_reload(page, wwr={"fixes": [
        {"action": "Fix one", "owner": "TECH", "because": ""},
        {"action": "Fix two", "owner": "CO", "because": ""}]})
    got = page.evaluate("""async () => {
        const before = document.querySelectorAll('#rca-fixes-section .fx-row').length;
        document.querySelector('[data-fx-del]').click();
        await new Promise(r => setTimeout(r, 900));
        return {before, after: document.querySelectorAll('#rca-fixes-section .fx-row').length};
    }""")
    assert got["after"] == got["before"] - 1, got


def test_a_deleted_row_stays_deleted_after_a_reload(page):
    """The splice has to reach the server. A delete that only edits the DOM
    comes back on the next render, which reads as the delete having failed
    silently."""
    _patch_and_reload(page, wwr={"fixes": [
        {"action": "Fix one", "owner": "TECH", "because": ""},
        {"action": "Fix two", "owner": "CO", "because": ""}]})
    page.evaluate("""async () => {
        document.querySelector('[data-fx-del]').click();
        await new Promise(r => setTimeout(r, 900));
    }""")
    page.reload(wait_until="load")
    page.wait_for_selector(".review-item", timeout=15000)
    page.locator(".review-item").first.click()
    page.wait_for_selector("#rca-fixes-section", timeout=15000)
    got = page.evaluate("""() => [...document.querySelectorAll(
        '#rca-fixes-section .fx-action')].map(e => e.textContent.trim())""")
    assert "Fix one" not in got, got


# ── the Unrouted tab, which the server routes to and the client must draw ──

def test_the_unrouted_tab_is_drawn(page):
    """The server routes a fix with no owner to `unrouted`
    (checklist.ACTION_TAB_ORDER). The client's tab strip was built from
    ACTION_TEAMS — nine — so those rows landed on a tab that was never
    rendered: invisible, on the screen whose whole job is to say what each
    team has to pick up."""
    tabs = page.evaluate("""() => [...document.querySelectorAll('.action-tab')]
        .map(b => b.dataset.tab)""")
    assert "unrouted" in tabs, tabs


def test_unrouted_is_first_so_it_cannot_be_scrolled_past(page):
    tabs = page.evaluate("""() => [...document.querySelectorAll('.action-tab')]
        .map(b => b.dataset.tab)""")
    assert tabs[0] == "unrouted", tabs


def test_an_unowned_fix_shows_a_count_on_that_tab(page):
    """A row nobody owns must be countable from the tab strip without opening
    it, or the reader has no reason to look."""
    _patch_and_reload(page, wwr={"guest_issues": [], "fixes": [
        {"action": "Nobody owns this", "owner": None, "because": ""}]})
    got = page.evaluate("""() => {
        const b = document.querySelector('.action-tab[data-tab="unrouted"]');
        return b ? {count: (b.querySelector('.count') || {}).textContent || '',
                    cls: b.className} : null;
    }""")
    assert got and got["count"].strip() == "1", got
    assert "action-tab-unrouted" in got["cls"], \
        "an unrouted row is not visually distinguished from an owned one"


def test_unrouted_is_not_offered_as_a_fix_owner(page):
    """The tab strip and the owner vocabulary are different lists. Offering
    Unrouted as a team would let someone assign work to nobody and think they
    had assigned it."""
    opts = page.evaluate("""() => {
        const s = document.querySelector('#rca-fixes-section select');
        return s ? [...s.options].map(o => o.value) : [];
    }""")
    assert "unrouted" not in [o.lower() for o in opts], opts


# ── deleting a whole issue ─────────────────────────────────────────────────

def _seed_issues(page, n=3):
    _patch_and_reload(page, wwr={"guest_issues": [
        {"issue": f"Issue {k}", "claim": f"claim {k}",
         "claim_accuracy": "Accurate", "root_cause": f"cause {k}"}
        for k in range(n)], "fixes": []})


def test_an_issue_can_be_deleted_entirely(page):
    """The x-del on the claim row removes the claim TEXT and leaves the
    numbered block behind, so an "Untitled issue" with an empty claim and an
    empty analysis had no way off the card at all."""
    _seed_issues(page)
    got = page.evaluate("""async () => {
        window.confirm = () => true;
        const before = document.querySelectorAll('.wwr-issue').length;
        document.querySelector('[data-wwr-issue-del]').click();
        await new Promise(r => setTimeout(r, 900));
        return {before, after: document.querySelectorAll('.wwr-issue').length};
    }""")
    assert got["after"] == got["before"] - 1, got


def test_it_asks_first(page):
    """The one control here that destroys ANALYSIS rather than a field: a
    claim, a verdict, a root cause and its evidence, with no undo."""
    _seed_issues(page)
    got = page.evaluate("""async () => {
        window.confirm = () => false;          // the operator says no
        const before = document.querySelectorAll('.wwr-issue').length;
        document.querySelector('[data-wwr-issue-del]').click();
        await new Promise(r => setTimeout(r, 700));
        return {before, after: document.querySelectorAll('.wwr-issue').length};
    }""")
    assert got["after"] == got["before"], "declining the prompt deleted it anyway"


def test_the_right_issue_goes(page):
    """Off by one here deletes somebody's analysis and keeps the empty block."""
    _seed_issues(page)
    got = page.evaluate("""async () => {
        window.confirm = () => true;
        const dels = [...document.querySelectorAll('[data-wwr-issue-del]')];
        dels[1].click();                        // the middle one
        await new Promise(r => setTimeout(r, 900));
        return [...document.querySelectorAll('.wwr-issue-title')]
                 .map(e => e.textContent.trim());
    }""")
    assert "Issue 1" not in got, got
    assert "Issue 0" in got and "Issue 2" in got, got


def test_the_delete_survives_a_reload(page):
    """A splice that only edits the DOM comes back on the next render, which
    reads as the delete having failed silently."""
    _seed_issues(page)
    page.evaluate("""async () => {
        window.confirm = () => true;
        document.querySelector('[data-wwr-issue-del]').click();
        await new Promise(r => setTimeout(r, 900));
    }""")
    page.reload(wait_until="load")
    page.wait_for_selector(".review-item", timeout=15000)
    page.locator(".review-item").first.click()
    page.wait_for_selector("#rca-fixes-section", timeout=15000)
    got = page.evaluate("""() => [...document.querySelectorAll('.wwr-issue-title')]
        .map(e => e.textContent.trim())""")
    assert "Issue 0" not in got, got
