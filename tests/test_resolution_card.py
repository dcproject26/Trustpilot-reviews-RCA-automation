"""Resolution & takedown, built to the design handoff.

Three parts in a fixed order — what policy prescribes, what the guest actually
got, then the takedown decision. A resolution is only defensible against the
DSS row above it, so the policy is stated first and the remedy reads as an
answer to it.

The control this replaces was worse than incomplete. The Resolution textarea
rendered, accepted typing, and was saved by NOTHING: no handler anywhere in
the client sent it. Type in it, click away, reload, gone — with a green tick
on the way past. That is the TL;DR failure and the area-of-improvement failure
for a third time, so every control here is driven in a browser through to a
reload.

The DSS block above it is deliberately unchanged — it stays as it was, not as
the handoff redrew it.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _set_v3(page, patch):
    page.evaluate("""(p) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__resKeep === undefined)
        window.__resKeep = JSON.parse(JSON.stringify(r.rca.v3 || {}));
      Object.assign(r.rca.v3, p);
      renderRcaCol();
    }""", patch)
    page.wait_for_timeout(350)


def _restore(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__resKeep !== undefined) r.rca.v3 = window.__resKeep;
      window.__resKeep = undefined;
      renderRcaCol(); }""")
    page.wait_for_timeout(250)


def _v3(page, path):
    return page.evaluate(
        """(p) => { let o = REVIEWS.find(x => x.id === state.selected).rca.v3;
             for (const k of p.split('.')) { if (o == null) return null; o = o[k]; }
             return o === undefined ? null : o; }""", path)


# ── order ──────────────────────────────────────────────────────────────────

def test_the_three_parts_are_in_the_handoff_s_order(page):
    """Policy, then what was given, then the decision."""
    try:
        _set_v3(page, {"dss": {"prescribes": "Refund inside policy."},
                       "resolution": {"text": "Refunded."},
                       "takedown": {"verdict": "No"}})
        order = page.evaluate("""() => {
          const sec = [...document.querySelectorAll('.section')]
            .find(s => /Resolution/.test(s.textContent));
          return [...sec.querySelectorAll('.dss-label,.res-label')]
            .map(e => e.textContent.trim()); }""")
        assert order == ["DSS", "Resolution", "Takedown request"], order
    finally:
        _restore(page)


# ── 1. the two DSS absences are different things ───────────────────────────

def test_the_dss_row_stub_is_never_rendered(page):
    """"DSS row: — / —" was the old empty state. A negative check, in a
    browser rather than at source, so it covers every branch above."""
    for payload in ({"dss": {"prescribes": None}}, {"dss": None},
                    {"dss": {"prescribes": "x"}}):
        try:
            _set_v3(page, payload)
            assert "— / —" not in page.locator(".dss-block").inner_text()
        finally:
            _restore(page)


# ── 2. compensation: type, amount, and the unit that follows the type ──────

def test_the_comp_vocabulary_is_closed(page):
    try:
        _set_v3(page, {"resolution": {"text": "x"}})
        got = page.evaluate(
            "() => [...document.querySelectorAll('[data-res-type] option')]"
            ".map(o => o.value)")
        assert got == ["HOC", "Refund to card", "Discount code", "None"], got
    finally:
        _restore(page)


def test_both_is_not_in_the_vocabulary(page):
    """Two things given is two records, each with its own type and amount."""
    try:
        _set_v3(page, {"resolution": {"text": "x"}})
        got = page.evaluate(
            "() => [...document.querySelectorAll('[data-res-type] option')]"
            ".map(o => o.value.toLowerCase())")
        assert "both" not in got
    finally:
        _restore(page)


@pytest.mark.parametrize("comp_type,unit", [
    ("HOC", "%"),
    ("Discount code", "%"),
    ("Refund to card", "USD"),
])
def test_the_unit_follows_the_type(page, comp_type, unit):
    """"25" with no unit is a number nobody can act on."""
    try:
        _set_v3(page, {"resolution": {"compensation_type": comp_type,
                                      "amount": "25", "text": "x"}})
        assert unit in page.locator(".res-head").first.inner_text()
    finally:
        _restore(page)


def test_none_has_no_amount_field_at_all(page):
    """An amount box beside "None" invites a number that contradicts it."""
    try:
        _set_v3(page, {"resolution": {"compensation_type": "None", "text": "x"}})
        assert page.locator("[data-res-amount]").count() == 0
    finally:
        _restore(page)


def test_an_empty_amount_is_a_state_not_an_error(page):
    """We know a HOC was issued and not how much. That is legitimate."""
    try:
        _set_v3(page, {"resolution": {"compensation_type": "HOC",
                                      "amount": None, "text": "x"}})
        assert "amount unknown" in page.locator(".res-head").first.inner_text()
    finally:
        _restore(page)


def test_a_known_amount_does_not_say_unknown(page):
    try:
        _set_v3(page, {"resolution": {"compensation_type": "HOC",
                                      "amount": "25", "text": "x"}})
        assert "amount unknown" not in page.locator(".res-head").first.inner_text()
    finally:
        _restore(page)


# ── everything saves, which is the bug this replaces ───────────────────────

def test_the_comp_type_saves(page):
    try:
        _set_v3(page, {"resolution": {"text": "x"}})
        page.select_option("[data-res-type]", "HOC")
        page.wait_for_timeout(1200)
        assert _v3(page, "resolution.compensation_type") == "HOC"
    finally:
        _restore(page)


def test_the_amount_saves(page):
    try:
        _set_v3(page, {"resolution": {"compensation_type": "HOC", "text": "x"}})
        page.fill("[data-res-amount]", "25")
        page.locator(".res-label").first.click()
        page.wait_for_timeout(1200)
        assert _v3(page, "resolution.amount") == "25"
    finally:
        _restore(page)


def test_an_emptied_amount_becomes_null_not_zero(page):
    """"0%" is a compensation decision somebody made. Blank is "we do not
    know". Coercing one into the other invents a decision."""
    try:
        _set_v3(page, {"resolution": {"compensation_type": "HOC",
                                      "amount": "25", "text": "x"}})
        page.fill("[data-res-amount]", "")
        page.locator(".res-label").first.click()
        page.wait_for_timeout(1200)
        got = _v3(page, "resolution.amount")
        assert got is None, f"an emptied amount became {got!r}"
    finally:
        _restore(page)


def test_switching_to_none_clears_a_stale_amount(page):
    """"None" with an amount left behind says two contradictory things."""
    try:
        _set_v3(page, {"resolution": {"compensation_type": "HOC",
                                      "amount": "25", "text": "x"}})
        page.select_option("[data-res-type]", "None")
        page.wait_for_timeout(1200)
        assert _v3(page, "resolution.amount") is None
    finally:
        _restore(page)


def test_the_resolution_line_saves_and_survives_a_reload(page):
    """The bug this replaces: the old textarea rendered, took typing, and was
    saved by nothing at all."""
    try:
        _set_v3(page, {"resolution": {"text": "old"}})
        el = page.locator('[data-v3p="resolution.text"]').first
        el.click()
        page.keyboard.press("ControlOrMeta+a")
        el.type("Already actioned by CE, approved on 23 Jul")
        page.locator(".res-label").first.click()
        page.wait_for_timeout(1500)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(900)
        page.locator(".review-item").first.click()
        page.wait_for_timeout(1500)
        assert "Already actioned by CE" in page.locator(".res-line").first.inner_text(), \
            "the resolution line was accepted and is not there on reload"
    finally:
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(900)
        page.locator(".review-item").first.click()
        page.wait_for_timeout(1500)


# ── older drafts ───────────────────────────────────────────────────────────

def test_a_draft_whose_resolution_is_a_bare_string_still_shows_it(page):
    """Everything written before the split stores it as a string. Reading
    `.text` off one returns undefined and blanks the line on every older
    review — silently, because a blank line looks like an empty field."""
    try:
        _set_v3(page, {"resolution": "Refund + 25% HOC"})
        assert "Refund + 25% HOC" in page.locator(".res-line").first.inner_text()
    finally:
        _restore(page)


def test_an_older_draft_still_gets_the_controls(page):
    try:
        _set_v3(page, {"resolution": "Refund + 25% HOC"})
        assert page.locator("[data-res-type]").count() == 1
    finally:
        _restore(page)


def test_editing_a_string_shaped_resolution_actually_saves(page):
    """The shape this file originally missed by seeding an OBJECT.

    `resolution` used to be a bare string. The generic saver walks
    `resolution.text`, and assigning a property to a string PRIMITIVE is a
    silent no-op — the field took the edit, showed a green tick, and saved
    nothing. Every draft written before the split was affected, and testing
    only the new shape proved nothing about any of them.
    """
    try:
        _set_v3(page, {"resolution": "Refund + 25% HOC"})
        el = page.locator('[data-v3p="resolution.text"]').first
        el.click()
        page.keyboard.press("ControlOrMeta+a")
        el.type("Rewritten on an old draft")
        page.locator(".res-label").first.click()
        page.wait_for_timeout(1500)
        got = _v3(page, "resolution.text")
        assert got == "Rewritten on an old draft", (
            f"the edit did not reach the store: {got!r}. Writing through a "
            f"string primitive is a silent no-op.")
    finally:
        _restore(page)


# ── the DSS block, unchanged from what it was ──────────────────────────────

def test_an_unclassified_review_says_there_is_nothing_to_look_up(page):
    """With no L1/L2 there is nothing to look up, which is NOT the same as
    looking and finding no row."""
    try:
        page.evaluate("""() => {
          const r = REVIEWS.find(x => x.id === state.selected);
          window.__clsKeep = [r.rca.issueL1, r.rca.issueL2, r.rca.subTheme];
          r.rca.issueL1 = ''; r.rca.issueL2 = ''; r.rca.subTheme = '';
          r.rca.v3.dss = {prescribes: null};
          renderRcaCol(); }""")
        page.wait_for_timeout(350)
        assert "not classified yet" in page.locator(".dss-block").inner_text()
    finally:
        page.evaluate("""() => {
          const r = REVIEWS.find(x => x.id === state.selected);
          if (window.__clsKeep)
            [r.rca.issueL1, r.rca.issueL2, r.rca.subTheme] = window.__clsKeep;
          window.__clsKeep = undefined; }""")
        _restore(page)


def test_the_page_is_still_healthy(page):
    assert page.errors == [], page.errors
