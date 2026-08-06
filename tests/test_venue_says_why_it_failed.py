"""Why no TGID resolved — four situations, one sentence, three rounds lost.

The card said `Venues extracted but no TGIDs resolved: ['premo tickets for
collosseum', 'Rome, Italy']` and that sentence covered:

  * looked up exactly and with spelling tolerance, and the catalogue has no
    such experience — the only genuine miss;
  * the spelling pass NEVER RAN, because the resolver is on the fallback
    table where `_fuzzy_rows` returns None by design, so a misspelled venue
    cannot resolve however close it is;
  * the spelling pass FAILED, because `EDIT_DISTANCE` is not available on this
    BigQuery edition — the exact path survives that, so the tolerance is off
    with nothing on screen to say so;
  * no usable token at all, because the hint was a city or filler.

Only the first is the venue being absent. The other three are the mechanism
being off, and the reader cannot act on any of them without being told which.

This is CLAUDE.md §1: a broken mechanism and an empty result producing
identical output. It cost three rounds of diagnosis on one review, which is
what a shared sentence costs.

The classification is driven through `explain_failure`, not through
`resolve()`. Where BigQuery is unreachable — here, and on any machine without
the connector — `resolve()`'s error handler clears `_WORKING_TABLE` before the
classification runs, so three of the four branches are unreachable and a test
that went through `resolve()` would assert the SAME sentence four times while
appearing to cover four cases. That is the failure this file is about, so it
must not be the shape of the file.

The last two tests do go through `resolve()`, to pin that the classifier is
actually wired to `last_failure` rather than written and called by nothing.
"""
import pytest

import server.services.venue_resolver as vr


@pytest.fixture(autouse=True)
def _reset():
    vr.last_failure.clear()
    vr.last_failure.update({"why": "", "tokens": [], "table": ""})
    vr._fuzzy_unavailable = ""
    yield
    vr._WORKING_TABLE = None


def _resolve(hints):
    import asyncio
    return asyncio.run(vr.resolve(hints))


def test_a_city_only_hint_says_there_was_no_token():
    """'Rome, Italy' yields no venue token at all. That is a different failure
    from a token that was looked up and missed."""
    why = vr.explain_failure([], None)
    assert "no usable venue token" in why, why


def test_the_fallback_table_says_the_spelling_pass_cannot_run():
    """The likely cause of the reported case. On the fallback table a
    misspelling can never resolve, and the card must say so rather than
    implying the catalogue lacks the venue."""
    why = vr.explain_failure(["collosseum"], "fallback:fct_bookings")
    assert "fallback" in why and "spelling" in why, why
    assert "MISSPELLED" in why or "misspelled" in why, why


def test_an_unavailable_edit_distance_is_named():
    """EDIT_DISTANCE missing is caught so the exact path survives — which is
    right, and silent, so the tolerance is off with nothing to show for it."""
    why = vr.explain_failure(["collosseum"], "proj.ds.dim_experiences",
                             "Function not found: EDIT_DISTANCE")
    assert "EDIT_DISTANCE" in why, why


def test_no_reachable_table_is_not_reported_as_a_missing_venue():
    why = vr.explain_failure(["collosseum"], None)
    assert "no experience table" in why or "nothing was looked up" in why, why


def test_a_genuine_catalogue_miss_is_the_only_one_that_blames_the_catalogue():
    """The one case where "we do not sell this" is the honest reading. It
    requires a real table, a token long enough to have been fuzzed, and no
    EDIT_DISTANCE error — i.e. every mechanism confirmed to have run."""
    why = vr.explain_failure(["collosseum"], "proj.ds.dim_experiences")
    assert "no such experience" in why, why


def test_a_short_token_is_not_a_catalogue_miss():
    """'roma' is below FUZZY_MIN_LEN, so only an exact match could ever have
    matched it. Reporting that as "the catalogue has no such experience"
    claims a spelling pass that was never eligible to run."""
    why = vr.explain_failure(["roma"], "proj.ds.dim_experiences")
    assert "too short" in why, why


def test_the_six_reasons_are_distinguishable():
    """The whole point. If any two produce the same sentence, the reader is
    back to guessing and this file has bought nothing."""
    seen = {
        vr.explain_failure([], None),
        vr.explain_failure(["collosseum"], None),
        vr.explain_failure(["collosseum"], "fallback:fct_bookings"),
        vr.explain_failure(["roma"], "proj.ds.dim_experiences"),
        vr.explain_failure(["collosseum"], "proj.ds.dim_experiences", "no EDIT_DISTANCE"),
        vr.explain_failure(["collosseum"], "proj.ds.dim_experiences"),
    }
    assert len(seen) == 6, f"reasons collapsed into {len(seen)}: {seen}"


def test_no_token_wins_over_no_table():
    """Order, not just coverage. With neither a token nor a table, the reader
    must be told the hint was unusable — "no table could be reached" sends
    them to check a connection that was never the problem."""
    assert "no usable venue token" in vr.explain_failure([], None)


def test_the_fallback_table_is_not_reported_as_edit_distance_failing():
    """On the fallback table the spelling pass never runs, so a stale
    EDIT_DISTANCE error from an earlier resolve must not be what the reader
    is told to chase."""
    why = vr.explain_failure(["collosseum"], "fallback:fct_bookings",
                             "Function not found: EDIT_DISTANCE")
    assert "fallback" in why, why
    assert "EDIT_DISTANCE" not in why, why


# ── and that resolve() actually uses it ─────────────────────────────────────
#
# Without these two, `explain_failure` could be correct, tested, and called by
# nothing — the exact shape CLAUDE.md §1 opens with.

def test_resolve_records_a_reason_rather_than_leaving_it_blank():
    """BigQuery is unreachable in the test environment, so this lands on the
    "no table" branch. WHICH branch is not the point — that `last_failure` is
    populated by a real resolve() at all is."""
    vr._WORKING_TABLE = None
    _resolve(["premo tickets for collosseum"])
    assert vr.last_failure["why"], "resolve() left no reason behind"
    assert vr.last_failure["why"] == vr.explain_failure(
        vr.last_failure["tokens"], None), vr.last_failure


def test_the_tokens_that_were_tried_are_recorded():
    """So a reader can see that 'collosseum' was searched and 'Rome' was not,
    without reading the resolver."""
    vr._WORKING_TABLE = None
    _resolve(["premo tickets for collosseum", "Rome, Italy"])
    assert vr.last_failure["tokens"] == ["collosseum"], vr.last_failure["tokens"]


def test_a_misspelling_is_within_budget_so_the_pass_is_worth_running():
    """The premise behind all of this: the tolerance CAN reach Colosseum from
    collosseum. If it could not, the fallback-table gap would not matter."""
    assert vr.fuzzy_budget("collosseum") >= 1
    assert vr.fuzzy_budget("rome") == 0        # too short to be safe
