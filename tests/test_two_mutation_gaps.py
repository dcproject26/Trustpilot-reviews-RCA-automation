"""Two survivors from the 148-mutation pass over the combined tree.

Neither is broken code. Both are guarantees nothing was holding, which is the
same thing one refactor later — and both are the shape this project keeps
punishing: a thing that could not happen looking exactly like a thing that did.

  1. `const head = passed` → `const head = true`. The collapsed trail draws a
     "N checks passed" summary line. With nothing passing, `true` renders
     "0 checks passed" — a reassuring sentence over a trail where every step
     failed. Nothing asserted the line stays away at zero.

  2. `class="chip-select td-${cur.toLowerCase()}"` → `class="chip-select"`.
     The takedown verdict chip carries its verdict in its colour: Yes, No and
     Untraceable are three different decisions and three different treatments.
     Strip the class and all three render identically — the control still
     works, and the card silently stops distinguishing them at a glance.

Driven in the browser. The first calls the page's own trailView; the second
reads the rendered class list.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


# ── 1. a collapsed trail with nothing passing ──────────────────────────────

def _collapsed(page, steps):
    return page.evaluate("""(steps) => {
      const keep = state.trailOpen['zz'];
      state.trailOpen['zz'] = false;
      const html = trailView(steps, 'zz');
      state.trailOpen['zz'] = keep;
      return html;
    }""", steps)


def test_a_trail_with_nothing_passing_does_not_claim_a_pass(page):
    html = _collapsed(page, [
        {"mark": "warn", "text": "<strong>RCA</strong> a coercion fired"},
        {"mark": "fail", "text": "<strong>Zendesk</strong> a note would not join"},
    ])
    assert "0 check" not in html, (
        '"0 checks passed" over a trail where every step failed — a summary '
        'line that reassures about nothing')
    assert "passed</span>" not in html, html[:300]


def test_a_trail_with_passes_still_summarises_them(page):
    """The other half. Removing the line entirely would 'fix' the mutation and
    lose the collapse that makes the trail readable."""
    html = _collapsed(page, [
        {"mark": "pass", "text": "<strong>BID extracted</strong> via attachment"},
        {"mark": "pass", "text": "<strong>Dates line up</strong>"},
        {"mark": "fail", "text": "<strong>Zendesk</strong> a note would not join"},
    ])
    assert "2 checks passed" in html, html[:300]


def test_one_pass_is_singular(page):
    html = _collapsed(page, [
        {"mark": "pass", "text": "<strong>BID extracted</strong> via attachment"},
        {"mark": "warn", "text": "<strong>RCA</strong> a coercion fired"},
    ])
    assert "1 check passed" in html, html[:300]


def test_the_steps_that_did_not_pass_are_always_shown(page):
    """Collapsing must never hide a failure — that is the whole point of
    showing what went wrong and summarising what went right."""
    html = _collapsed(page, [
        {"mark": "pass", "text": "<strong>BID extracted</strong>"},
        {"mark": "fail", "text": "<strong>Zendesk</strong> a note would not join"},
    ])
    assert "a note would not join" in html


# ── 2. the takedown verdict chip's colour ──────────────────────────────────

@pytest.mark.parametrize("verdict", ["Yes", "No", "Untraceable"])
def test_the_takedown_chip_carries_its_verdict_in_its_class(page, verdict):
    """Three decisions, three treatments. Stripped of the class they render
    identically and the card stops distinguishing them at a glance, while the
    control keeps working — so nothing looks broken."""
    cls = page.evaluate("""(v) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      // The chip reads rca.v3.takedown (v3d in the renderer), not
      // rca.takedown — two stores for one fact, and the render takes v3.
      r.rca.v3 = r.rca.v3 || {};
      const keep = r.rca.v3.takedown;
      r.rca.v3.takedown = {verdict: v};
      renderRcaCol();
      const el = document.querySelector('[data-takedown-rec]');
      const out = el ? el.className : '(no chip)';
      r.rca.v3.takedown = keep; renderRcaCol();
      return out;
    }""", verdict)
    assert f"td-{verdict.lower()}" in cls, (
        f"the {verdict} chip renders as {cls!r} — the verdict is no longer "
        f"visible in the chip's treatment")


def test_the_three_verdicts_do_not_share_one_class(page):
    """A guard on the guard: three classes that happen to be equal would pass
    the check above for each one individually."""
    seen = set()
    for v in ("Yes", "No", "Untraceable"):
        seen.add(page.evaluate("""(v) => {
          const r = REVIEWS.find(x => x.id === state.selected);
          r.rca.v3 = r.rca.v3 || {};
          const keep = r.rca.v3.takedown;
          r.rca.v3.takedown = {verdict: v};
          renderRcaCol();
          const el = document.querySelector('[data-takedown-rec]');
          const out = el ? el.className : '';
          r.rca.v3.takedown = keep; renderRcaCol();
          return out;
        }""", v))
    assert len(seen) == 3, f"the three verdicts render as {seen}"
