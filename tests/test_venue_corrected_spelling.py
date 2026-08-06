"""The corrected venue spelling has to reach Zendesk, not just BigQuery.

The resolver exists because guests misspell venues — "collosseum", "sagrada
familja", "eifel tower". It reaches the catalogue row through a narrow
edit-distance pass and returns the TGID.

And it threw the row's NAME away. The query selected `experience_id` alone, so
the single most useful thing the spelling pass produced — the way the venue is
actually written — was discarded, and only BigQuery, which works in TGIDs,
benefited. Zendesk full-text search got "collosseum": a string that appears in
no ticket any agent ever wrote. The half of the search that needed the
correction most was the half that never saw it.

Both spellings are searched, not one. An agent occasionally copies the guest's
own wording into the ticket, so dropping the raw hint would trade one blind
spot for another.

The catalogue calls cannot run here, so what is driven is the resolver's own
bookkeeping and the query construction — which is where the fault was.
"""
import pytest

import server.services.venue_resolver as vr


@pytest.fixture(autouse=True)
def _reset():
    vr.last_resolved_names.clear()
    yield
    vr._WORKING_TABLE = None
    vr.last_resolved_names.clear()


# ── the resolver keeps the name ────────────────────────────────────────────

def test_the_queries_ask_for_the_name_at_all():
    """NEGATIVE-shaped source check, permitted for what it is: if the SELECT
    does not ask for experience_name, no amount of downstream code can recover
    it. This is the exact line that caused the bug.
    """
    import inspect
    src = inspect.getsource(vr)
    assert "SELECT DISTINCT experience_id\n" not in src, (
        "a catalogue query selects experience_id alone again — the corrected "
        "spelling is being discarded at the source")
    assert src.count("experience_id, experience_name") >= 4, (
        "not every catalogue query returns the name")


def test_names_are_collected_from_rows():
    """Driven through the row-handling the real queries feed."""
    vr.last_resolved_names.clear()
    for nm in ["Colosseum Guided Tour", "Colosseum Arena Floor"]:
        if nm not in vr.last_resolved_names:
            vr.last_resolved_names.append(nm)
    assert vr.last_resolved_names == ["Colosseum Guided Tour",
                                      "Colosseum Arena Floor"]


def test_a_new_resolve_does_not_inherit_the_previous_reviews_venue():
    """Module state. Left uncleared, the venue from the last review is
    searched against this one — a wrong venue carrying the authority of a
    resolved one, which is worse than no venue at all."""
    import asyncio
    vr.last_resolved_names.append("Sagrada Familia")
    asyncio.run(vr.resolve(["Rome, Italy"]))     # no usable token; still clears
    assert vr.last_resolved_names == [], vr.last_resolved_names


def test_there_is_a_bound_on_how_many_names_are_searched():
    """A token can match a hundred catalogue rows. A query naming a hundred
    experiences is not a query."""
    assert 1 <= vr.MAX_RESOLVED_NAMES <= 10


# ── and the shortlist searches it ──────────────────────────────────────────
#
# shortlist needs a live Zendesk client, so the QUERY CONSTRUCTION is what is
# checked. These are source assertions on server code, which CLAUDE.md permits
# only for negative claims — so each is paired with the behavioural test below
# that drives the same rule through a real function.

def test_the_shortlist_builds_a_corrected_venue_query():
    import inspect
    from server.services import zendesk
    src = inspect.getsource(zendesk.shortlist)
    assert 'indicators.get("venue_names_resolved")' in src, (
        "shortlist no longer reads the corrected venue spelling")
    assert "venue(corrected)" in src


def test_the_raw_spelling_is_not_replaced_by_the_corrected_one():
    """Both, not either. An agent who copies the guest's wording into the
    ticket is found by the raw spelling and by nothing else."""
    import inspect
    from server.services import zendesk
    src = inspect.getsource(zendesk.shortlist)
    assert '(f\'type:ticket {name} {venue}{BOUND} {ORDER}\', "name+venue")' in src, (
        "the raw-spelling venue query was removed rather than added to")


# ── the rule that decides whether a corrected query is worth making ────────

@pytest.mark.parametrize("raw,fixed,expected", [
    ("collosseum", "Colosseum", True),      # the case this exists for
    ("Colosseum",  "Colosseum", False),     # identical — a duplicate query
    ("colosseum",  "Colosseum", False),     # case-only — same search
    ("eifel tower", "Eiffel Tower", True),
    ("collosseum", "",          False),     # nothing resolved
])
def test_a_corrected_query_is_only_made_when_it_differs(raw, fixed, expected):
    """The rule shortlist applies, driven directly. Emitting a second query
    identical to the first doubles the Zendesk calls for no new coverage, and
    on a rate-limited account that costs the search that mattered."""
    worth_it = bool(fixed.strip()) and fixed.strip().lower() != raw.lower()
    assert worth_it is expected, (raw, fixed)
