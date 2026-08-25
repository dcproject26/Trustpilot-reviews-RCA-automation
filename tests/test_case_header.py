"""The case header — who is on the review, and the working "Picked up by" control.

The workbench gives the case page a header: ← Inbox (the queue is a separate
surface), the guest name, the stage chip, and the owner control. There is no
signed-in user, so the associate picks their name from the roster in
content/orm_macros.yaml; it saves on change to the endpoint stage 1 added,
repaints the queue column from the same record, and — invariant 9 — reverts if
the save fails, so a control that looks saved always did save.

IT WAS A TEXT BOX, which is how one person arrived as "Avi", "avi" and "Avi " —
three owners as far as any grouping is concerned. The three cases that make
this more than `REVIEWERS.map()` are driven below: a name on the roster, a name
NOT on it (typed before the dropdown existed, or since taken off the roster —
it must still render, because a card that quietly forgets who owns it is worse
than one naming someone who left), and no roster at all.
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

def test_picking_a_name_saves_it(page):
    rid = _open_first_case(page)
    page.select_option("#case-owner-input", "Devshree")
    page.wait_for_timeout(500)
    # the record carries it, and so does the server
    assert page.evaluate("(id) => REVIEWS.find(r => r.id === id).owner", rid) == "Devshree"
    got = page.evaluate("""async (id) => {
      const r = await fetch(`/api/reviews/${id}`); const j = await r.json();
      return j.review.picked_up_by; }""", rid)
    assert got == "Devshree"


def test_the_saved_owner_shows_in_the_queue_column(page):
    """The queue column and the header read the same record — saving in one
    shows in the other with no second fetch."""
    rid = _open_first_case(page)
    page.select_option("#case-owner-input", "Devshree")
    page.wait_for_timeout(500)
    page.click("#case-header .back-to-inbox")
    page.wait_for_selector("#inbox-list .inbox-row", timeout=15000)
    cell = page.evaluate("""(id) => {
      const c = document.querySelector(`#inbox-list .inbox-row[data-id="${id}"] .ic-owner`);
      return c ? c.textContent.trim() : null; }""", rid)
    assert cell == "Devshree"


def test_unassigning_saves_empty_not_unassigned_text(page):
    """Assigned-then-unassigned is a value; the control returns to the
    "unassigned" option and the queue column falls back to its dim rendering,
    but the STORED value is "" — never the literal word on the option."""
    rid = _open_first_case(page)
    page.select_option("#case-owner-input", "Paul")
    page.wait_for_timeout(400)
    page.select_option("#case-owner-input", "")
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
        page.select_option("#case-owner-input", "Shruti")
        page.wait_for_timeout(500)
        after = page.evaluate("(id) => REVIEWS.find(r => r.id === id).owner ?? null", rid)
        assert after == before, f"a failed save stuck: {before!r} -> {after!r}"
        shown = page.evaluate("() => document.querySelector('#case-owner-input').value")
        assert shown != "Shruti", \
            "the dropdown still shows a name the server never stored"
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


# ── the roster, and the two ways a dropdown can lie about ownership ─────────

def _set_roster(page, names):
    """Re-render the header against a given roster."""
    page.evaluate("""(n) => { REVIEWERS = n; REVIEWERS_LOADED = true;
                              renderCaseHeader(); }""", names)


def test_the_options_are_the_roster_from_the_content_file(page):
    _open_first_case(page)
    from server.prompts import REVIEWERS
    opts = page.evaluate("""() => [...document.querySelectorAll(
        '#case-owner-input option')].map(o => o.value)""")
    assert opts[0] == "", "there is no way back to unassigned"
    assert opts[1:] == REVIEWERS, f"{opts[1:]} != {REVIEWERS}"


def test_a_stored_name_off_the_roster_still_shows_and_is_marked(page):
    """THE ONE THAT MATTERS. A select silently drops a value it has no option
    for, so the card would read "unassigned" over a review that IS assigned —
    and the next save would write that lie to the database. Happens to every
    name typed before this dropdown existed, and to anyone taken off the
    roster while still owning open cards."""
    rid = _open_first_case(page)
    page.evaluate("""(id) => { REVIEWS.find(r => r.id === id).owner = 'Someone Who Left';
                               renderCaseHeader(); }""", rid)
    assert page.evaluate(
        "() => document.querySelector('#case-owner-input').value") == "Someone Who Left"
    label = page.evaluate("""() => [...document.querySelectorAll('#case-owner-input option')]
        .find(o => o.value === 'Someone Who Left').textContent""")
    assert "not on the roster" in label, label


def test_an_off_roster_owner_can_still_be_handed_over(page):
    """Showing it is half. The point of the control is reassignment, so the
    roster options must still be reachable from that state."""
    rid = _open_first_case(page)
    page.evaluate("""(id) => { REVIEWS.find(r => r.id === id).owner = 'Someone Who Left';
                               renderCaseHeader(); }""", rid)
    page.select_option("#case-owner-input", "Avi")
    page.wait_for_timeout(500)
    assert page.evaluate("(id) => REVIEWS.find(r => r.id === id).owner", rid) == "Avi"


def test_no_roster_falls_back_to_typing_rather_than_blocking_the_work(page):
    """An empty dropdown says "nobody can be assigned", which is a claim about
    the team rather than about a failed lookup. Work is never blocked on
    /api/taxonomy coming back."""
    _open_first_case(page)
    _set_roster(page, [])
    assert page.evaluate(
        "() => document.querySelector('#case-owner-input').tagName") == "INPUT"


def test_a_roster_that_has_not_loaded_says_so_differently_from_an_empty_one(page):
    """Two empties, two fixes: one is edited in content/orm_macros.yaml, the
    other waits for the server. Same blank control, different sentence."""
    _open_first_case(page)
    page.evaluate("() => { REVIEWERS = []; REVIEWERS_LOADED = false; renderCaseHeader(); }")
    not_loaded = page.evaluate("() => document.querySelector('#case-owner-input').title")
    _set_roster(page, [])
    empty = page.evaluate("() => document.querySelector('#case-owner-input').title")
    assert not_loaded and empty and not_loaded != empty, (not_loaded, empty)
    assert "not loaded" in not_loaded, not_loaded
    assert "orm_macros.yaml" in empty, empty
