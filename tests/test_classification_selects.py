"""The Classification selects offer the real taxonomy, and changing one saves.

Three defects in one block, all shipping:

  * the L1 list was hardcoded — 'SP issue', 'CO miss', 'Fulfilment',
    'Product/UX' — and seven of its eight entries are not L1s;
  * L2_OPTIONS was a placeholder catalogue keyed on those same non-L1s, whose
    own comment said "to be replaced with real list from CX team". It had no
    entry for any real L1, so the L2 dropdown was EMPTY on every review;
  * neither select was bound to anything. No data- hook, no listener.

The three hid each other. A control nobody can operate cannot report that its
options are missing, and a classification that cannot be changed cannot be
observed writing a category nothing downstream matches — which is what
"no support-tag mapping for Operations Issue / Customer Support Issues" was
downstream of.

/api/taxonomy already served all of this. Sub-themes and scenarios were
already reading it. L1/L2 were the one pair left behind.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401

from server.taxonomy import L1_CATEGORIES, L2_OPTIONS


def _opts(page, which):
    return page.evaluate(
        "(w) => { const s = document.querySelector(`[data-classify=${w}]`);"
        "return s ? [...s.options].map(o => o.value) : null; }", which)


def _draft(page):
    return page.evaluate(
        "async () => (await (await fetch('/api/reviews/tp_ui')).json()).draft")


@pytest.fixture(scope="module", autouse=True)
def _restore_classification(page):
    """Put L1/L2 back the way this module found them.

    The tests below change the classification FOR REAL — that is the point of
    them, since a select nobody can operate cannot be observed saving. The
    browser and the server are shared across every UI module now, so what they
    save outlives them: test_inbox_search searches for "Ticket Issues" and
    stopped finding it, because by then this module had changed it to
    something else. Restoring here is cheaper and less fragile than reseeding
    the database between all 28 modules, which fought uvicorn for the SQLite
    write lock and wedged the run.
    """
    before = _draft(page) or {}
    l1, l2 = before.get("l1"), before.get("l2")
    yield
    if not l1:
        return
    page.select_option("[data-classify=l1]", l1)
    page.wait_for_timeout(300)
    if l2:
        try:
            page.select_option("[data-classify=l2]", l2)
        except Exception:
            # The L2 list is derived from L1; if this build no longer offers
            # the old pair, leaving L1 correct is the best available restore
            # and is better than leaving both wrong.
            pass
    page.wait_for_timeout(400)


def test_the_l1_select_offers_the_real_categories(page):
    got = _opts(page, "l1")
    assert got is not None, "the L1 select has no hook, so nothing can bind it"
    assert set(got) >= set(L1_CATEGORIES), (
        f"the L1 list is not the taxonomy. missing: "
        f"{sorted(set(L1_CATEGORIES) - set(got))}")


def test_the_l1_select_offers_nothing_that_is_not_a_category(page):
    """The half that matters. Classifying a review as 'CO miss' writes a
    category the macro filing, the support-tag lookup and the sub-theme
    framework can none of them match — and every one of them then correctly
    reports that it found nothing."""
    stray = [o for o in _opts(page, "l1")
             if o and o not in L1_CATEGORIES]
    assert not stray, f"these are offered as L1s and are not: {stray}"


def test_the_l2_select_is_not_empty(page):
    """It offered exactly one option — the value already set — so the
    classification could not be changed even if the select had been wired."""
    got = _opts(page, "l2")
    assert got, "the L2 select has no options at all"
    assert len(got) > 1, (
        f"the L2 select offers only {got} — there is nothing to change it to")


def test_the_l2_options_are_the_ones_for_the_current_l1(page):
    l1 = page.evaluate(
        "() => document.querySelector('[data-classify=l1]').value")
    got = set(_opts(page, "l2"))
    assert got >= set(L2_OPTIONS.get(l1, [])), (
        f"L2 options for {l1!r} are incomplete: "
        f"{sorted(set(L2_OPTIONS.get(l1, [])) - got)}")


def test_changing_the_l2_reaches_the_server(page):
    l1 = page.evaluate(
        "() => document.querySelector('[data-classify=l1]').value")
    current = _draft(page)["l2"]
    other = next(o for o in L2_OPTIONS[l1] if o != current)
    page.select_option("[data-classify=l2]", other)
    page.wait_for_timeout(700)
    assert _draft(page)["l2"] == other, (
        "the L2 select changed on screen and the draft did not — the control "
        "is not bound to anything")


def test_changing_the_l1_moves_the_l2_to_a_valid_one(page):
    """Keeping the old L2 under a new L1 produces a pair that exists nowhere
    in the taxonomy — the state that makes every downstream lookup correctly
    report nothing."""
    # From the DRAFT, not the select: the previous test's re-render may still
    # be settling, and reading the DOM mid-render is how a test starts
    # measuring its own timing rather than the code.
    page.wait_for_timeout(500)
    l1_now = _draft(page)["l1"]
    other_l1 = next(c for c in L1_CATEGORIES
                    if c != l1_now and L2_OPTIONS.get(c))
    page.select_option("[data-classify=l1]", other_l1)
    page.wait_for_timeout(1200)
    d = _draft(page)
    assert d["l1"] == other_l1, "the L1 change did not reach the server"
    assert d["l2"] in L2_OPTIONS[other_l1], (
        f"L2 is {d['l2']!r}, which is not an L2 of {other_l1!r} — the pair "
        f"cannot match a macro, a support tag or a sub-theme framework")


def test_the_l2_options_follow_the_new_l1(page):
    page.wait_for_timeout(500)
    l1_now = _draft(page)["l1"]
    assert page.evaluate(
        "() => document.querySelector('[data-classify=l1]').value") == l1_now, \
        "the select and the stored classification disagree"
    got = set(_opts(page, "l2"))
    assert got >= set(L2_OPTIONS.get(l1_now, [])), (
        f"after changing L1 to {l1_now!r} the L2 select still offers {got}")


def test_two_changes_in_a_row_both_land(page):
    """The bug the first fix had. The handler was bound inside renderRcaCol
    while the block is drawn by renderReviewCol, so changing the L2 replaced
    both selects with unbound copies and every subsequent change did nothing —
    the first one working is what made it look wired."""
    l1 = _draft(page)["l1"]
    a, b = L2_OPTIONS[l1][0], L2_OPTIONS[l1][1]
    first = a if _draft(page)["l2"] != a else b
    second = b if first == a else a
    page.select_option("[data-classify=l2]", first)
    page.wait_for_timeout(800)
    assert _draft(page)["l2"] == first
    page.select_option("[data-classify=l2]", second)
    page.wait_for_timeout(800)
    assert _draft(page)["l2"] == second, (
        "the second change did nothing — the re-render left the select "
        "unbound")


def test_a_classification_the_taxonomy_does_not_have_is_still_shown(page):
    """A draft classified by an older build must not have its value silently
    dropped from the select — that would make the card show a different
    classification from the one stored."""
    page.evaluate("""async () => {
        await fetch('/api/reviews/tp_ui/draft-v2', {method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({l1: 'CO miss', l2: 'Failed escalation'})}); }""")
    page.reload(wait_until="load")
    page.wait_for_timeout(1000)
    page.locator(".review-item").first.click()
    page.wait_for_timeout(1400)
    assert page.evaluate(
        "() => document.querySelector('[data-classify=l1]').value") == "CO miss"
    assert "Failed escalation" in _opts(page, "l2")
