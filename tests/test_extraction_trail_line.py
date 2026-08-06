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


# ── what the trail line is HANDED ──────────────────────────────────────────
#
# A mutation collapsing `isinstance(parsed, dict)` into `parsed or {}`
# SURVIVED every test above. They drive the pure trail-line function; nothing
# drove the code deciding what to hand it — the same gap one layer up, and it
# turns an unparseable reply back into "the review named nothing".

from server.pipeline import classify_extraction


def test_a_parsed_object_is_the_answer():
    ind, state, why = classify_extraction({"experience_or_venue": "Colosseum"},
                                          '{"experience_or_venue":"Colosseum"}')
    assert state == "ok" and why == ""
    assert ind["experience_or_venue"] == "Colosseum"


def test_an_empty_parsed_object_is_still_an_answer():
    """A model that looked and found nothing HAS answered. This is the honest
    empty case and it must not be reported as a failure."""
    assert classify_extraction({}, "{}")[1] == "ok"


def test_a_reply_that_is_not_an_object_is_NOT_an_empty_answer():
    """THE MUTATION THAT SURVIVED. Coercing it to {} makes an unparseable
    reply indistinguishable from a review that named nothing — the whole
    fault this tracking exists to fix."""
    ind, state, why = classify_extraction(None, "I'm sorry, I can't help.")
    assert state == "unparsed", (state, why)
    assert ind == {}
    assert "not a JSON object" in why, why


@pytest.mark.parametrize("parsed", [None, "a string", 42, ["a", "list"]])
def test_no_non_dict_is_treated_as_an_answer(parsed):
    assert classify_extraction(parsed, "raw")[1] == "unparsed", parsed


def test_a_raised_call_is_a_failure_and_names_the_exception():
    ind, state, why = classify_extraction(None, "", RuntimeError("529 overloaded"),
                                          ai_live=True)
    assert state == "failed"
    assert "RuntimeError" in why and "529" in why, why


def test_a_raise_with_no_provider_configured_says_THAT_instead():
    """One is broken, the other is how this server is set up, and a reader
    chases only the first."""
    _, state, why = classify_extraction(None, "", RuntimeError("boom"),
                                        ai_live=False, mock_mode=False)
    assert state == "unavailable"
    assert "not configured" in why, why


def test_mock_mode_reports_a_raise_as_a_failure_not_as_unconfigured():
    """In mock mode is_live is false for everything, so the unconfigured
    branch would swallow a real bug in the mock path."""
    _, state, _ = classify_extraction(None, "", RuntimeError("boom"),
                                      ai_live=False, mock_mode=True)
    assert state == "failed"


def test_the_four_states_are_the_ones_the_trail_line_knows():
    """A fifth state would fall through to the failure branch and be reported
    as an outage."""
    states = {
        classify_extraction({}, "{}")[1],
        classify_extraction(None, "prose")[1],
        classify_extraction(None, "", RuntimeError("x"), ai_live=True)[1],
        classify_extraction(None, "", RuntimeError("x"), ai_live=False)[1],
    }
    assert states == {"ok", "unparsed", "failed", "unavailable"}
    for st in states:
        assert line(st, "why", {})["text"].strip(), st
