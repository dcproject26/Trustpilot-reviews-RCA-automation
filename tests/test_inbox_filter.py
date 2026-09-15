"""The + Filter button on the inbox: date range, picked-up-by, and the two
presets Received-today and Pending.

The filter STACKS with the tab and the search — one AND across all three —
and this drives the real page so the DOM plumbing (button → popover → chip
strip → renderInbox → tab counts) is walked end-to-end. The failure mode we
never want back is a filter that says "3 rows" while the tab pill above it
still reads "All 200" — that reads as a broken tool, not a filter.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


# The inbox seed the other tests use writes a broad mix; we plant our own
# small, pinned set of REVIEWS so every assertion is a literal count.
_SEED = r"""() => {
  // A popover from a previous test that clicked the filter button and
  // never closed it will steal our next click. Remove it FIRST.
  document.querySelectorAll('.ibx-pop').forEach(p => p.remove());
  state.filters = {receivedFrom:'', receivedTo:'', owner:'', preset:''};
  state.filter  = 'all';
  state.search  = '';
  state.screen  = 'inbox';
  const day = (iso) => new Date(iso + 'T10:00:00Z').toISOString();
  REVIEWS = [
    {id:'a', author:'Ava',   stars:'★', type:'identified',  status:'draft',
     owner:'Devshree', receivedAt: day('2026-09-15'), booking:{id:'1'}, rca:{}},
    {id:'b', author:'Bela',  stars:'★', type:'candidates',  status:'draft',
     owner:'Shruti',   receivedAt: day('2026-09-15'), booking:{id:'2'}, rca:{}},
    {id:'c', author:'Chip',  stars:'★', type:'sent',        status:'sent',
     owner:'Devshree', receivedAt: day('2026-09-14'), booking:{id:'3'}, rca:{}},
    {id:'d', author:'Dana',  stars:'★', type:'untraceable', status:'draft',
     owner:'',         receivedAt: day('2026-09-13'), booking:{},        rca:{}},
    {id:'e', author:'Erin',  stars:'★', type:'processing',  status:'draft',
     owner:'Devshree', receivedAt: day('2026-09-12'), booking:{id:'5'}, rca:{}},
  ];
  state.selected = null;
  applyScreen(); renderInbox();
}"""


@pytest.fixture(autouse=True)
def _seeded(page):
    page.evaluate(_SEED)
    page.wait_for_selector("#ibx-filter-btn", state="visible", timeout=15000)
    yield


def _rows(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('#inbox-list .inbox-row')]"
        ".map(el => el.dataset.id)")


def _chips(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('#ibx-chip-row .ibx-chip')]"
        ".map(c => c.textContent.trim())")


# ── the button ──────────────────────────────────────────────────────────────

def test_the_button_sits_beside_add_review(page):
    """A filter that lives in a menu three clicks deep is a filter nobody uses;
    the point is that it is there, next to search, all the time."""
    layout = page.evaluate("""() => {
      const bar = document.querySelector('.inbox-search-row');
      const btn = document.getElementById('ibx-filter-btn');
      return {inBar: bar.contains(btn), text: btn.textContent.replace(/\\s+/g,' ').trim()};
    }""")
    assert layout["inBar"]
    assert layout["text"].startswith("⚑ Filter")


def test_the_badge_counts_active_filters(page):
    page.evaluate(
        "() => { state.filters.owner = 'Devshree'; state.filters.preset = 'pending';"
        " _renderChips(); renderInbox(); }")
    txt = page.evaluate(
        "() => document.getElementById('ibx-filter-count').textContent")
    hidden = page.evaluate(
        "() => document.getElementById('ibx-filter-count').hidden")
    assert txt == "2" and hidden is False


# ── the presets ─────────────────────────────────────────────────────────────

def test_received_today_narrows_to_todays_reviews(page):
    """The preset sets the local-day range for BOTH ends; it must include a
    review from today and exclude one from yesterday. The chip proves the
    filter is visible on screen — an invisible filter is why "the list looks
    wrong" was a bug for two weeks running."""
    page.evaluate("""() => {
      // The seed has 15 Sep for a,b and 14 Sep for c; force "today" = 15 Sep
      // so this is stable regardless of when the test runs.
      state.filters.preset      = 'today';
      state.filters.receivedFrom = '2026-09-15';
      state.filters.receivedTo   = '2026-09-15';
      _renderChips(); renderInbox();
    }""")
    assert set(_rows(page)) == {"a", "b"}
    assert any("Received today" in c for c in _chips(page))


def test_pending_excludes_sent_and_only_sent(page):
    """Pending = anything that is not `status === 'sent'`. Untraceable stays,
    processing stays, drafts stay; only Chip (sent) drops out."""
    page.evaluate(
        "() => { state.filters.preset = 'pending';"
        " _renderChips(); renderInbox(); }")
    assert "c" not in _rows(page)
    assert set(_rows(page)) == {"a", "b", "d", "e"}


# ── the fields ──────────────────────────────────────────────────────────────

def test_owner_filter_uses_substring_case_insensitive(page):
    page.evaluate(
        "() => { state.filters.owner = 'DEVS'; _renderChips(); renderInbox(); }")
    assert set(_rows(page)) == {"a", "c", "e"}


def test_owner_filter_unassigned_matches_the_empty_owner(page):
    """Blank owner is displayed as "unassigned"; the filter has to match on
    that same word or the reader searches for "the reviews nobody took" and
    gets nothing back."""
    page.evaluate(
        "() => { state.filters.owner = 'unassigned'; _renderChips(); renderInbox(); }")
    assert _rows(page) == ["d"]


def test_date_range_is_inclusive_on_both_ends(page):
    page.evaluate("""() => {
      state.filters.receivedFrom = '2026-09-13';
      state.filters.receivedTo   = '2026-09-14';
      _renderChips(); renderInbox();
    }""")
    assert set(_rows(page)) == {"c", "d"}


# ── stacking with the tab, and the tab counts ──────────────────────────────

def test_filter_stacks_with_the_tab_as_AND_not_OR(page):
    """If the filter OR-ed with the tab, "Pending + tab=Sent" would be every
    row on the inbox. It has to be zero, or the pill and the list disagree."""
    page.evaluate("""() => {
      state.filter = 'sent';
      state.filters.preset = 'pending';
      _renderChips(); renderInbox();
    }""")
    assert _rows(page) == []


def test_the_tab_counts_reflect_the_filter_not_the_full_inbox(page):
    """The tab pills sit above the list. If they still count the full inbox
    while the filter narrows the list, "All 5" over a list of two is exactly
    the "the tool is broken" complaint. Counts must reflect what the reader
    can see."""
    page.evaluate("""() => {
      state.filters.owner = 'Devshree';
      _renderChips(); renderInbox();
    }""")
    all_count  = page.evaluate("() => document.getElementById('cnt-all').textContent")
    sent_count = page.evaluate("() => document.getElementById('cnt-sent').textContent")
    assert all_count == "3"          # a, c, e
    assert sent_count == "1"         # only c


# ── the chip strip ──────────────────────────────────────────────────────────

def test_dropping_a_chip_clears_that_filter_only(page):
    """Removing the owner chip must not clear the date range; two filters get
    set in independent gestures, they must be dropped in independent ones."""
    page.evaluate("""() => {
      state.filters.owner = 'Devshree';
      state.filters.receivedFrom = '2026-09-15';
      state.filters.receivedTo   = '2026-09-15';
      _renderChips(); renderInbox();
    }""")
    page.evaluate("() => _dropFilter('owner')")
    kept = page.evaluate("() => ({from: state.filters.receivedFrom, "
                         "to: state.filters.receivedTo, owner: state.filters.owner})")
    assert kept["owner"] == "" and kept["from"] == "2026-09-15" and kept["to"] == "2026-09-15"


def test_chip_row_hides_when_nothing_is_filtered(page):
    """No filter set = no chip strip. A visible strip with a "Clear all"
    button on an empty filter is a control that lies about the state."""
    hidden = page.evaluate(
        "() => document.getElementById('ibx-chip-row').hidden")
    assert hidden is True


# ── the popover opens, lists real owners, and toggles the preset ───────────

def test_the_popover_lists_owners_from_the_actual_inbox(page):
    page.click("#ibx-filter-btn")
    page.wait_for_selector(".ibx-pop", state="visible", timeout=5000)
    opts = page.evaluate(
        "() => [...document.querySelectorAll('#ibx-owner-list option')]"
        ".map(o => o.value)")
    assert "Devshree" in opts and "Shruti" in opts
    # "unassigned" is offered on purpose so a reader can pick it as a name.
    assert "unassigned" in opts


def test_applying_the_range_drops_the_today_preset(page):
    """A "Received today" preset that stays on while the range says a
    different day is a control lying about itself. Typing a range demotes
    the preset to plain fields, which the chip strip shows as a range."""
    page.evaluate("""() => {
      state.filters.preset       = 'today';
      state.filters.receivedFrom = '2026-09-15';
      state.filters.receivedTo   = '2026-09-15';
      _renderChips();
    }""")
    page.click("#ibx-filter-btn")
    page.fill("#ibx-from", "2026-09-13")
    page.fill("#ibx-to",   "2026-09-14")
    page.click(".ibx-apply")
    now = page.evaluate("() => ({preset: state.filters.preset, "
                        "from: state.filters.receivedFrom, "
                        "to: state.filters.receivedTo})")
    assert now == {"preset": "", "from": "2026-09-13", "to": "2026-09-14"}
