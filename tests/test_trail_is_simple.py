"""The match trail is readable, and nothing in it can break the page.

"How we built this match - make this extremely simple, dont have to give too
much of info here, it can be confusing to the team using the app. also make
sure no sql code is breaking here."

The trail is 40-odd steps of pipeline vocabulary — "Mock BQ", "regex BID",
"Author parsed: first='…'". That is a debugging log, and the people reading
this card are deciding whether to trust a booking match.

Two problems, one of them a rendering bug. Step text is written WITH <strong>
in it, so it was injected raw — which also injects whatever the pipeline put
there, and some of those values are SQL, error text and quoted fragments. A
stray '<' swallows the rest of the line; a query with angle brackets can take
the panel with it.

Simplifying must not become hiding: a shorter trail that drops steps would
make a step that ran and a step that never ran look identical, which is the
thing this codebase is built around. So the full trail is one click away.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401

NASTY = [
    {"mark": "pass", "text": "<strong>BID</strong> ok"},
    {"mark": "pass", "text": "<strong>BQ:</strong> SELECT * FROM t WHERE a < 5 AND b > 2"},
    {"mark": "pass", "text": "plain step"},
    {"mark": "warn", "text": "<strong>Zendesk</strong> search returned nothing"},
]


def _set(page, steps):
    page.evaluate("""(st) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__tKeep === undefined)
        window.__tKeep = JSON.parse(JSON.stringify(r.confidenceTrail || []));
      r.confidenceTrail = st;
      state.trailOpen = {};
      renderReviewCol();
    }""", steps)
    page.wait_for_timeout(350)


def _restore(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__tKeep !== undefined) r.confidenceTrail = window.__tKeep;
      window.__tKeep = undefined; state.trailOpen = {};
      renderReviewCol(); }""")
    page.wait_for_timeout(250)


def _trail(page):
    return page.evaluate("""() => {
      const l = [...document.querySelectorAll('.confidence-trail-label')]
        .find(e => /How we built/i.test(e.textContent));
      return l ? l.parentElement.innerText : ''; }""")


# ── simple by default ──────────────────────────────────────────────────────

def test_the_trail_renders(page):
    try:
        _set(page, NASTY)
        assert "How we built" in _trail(page) or _trail(page)
    finally:
        _restore(page)


def test_passing_steps_collapse_to_one_line(page):
    """Three passes become "3 checks passed" rather than three lines of
    pipeline vocabulary."""
    try:
        _set(page, NASTY)
        txt = _trail(page)
        assert "3 checks passed" in txt, txt
        assert "SELECT" not in txt, "a SQL step is still being shown by default"
    finally:
        _restore(page)


def test_what_went_wrong_is_never_collapsed(page):
    """The warn and fail steps are the ones that change what the reader does.
    Collapsing those would be hiding, not simplifying."""
    try:
        _set(page, NASTY)
        assert "Zendesk" in _trail(page)
    finally:
        _restore(page)


def test_the_full_trail_is_one_click_away(page):
    """A shorter trail must not become a trail that hides that a step ran."""
    try:
        _set(page, NASTY)
        page.locator("[data-trail-toggle]").first.click()
        page.wait_for_timeout(400)
        txt = _trail(page)
        assert "SELECT" in txt, "the full trail does not show every step"
        assert "plain step" in txt
    finally:
        _restore(page)


def test_the_toggle_says_how_many_are_hidden(page):
    try:
        _set(page, NASTY)
        assert "4 steps" in page.locator("[data-trail-toggle]").first.inner_text()
    finally:
        _restore(page)


def test_a_trail_of_only_passes_still_offers_the_detail(page):
    try:
        _set(page, [{"mark": "pass", "text": "a"}, {"mark": "pass", "text": "b"}])
        assert "2 checks passed" in _trail(page)
        assert page.locator("[data-trail-toggle]").count() == 1
    finally:
        _restore(page)


def test_an_empty_trail_draws_nothing_rather_than_a_zero(page):
    try:
        _set(page, [])
        assert "checks passed" not in _trail(page)
        assert page.locator("[data-trail-toggle]").count() == 0
    finally:
        _restore(page)


# ── nothing in a step can break the page ───────────────────────────────────

def test_sql_in_a_step_renders_as_text(page):
    """`a < 5 AND b > 2` injected raw makes the browser open a tag and eat the
    rest of the line."""
    try:
        _set(page, NASTY)
        page.locator("[data-trail-toggle]").first.click()
        page.wait_for_timeout(400)
        txt = _trail(page)
        assert "a < 5 AND b > 2" in txt, f"the SQL was mangled: {txt!r}"
    finally:
        _restore(page)


def test_a_script_tag_in_a_step_does_not_become_an_element(page):
    try:
        _set(page, [{"mark": "warn", "text": "<img src=x onerror=1> broke"}])
        html = page.evaluate("""() => {
          const l = [...document.querySelectorAll('.confidence-trail-label')]
            .find(e => /How we built/i.test(e.textContent));
          return l ? l.parentElement.innerHTML : ''; }""")
        assert "<img" not in html, "markup from a trail step reached the DOM"
        assert "&lt;img" in html
    finally:
        _restore(page)


def test_the_one_allowed_tag_still_renders(page):
    """<strong> is what the pipeline writes deliberately. Escaping everything
    would leave the whole trail reading as literal angle brackets."""
    try:
        _set(page, [{"mark": "warn", "text": "<strong>BID</strong> missing"}])
        html = page.evaluate("""() => {
          const l = [...document.querySelectorAll('.confidence-trail-label')]
            .find(e => /How we built/i.test(e.textContent));
          return l ? l.parentElement.innerHTML : ''; }""")
        assert "<strong>BID</strong>" in html
    finally:
        _restore(page)


def test_the_page_is_still_healthy(page):
    assert page.errors == [], page.errors
