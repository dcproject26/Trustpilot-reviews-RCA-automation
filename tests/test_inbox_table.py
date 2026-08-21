"""The inbox as a full-width table, and the screen navigation around it.

The workbench replaced the 300px card sidebar with a queue table: one row per
review across Guest · Review · Booking · State · Picked up by · Received, and
each surface of the flow — inbox, case — is its own screen. These drive the
real page, because the columns, the owner cell and the screen switch are all
renderings that can be left behind.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


@pytest.fixture(autouse=True)
def _on_the_inbox(page):
    page.evaluate("() => { state.screen = 'inbox'; applyScreen(); renderInbox(); }")
    page.wait_for_selector("#inbox-search", state="visible", timeout=15000)
    yield


# ── the table ───────────────────────────────────────────────────────────────

def test_the_header_names_all_six_columns(page):
    heads = page.evaluate(
        "() => [...document.querySelectorAll('.inbox-thead > div')]"
        ".map(d => d.textContent.trim())")
    assert heads == ["Guest", "Review", "Booking", "State",
                     "Picked up by", "Received"]


def test_a_row_carries_a_cell_for_each_column(page):
    cells = page.evaluate("""() => {
      const row = document.querySelector('#inbox-list .inbox-row');
      if (!row) return null;
      return ['.ic-guest', '.ic-review', '.ic-booking', '.ic-state',
              '.ic-owner', '.ic-received'].map(s => !!row.querySelector(s));
    }""")
    assert cells == [True, True, True, True, True, True]


def test_the_state_cell_carries_the_stage_chip(page):
    """The stage chip did not move — green nothing-to-do, amber a human must
    act, red stuck — it just lives in the State column now."""
    kinds = page.evaluate("""() => [...document.querySelectorAll(
      '#inbox-list .inbox-row .ic-state .tag')].map(t => t.className)""")
    assert kinds, "no stage chip rendered in any row"
    assert all("stage-" in k for k in kinds)


# ── the owner cell ──────────────────────────────────────────────────────────

def test_an_unowned_review_reads_unassigned(page):
    page.evaluate("""() => {
      window.__save = REVIEWS;
      REVIEWS = [{id:'tp_o', author:'A', stars:'★', lang:'EN', original:'x',
                  owner:null, booking:{}, type:'untraceable', status:'new'}];
      renderInbox(); }""")
    try:
        txt = page.evaluate(
            "() => document.querySelector('#inbox-list .ic-owner').textContent.trim()")
        cls = page.evaluate(
            "() => !!document.querySelector('#inbox-list .ic-owner .ic-owner-none')")
        assert txt == "unassigned" and cls
    finally:
        page.evaluate("() => { REVIEWS = window.__save; renderInbox(); }")


def test_a_named_owner_is_shown(page):
    page.evaluate("""() => {
      window.__save = REVIEWS;
      REVIEWS = [{id:'tp_o', author:'A', stars:'★', lang:'EN', original:'x',
                  owner:'Rhea', booking:{}, type:'untraceable', status:'new'}];
      renderInbox(); }""")
    try:
        txt = page.evaluate(
            "() => document.querySelector('#inbox-list .ic-owner').textContent.trim()")
        named = page.evaluate(
            "() => !!document.querySelector('#inbox-list .ic-owner .ic-owner-name')")
        assert txt == "Rhea" and named
    finally:
        page.evaluate("() => { REVIEWS = window.__save; renderInbox(); }")


# ── the screen navigation ───────────────────────────────────────────────────

def test_selecting_a_row_moves_to_the_case_screen(page):
    """The inbox and the case are separate surfaces. Opening a review hides the
    queue and shows its columns; the ← Inbox control appears."""
    page.locator("#inbox-list .inbox-row").first.click()
    page.wait_for_timeout(300)
    assert page.locator(".main.screen-case").count() == 1
    assert not page.locator(".inbox").is_visible(), "the queue is still showing"
    assert page.locator("#rca-col").is_visible(), "the case columns did not show"
    assert page.locator("#back-to-inbox").is_visible(), "no way back to the inbox"


def test_back_returns_to_the_queue(page):
    page.locator("#inbox-list .inbox-row").first.click()
    page.wait_for_timeout(250)
    page.click("#back-to-inbox")
    page.wait_for_timeout(250)
    assert page.locator(".main.screen-inbox").count() == 1
    assert page.locator("#inbox-search").is_visible(), "the queue did not come back"
    assert not page.locator("#back-to-inbox").is_visible()


def test_the_selection_is_kept_when_returning_and_reopening(page):
    """State lives on the record, not the surface. Going back to the queue and
    the review is still the one that was open."""
    first_id = page.evaluate(
        "() => document.querySelector('#inbox-list .inbox-row').dataset.id")
    page.locator("#inbox-list .inbox-row").first.click()
    page.wait_for_timeout(250)
    assert page.evaluate("() => state.selected") == first_id
    page.click("#back-to-inbox")
    page.wait_for_timeout(200)
    # selection is unchanged by returning — the surface changed, not the record
    assert page.evaluate("() => state.selected") == first_id


# ── the handoff placement: Add review by the search, Reporting by CSV ────────

def test_add_review_sits_in_the_search_row_and_still_opens_the_form(page):
    """Handoff layout: + Add review is beside the search box, not in a footer
    strip. Driven, not just located: a control moved to the right place but
    unbound would be worse than one left where it was, so the click must still
    open the manual-add form."""
    assert page.locator(".inbox-search-row .add-manual-btn").count() == 1, \
        "+ Add review is not in the search row"
    assert page.locator(".inbox-footer").count() == 0, \
        "the old footer strip is still present"
    page.locator(".inbox-search-row .add-manual-btn").click()
    try:
        # the review textarea is the form's own field — assert on it, not the
        # zero-height backdrop wrapper (its class carries no CSS by design)
        page.wait_for_selector("#mr-body", state="visible", timeout=15000)
        assert page.locator("#manual-review-modal .flag-modal").is_visible(), \
            "the moved + Add review no longer opens the form"
    finally:
        page.evaluate("() => document.getElementById('manual-review-modal')?.remove()")


def test_reporting_moved_to_the_topbar_after_csv(page):
    """Handoff layout: Reporting is a topbar action next to CSV, not a footer
    button. It reads off the same bar as Refresh / Fix incomplete / CSV."""
    assert page.locator(".topbar-status .reporting-link").count() == 1, \
        "Reporting is not in the topbar"
    assert page.locator(".inbox-footer .reporting-link").count() == 0, \
        "Reporting is still in a footer"
    labels = page.evaluate(
        "() => [...document.querySelectorAll('.topbar-status button')]"
        ".map(b => b.textContent.trim())")
    csv_i = next(i for i, x in enumerate(labels) if "CSV" in x)
    rep_i = next(i for i, x in enumerate(labels) if "Reporting" in x)
    assert rep_i > csv_i, f"Reporting should sit after CSV in the topbar: {labels}"


# ── a sent review belongs to Sent, not two tabs at once ─────────────────────

@pytest.mark.parametrize("typ", ["candidates", "untraceable", "processing"])
def test_a_sent_review_counts_only_in_sent_not_its_old_working_tab(page, typ):
    """Driven on the real `inTab`: once a review is sent it leaves its working
    tab, exactly as `identified` already did. Otherwise it is counted in both
    its type tab and Sent, and the 'N elsewhere' breakdown sums past the unique
    total (which is what perTab.all counts)."""
    res = page.evaluate("""(typ) => {
      const r = {id:'tp_x', type: typ, status:'sent'};
      return { own: inTab(r, typ), sent: inTab(r, 'sent') }; }""", typ)
    assert res == {"own": False, "sent": True}, \
        f"a sent {typ} review still shows in the {typ} tab: {res}"


def test_the_jump_link_names_the_possible_matches_tab_not_the_stage(page):
    """The 'N elsewhere' jump link reads as the tab it lands on."""
    assert page.evaluate("() => _tabLabel('candidates')") == "Possible matches"
