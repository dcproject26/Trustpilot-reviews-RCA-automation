"""The case header — who is on the review, and the working "Picked up by" input.

The workbench gives the case page a header: ← Inbox (the queue is a separate
surface), the guest name, the stage chip, and a free-text owner input. There is
no signed-in user, so the associate types their name; it saves on blur to the
endpoint stage 1 added, repaints the queue column from the same record, and —
invariant 9 — reverts if the save fails, so a control that looks saved always
did save.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _open_first_case(page):
    page.evaluate("() => { state.screen = 'inbox'; applyScreen(); renderInbox(); }")
    page.wait_for_selector("#inbox-list .inbox-row", timeout=15000)
    rid = page.evaluate("() => document.querySelector('#inbox-list .inbox-row').dataset.id")
    page.locator("#inbox-list .inbox-row").first.click()
    page.wait_for_selector("#case-header .back-to-inbox", timeout=15000)
    return rid


# ── the header is there and reads off the record ────────────────────────────

def test_the_header_carries_back_name_stage_and_owner(page):
    _open_first_case(page)
    assert page.locator("#case-header .back-to-inbox").is_visible()
    assert page.locator("#case-header .ch-name").count() == 1
    assert page.locator("#case-header .ch-stage").count() == 1
    assert page.locator("#case-header #case-owner-input").count() == 1


def test_back_is_in_the_header_not_the_topbar(page):
    """It moved: the topbar is chrome, the case header is where you are."""
    _open_first_case(page)
    assert page.locator(".topbar #back-to-inbox").count() == 0
    assert page.locator("#case-header #back-to-inbox").count() == 1


def test_the_stage_chip_matches_the_queue_row(page):
    """One `_stageOf`, two surfaces — they must not disagree about the review."""
    rid = _open_first_case(page)
    header = page.locator("#case-header .ch-stage").inner_text().strip()
    page.click("#case-header .back-to-inbox")
    page.wait_for_selector("#inbox-list .inbox-row", timeout=15000)
    row = page.evaluate("""(id) => {
      const r = document.querySelector(`#inbox-list .inbox-row[data-id="${id}"] .ic-state .tag`);
      return r ? r.textContent.trim() : null; }""", rid)
    assert header == row, f"header says {header!r}, queue says {row!r}"


@pytest.mark.parametrize("rtype,want", [
    ("untraceable", "Untraceable"),
    ("candidates",  "Confirm match"),
])
def test_the_header_chip_is_derived_not_a_constant(page, rtype, want):
    """The other half of the test above, and the one that has teeth. Opening a
    review that is NOT draft-ready and reading the chip proves the header runs
    `_stageOf` rather than printing a fixed label — a constant would match the
    draft-ready case and hide the drift."""
    page.evaluate("""(t) => {
      window.__save = REVIEWS;
      REVIEWS = [{id:'tp_h', author:'A', stars:'★', lang:'EN', original:'x',
                  owner:null, booking:{}, type:t, status:'new', tier:'—'}];
      state.selected = 'tp_h'; state.screen = 'case';
      applyScreen(); renderInbox(); }""", rtype)
    try:
        page.wait_for_selector("#case-header .ch-stage", timeout=15000)
        chip = page.locator("#case-header .ch-stage").inner_text().strip()
        assert chip == want, f"{rtype} rendered chip {chip!r}, expected {want!r}"
    finally:
        page.evaluate("() => { REVIEWS = window.__save; state.screen='inbox'; "
                      "applyScreen(); renderInbox(); }")


# ── the owner input saves ───────────────────────────────────────────────────

def test_typing_a_name_and_blurring_saves_it(page):
    rid = _open_first_case(page)
    page.fill("#case-owner-input", "Rhea")
    page.locator("#case-owner-input").blur()
    page.wait_for_timeout(500)
    # the record carries it, and so does the server
    assert page.evaluate("(id) => REVIEWS.find(r => r.id === id).owner", rid) == "Rhea"
    got = page.evaluate("""async (id) => {
      const r = await fetch(`/api/reviews/${id}`); const j = await r.json();
      return j.review.picked_up_by; }""", rid)
    assert got == "Rhea"


def test_the_saved_owner_shows_in_the_queue_column(page):
    """The queue column and the header read the same record — saving in one
    shows in the other with no second fetch."""
    rid = _open_first_case(page)
    page.fill("#case-owner-input", "Devshree")
    page.locator("#case-owner-input").blur()
    page.wait_for_timeout(500)
    page.click("#case-header .back-to-inbox")
    page.wait_for_selector("#inbox-list .inbox-row", timeout=15000)
    cell = page.evaluate("""(id) => {
      const c = document.querySelector(`#inbox-list .inbox-row[data-id="${id}"] .ic-owner`);
      return c ? c.textContent.trim() : null; }""", rid)
    assert cell == "Devshree"


def test_clearing_the_name_saves_empty_not_unassigned_text(page):
    """Typed-then-cleared is a value; the input goes empty and the queue column
    falls back to the dim 'unassigned' rendering, but the stored value is ""."""
    rid = _open_first_case(page)
    page.fill("#case-owner-input", "Temp")
    page.locator("#case-owner-input").blur()
    page.wait_for_timeout(400)
    page.fill("#case-owner-input", "")
    page.locator("#case-owner-input").blur()
    page.wait_for_timeout(500)
    stored = page.evaluate("""async (id) => {
      const r = await fetch(`/api/reviews/${id}`); const j = await r.json();
      return j.review.picked_up_by; }""", rid)
    assert stored == "", f"cleared owner stored as {stored!r}, not empty string"


def test_a_failed_save_reverts_so_it_does_not_look_saved(page):
    """Invariant 9. If the PATCH fails, the optimistic value is rolled back —
    a control that shows a name it did not persist is a lie about the data."""
    rid = _open_first_case(page)
    before = page.evaluate("(id) => REVIEWS.find(r => r.id === id).owner ?? null", rid)
    # make the endpoint fail for this one call
    page.evaluate("""() => {
      window.__origFetch = window.fetch;
      window.fetch = (u, o) => (String(u).includes('/picked-up-by'))
        ? Promise.resolve(new Response('nope', {status: 500}))
        : window.__origFetch(u, o); }""")
    try:
        page.fill("#case-owner-input", "WillFail")
        page.locator("#case-owner-input").blur()
        page.wait_for_timeout(500)
        after = page.evaluate("(id) => REVIEWS.find(r => r.id === id).owner ?? null", rid)
        assert after == before, f"a failed save stuck: {before!r} -> {after!r}"
    finally:
        page.evaluate("() => { if (window.__origFetch) window.fetch = window.__origFetch; }")


def test_a_failed_save_reverts_even_if_the_list_was_rebuilt_midflight(page):
    """Invariant 9 under a concurrent poll. `saveOwner` writes the optimistic
    value, then awaits the PATCH. A `fetchInbox` completing during that await
    rebuilds REVIEWS with fresh objects — so the revert must land on the record
    as it stands AFTER the await, re-found by id, not on the object captured
    before it. Reverting the detached copy would leave the visible record stuck
    on the value that never saved.

    The fetch mock stands in for that poll: when the PATCH is issued it swaps in
    a fresh REVIEWS array (new object identities, carrying the optimistic name),
    then fails the request. A correct revert reaches the fresh object; the old
    capture-and-mutate would revert the orphan and leave 'WillFail' showing."""
    rid = _open_first_case(page)
    before = page.evaluate("(id) => REVIEWS.find(r => r.id === id).owner ?? null", rid)
    page.evaluate("""() => {
      window.__origFetch = window.fetch;
      window.fetch = (u, o) => {
        if (String(u).includes('/picked-up-by')) {
          // a poll landed mid-request: REVIEWS is now a different array of
          // different objects (the optimistic name rode across on the copy)
          REVIEWS = REVIEWS.map(r => ({...r}));
          return Promise.resolve(new Response('nope', {status: 500}));
        }
        return window.__origFetch(u, o); }; }""")
    try:
        # drive saveOwner directly so the rebuild is unambiguously mid-await
        page.evaluate("(id) => saveOwner(id, 'WillFail')", rid)
        page.wait_for_timeout(500)
        after = page.evaluate("(id) => REVIEWS.find(r => r.id === id).owner ?? null", rid)
        assert after == before, (
            f"a failed save stuck on the rebuilt list: {before!r} -> {after!r} "
            f"(the revert reached the orphaned record, not the visible one)")
    finally:
        page.evaluate("() => { if (window.__origFetch) window.fetch = window.__origFetch; }")
