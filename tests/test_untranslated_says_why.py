"""No English on a foreign review has four causes, and re-running fixes one.

The card said "Not translated yet — re-run this review to generate it" for all
of them, under a comment asserting it "means the translation step never ran".
It does not. pipeline.py step 1 leaves body_english empty when:

  1. the run died before step 1              — re-running IS the fix
  2. the model answered ENGLISH_ALREADY      — on a Spanish review that is a
                                               mis-detection, not a no-op
  3. english_or_reject refused the result    — a wrong-language translation is
                                               a failed one, withheld on purpose
  4. the translate call threw

Cases 2-4 are unaffected by a re-run, so the old line sent an associate round a
loop that could not terminate — and case 2 hid a wrong answer behind wording
that blamed a step for not running. The pipeline already writes which one
happened onto the confidence trail; the card reads it back now.

These are driven through the real rendered page: the function is client-side
JavaScript, and a source assertion here would be the spelling check CLAUDE.md
forbids.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _render(page, lang, english, trail):
    """Put a review in the given translation state and redraw the facts col."""
    return page.evaluate("""([lang, english, trail]) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (window.__tKeep === undefined)
        window.__tKeep = [r.lang, r.english, r.confidenceTrail];
      r.lang = lang; r.english = english; r.confidenceTrail = trail;
      return translationCard(r);
    }""", [lang, english, trail])


def _restore(page):
    page.evaluate("""() => {
      if (window.__tKeep === undefined) return;
      const r = REVIEWS.find(x => x.id === state.selected);
      [r.lang, r.english, r.confidenceTrail] = window.__tKeep;
      window.__tKeep = undefined; }""")


_REJECTED = [{"mark": "warn",
              "text": "<strong>Translation</strong> — the translation came "
                      "back in Polish, so it was not stored"}]
_ENGLISH_ALREADY = [{"mark": "pass",
                     "text": "<strong>Translation</strong> — none needed; the "
                             "review reads as English (ENGLISH_ALREADY)"}]


def test_a_rejected_translation_does_not_ask_for_a_rerun(page):
    """THE POINT. Re-running re-rejects. Telling the associate to re-run is a
    loop with no exit, and it hides that a translation was produced and
    deliberately withheld."""
    try:
        html = _render(page, "SPANISH", "", _REJECTED)
        assert "came back in Polish" in html, html
        assert "re-run" not in html.lower(), (
            "a rejected translation still tells the reader to re-run")
    finally:
        _restore(page)


def test_a_rejected_translation_is_marked_as_a_problem(page):
    """A withheld translation is a failure, not an empty. It gets the amber
    treatment the rest of the card uses for one."""
    try:
        html = _render(page, "SPANISH", "", _REJECTED)
        assert "rca-empty err" in html, html
    finally:
        _restore(page)


def test_english_already_is_reported_as_the_answer_it_is(page):
    """Not a failure — but on a SPANISH review it is a mis-detection, and the
    reader can only see that if the card says which answer came back."""
    try:
        html = _render(page, "SPANISH", "", _ENGLISH_ALREADY)
        assert "ENGLISH_ALREADY" in html, html
        assert "err" not in html, "a pass verdict was drawn as a failure"
    finally:
        _restore(page)


def test_a_step_that_never_ran_is_the_one_case_that_says_rerun(page):
    """No translation entry on the trail: step 1 did not run on any completed
    pass, and a re-run genuinely is the fix."""
    try:
        html = _render(page, "SPANISH", "", [{"mark": "pass",
                                              "text": "<strong>Match</strong> — T2"}])
        assert "has\n         not run" in html or "not run" in html, html
        assert "Re-run" in html, html
    finally:
        _restore(page)


def test_an_empty_trail_also_reads_as_never_ran(page):
    try:
        html = _render(page, "SPANISH", "", [])
        assert "not run" in html and "Re-run" in html, html
    finally:
        _restore(page)


def test_a_translated_review_still_shows_its_english(page):
    """The success path must be untouched."""
    try:
        html = _render(page, "SPANISH", "Be careful!!!", _REJECTED)
        assert "Be careful!!!" in html
        assert "No English stored" not in html
    finally:
        _restore(page)


def test_an_english_review_draws_no_card_at_all(page):
    try:
        assert _render(page, "EN", "", []) == ""
    finally:
        _restore(page)
