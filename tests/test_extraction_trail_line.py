"""What the indicator extraction produced — three outcomes, not one sentence.

THE CARD SAID, on a Spanish review that plainly describes a date problem:

    Extracted from review: venue='—' · city='—' · visit≈'—' — nothing usable
    was found in the review text, so the search has only the author's name to
    work with

Two separate faults in that line.

FIRST: it is what an OUTAGE printed. `indicators` stayed {} on every failure
path — provider unconfigured, call raised, reply unparseable — and a coercion
below wrote `visit_date_hint = None` into it, which turns {} into a TRUTHY
dict. So the gate `if indicators:` was always true and a timeout rendered
identically to a review that named nothing. The sentence blames the guest's
words for our own failure, and it is the one a reader acts on: they stop
looking.

SECOND: "nothing usable" was decided from three of the eight extracted fields.
`issue_terms` gates the entire shortlist step and is searched one query per
term; `dates_mentioned` drives the support-anchored search. A review with
neither venue nor city nor date but with issue phrases has a real search
running on it, and the card told the reader there was nothing but the name.

A FUNCTION, because the branch this line lives in is unreachable wherever
BigQuery is offline — the pipeline stops before Tier 2 matching — so a test
driving process_review() sees none of these cases and would assert nothing
while looking thorough.
"""
import pytest

from server.pipeline import extraction_trail_line as line


def _t(state, why="", **ind):
    return line(state, why, ind)


# ── the extraction did not produce an answer ───────────────────────────────

@pytest.mark.parametrize("state,why", [
    ("failed", "TimeoutError: read timed out"),
    ("failed", "RuntimeError: Anthropic 529 overloaded"),
    ("unavailable", "the AI provider is not configured on this server"),
    ("unparsed", "the model answered but the reply was not a JSON object"),
])
def test_a_failure_says_the_extraction_produced_no_answer(state, why):
    got = line(state, why, {})
    assert "could not be extracted" in got["text"], got
    assert why in got["text"], got


@pytest.mark.parametrize("state", ["failed", "unavailable", "unparsed"])
def test_a_failure_never_says_the_review_named_nothing(state):
    """The whole point. That sentence is a statement about the guest's text,
    and on these paths the guest's text was never read."""
    got = line(state, "some reason", {})
    assert "nothing usable was found in the review text" not in got["text"], got


@pytest.mark.parametrize("state", ["failed", "unavailable", "unparsed"])
def test_a_failure_says_so_in_words_a_reader_can_act_on(state):
    got = line(state, "some reason", {})
    assert "NOT a review that named nothing" in got["text"], got
    assert "Re-run" in got["text"], got


def test_a_raised_call_is_marked_harder_than_an_unconfigured_provider():
    """One is broken, the other is how this server is set up. A red tick on
    the second would send someone chasing a fault that does not exist."""
    assert line("failed", "boom", {})["mark"] == "fail"
    assert line("unavailable", "not configured", {})["mark"] == "warn"


def test_a_failure_with_no_reason_recorded_still_says_that():
    """An empty `why` must not render as a sentence trailing into nothing."""
    got = line("failed", "", {})
    assert "no reason was recorded" in got["text"], got


# ── it ran, and the review named nothing ───────────────────────────────────

def test_a_genuinely_empty_extraction_keeps_its_sentence():
    """The honest case has to survive: this is the one the sentence was
    written for."""
    got = _t("ok", experience_or_venue=None, city_or_country=None,
             visit_date_hint=None, issue_terms=[], dates_mentioned=[])
    assert "nothing usable was found in the review text" in got["text"], got
    assert got["mark"] == "warn"


def test_an_empty_extraction_and_a_failed_one_do_not_share_a_sentence():
    """The defect, in one assertion."""
    empty = _t("ok", issue_terms=[], dates_mentioned=[])["text"]
    failed = line("failed", "TimeoutError", {})["text"]
    assert empty != failed


# ── it ran, and the review named something ─────────────────────────────────

def test_issue_terms_alone_count_as_something_usable():
    """They gate the whole shortlist step and are searched one query per term.
    Reporting "only the author's name" over a running issue-led search stops
    the reader investigating the search that actually ran."""
    got = _t("ok", issue_terms=["fecha no conseguida", "cannot change date"])
    assert "nothing usable" not in got["text"], got
    assert got["mark"] == "pass"
    assert "2 issue phrase(s)" in got["text"], got


def test_dates_mentioned_alone_count_too():
    """They drive the support-anchored search."""
    got = _t("ok", dates_mentioned=["2026-08-02"])
    assert "nothing usable" not in got["text"], got
    assert "1 date(s) mentioned" in got["text"], got


def test_a_venue_still_counts():
    got = _t("ok", experience_or_venue="Colosseum")
    assert got["mark"] == "pass"
    assert "Colosseum" in got["text"]
    assert "nothing usable" not in got["text"]


def test_the_reader_is_told_the_search_will_lead_on_the_problem():
    """"Something was found" and "a venue was found" are different, and they
    send the reader to different parts of the trail."""
    got = _t("ok", issue_terms=["fecha no conseguida"])
    assert "leads on the problem the guest described" in got["text"], got


def test_that_clause_is_absent_when_a_venue_WAS_found():
    got = _t("ok", experience_or_venue="Colosseum", issue_terms=["late"])
    assert "leads on the problem" not in got["text"], got


def test_blank_and_whitespace_entries_do_not_count_as_found():
    """A list of empty strings is not a set of issue phrases, and counting it
    would turn an empty extraction into a confident one."""
    got = _t("ok", issue_terms=["", "  "], dates_mentioned=[""])
    assert "nothing usable was found" in got["text"], got


# ── shape ──────────────────────────────────────────────────────────────────

def test_the_three_fields_are_always_shown_even_when_empty():
    """The dashes are the evidence that the lookup ran and came back empty."""
    got = _t("ok")["text"]
    for k in ("venue=", "city=", "visit≈"):
        assert k in got, got


def test_a_non_dict_does_not_raise():
    """It runs inside matching, which must not die over a malformed answer."""
    assert line("ok", "", None)["text"]
    assert line("ok", "", "not a dict")["text"]


def test_the_pipeline_uses_this_function_rather_than_its_own_copy():
    """NEGATIVE source assertion, permitted by CLAUDE.md: a second copy of
    this logic in the pipeline would drift, and the one that renders would be
    the untested one."""
    import inspect
    from server import pipeline
    src = inspect.getsource(pipeline.process_review)
    assert "extraction_trail_line(" in src, (
        "the pipeline no longer calls extraction_trail_line")
    assert "nothing usable was found in the review" not in src, (
        "the pipeline has its own copy of the sentence again")
