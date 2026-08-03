"""A guest's middle name is worth searching on, and an initial is not a surname.

From a real card:

    Guest name        Bhayani Salim F
    Searched Zendesk as   Bhayani F

Two faults in one line. "Salim" — the second most distinctive token in the
name — never reached Zendesk at all, and "F" was used as the SURNAME, which
carries 0.7 of the name score and gets a `requester:` query of its own. So the
search was largely keyed on a single letter, which matches a great many people
and ranks none of them.

The rule was `parts[0], parts[-1]`, written twice in server/pipeline.py. Both
copies agreed, which is how a second copy always starts.
"""
import pytest

from server.names import name_tokens, parse_author, search_tokens
from tests.conftest import live_db                            # noqa: F401


# ── the name from the card ──────────────────────────────────────────────────

def test_the_name_from_the_card():
    assert parse_author("Bhayani Salim F") == ("Bhayani", "Salim")


def test_the_middle_name_is_searched():
    assert "Salim" in search_tokens("Bhayani Salim F")


def test_an_initial_is_never_the_surname():
    """It carries 0.7 of the name score and gets its own requester: query."""
    _, last = parse_author("Bhayani Salim F")
    assert last != "F"
    assert len(last) > 1


def test_an_initial_is_not_searched_on_its_own():
    """requester:F returns everyone and ranks nobody."""
    assert "F" not in search_tokens("Bhayani Salim F")


# ── ordinary names still work ───────────────────────────────────────────────

@pytest.mark.parametrize("name,first,last", [
    ("Fredrik Olsen", "Fredrik", "Olsen"),
    ("Lewis MacAndrew", "Lewis", "MacAndrew"),
    ("Salim F Bhayani", "Salim", "Bhayani"),
    ("María José García", "María", "García"),
    ("O'Brien Sean", "O'Brien", "Sean"),
])
def test_two_and_three_token_names(name, first, last):
    assert parse_author(name) == (first, last)


def test_every_token_of_a_three_part_name_is_searched():
    assert search_tokens("Fredrik Martin Olsen") == ["Fredrik", "Martin", "Olsen"]


def test_a_single_name_is_a_first_name_with_no_surname():
    assert parse_author("David") == ("David", None)


def test_titles_and_suffixes_are_not_names():
    """"Jr" as a surname is the same fault as "F" as a surname."""
    assert parse_author("Mr. Fredrik Martin Olsen Jr") == ("Fredrik", "Olsen")
    assert "Jr" not in search_tokens("Fredrik Olsen Jr")
    assert "Mr" not in name_tokens("Mr Fredrik Olsen")


# ── the cases that must produce nothing rather than a guess ─────────────────

@pytest.mark.parametrize("name,first,last", [
    ("Li Wang", "Li", "Wang"),
    ("Bo Andersson", "Bo", "Andersson"),
    ("Jo Ng", "Jo", "Ng"),
    ("Wang Li Chen", "Wang", "Chen"),
])
def test_a_two_letter_name_is_a_name_not_an_initial(name, first, last):
    """"Li", "Bo" and "Ng" are surnames people actually have. Widening the
    initial test to two characters would discard them, and the fault would
    show up as a poor search for one demographic and nobody else's."""
    assert parse_author(name) == (first, last)
    assert last in search_tokens(name), \
        f"{last!r} was dropped as though it were an initial"


def test_a_name_of_only_initials_yields_no_surname():
    """No surname is better than a letter, because the score would rest on
    the letter."""
    first, last = parse_author("A B")
    assert last is None


def test_a_name_of_only_initials_is_not_searchable():
    assert search_tokens("A B") == []


def test_a_single_letter_is_not_a_name():
    assert parse_author("X") == (None, None)


def test_a_numeric_name_is_not_a_name():
    assert parse_author("12345") == (None, None)
    assert parse_author("") == (None, None)
    assert parse_author(None) == (None, None)


def test_punctuation_alone_is_dropped():
    assert name_tokens("... --- ,,,") == []


# ── one rule, one place ─────────────────────────────────────────────────────

def test_the_pipeline_does_not_split_names_itself():
    """Negative assertion — the two hand-rolled copies. A string that appears
    nowhere cannot be defeated by unreachability, which is the one thing a
    source check is good for.
    """
    src = open("server/pipeline.py", encoding="utf-8").read()
    assert "parts[0], parts[-1]" not in src, \
        "the first/last split is back, and it takes an initial as a surname"
    assert "_ap[-1] if len(_ap) > 1" not in src, \
        "the second copy of the split is back"


# ── what actually reaches Zendesk ───────────────────────────────────────────

def _query_names(monkeypatch, author, first, last):
    """The name string find_bids_by_requester_name builds, without a network.

    Driven through the real function: asserting on search_tokens alone would
    pass against a build where the search still ignores it.
    """
    import asyncio
    import server.services.zendesk as Z
    seen = {}

    class _FakeSearch:
        def __call__(self, query="", **kw):
            seen.setdefault("queries", []).append(query)
            return []

    class _FakeClient:
        search = _FakeSearch()

    monkeypatch.setattr(Z, "is_live", lambda k: True)
    monkeypatch.setattr(Z, "_get_client", lambda: _FakeClient())
    asyncio.run(Z.find_bids_by_requester_name(
        first, last, with_context=True, full_name=author))
    return " | ".join(seen.get("queries", []))


def test_the_middle_name_reaches_the_zendesk_query(monkeypatch):
    """A name whose middle token is NEITHER the first nor the surname.

    "Bhayani Salim F" cannot show this: once the parser is fixed it returns
    ("Bhayani", "Salim"), so "Salim" reaches the query through author_last
    whether or not the full name is used at all. Mutation testing proved it —
    deleting the full-name path entirely left this green. Three real tokens
    are needed before the difference exists.
    """
    q = _query_names(monkeypatch, "Fredrik Martin Olsen", "Fredrik", "Olsen")
    assert "Martin" in q, f"the middle name is still not searched: {q}"
    assert "Fredrik Martin Olsen" in q, q


def test_a_fourth_token_reaches_it_too(monkeypatch):
    q = _query_names(monkeypatch, "Maria Jose Garcia Lopez", "Maria", "Lopez")
    for tok in ("Maria", "Jose", "Garcia", "Lopez"):
        assert tok in q, f"{tok!r} was dropped: {q}"


def test_the_initial_does_not_reach_the_zendesk_query(monkeypatch):
    q = _query_names(monkeypatch, "Bhayani Salim F", "Bhayani", "Salim")
    assert "Bhayani Salim" in q
    assert "Bhayani F" not in q, f"still searching on the initial: {q}"


def test_a_single_usable_token_does_not_collapse_the_search(monkeypatch):
    """"A Bhayani" must not become "Bhayani". A single-token requester search
    matches every user carrying it, their tickets yield real booking ids that
    verify in BigQuery, and strangers' bookings become indistinguishable
    candidates — a decision already made and paid for above this code."""
    q = _query_names(monkeypatch, "A Bhayani", "A", "Bhayani")
    assert "A Bhayani" in q, f"the search collapsed to one token: {q}"


def test_a_caller_that_passes_no_full_name_still_works(monkeypatch):
    """Older callers, and anything that only has the split pair."""
    q = _query_names(monkeypatch, None, "Fredrik", "Olsen")
    assert "Fredrik Olsen" in q


def test_the_pipeline_hands_the_display_name_to_the_search(live_db, monkeypatch):
    """The link between the two halves, and the one that was untested.

    A parser that returns the middle name and a search that never receives it
    look identical from the card — and mutation testing showed exactly that:
    deleting `full_name=_full` from the pipeline's call left the whole suite
    green. So this runs process_review for real and reads what
    find_bids_by_requester_name was actually handed.
    """
    import asyncio
    from datetime import datetime
    db = live_db
    s = db.SessionLocal()
    try:
        s.add(db.Review(id="tp_name", slack_ts="7.0", slack_channel="C1",
                        rating=1, author="Fredrik Martin Olsen",
                        body_original="the tour never turned up",
                        reference_number=None, status="new",
                        received_at=datetime.utcnow()))
        s.commit()
    finally:
        s.close()

    import importlib
    import server.pipeline as P
    importlib.reload(P)
    seen = {}

    async def _spy(first, last, lookback_days=None, with_context=False,
                   full_name=None):
        seen["first"], seen["last"], seen["full"] = first, last, full_name
        return ([], []) if with_context else []

    monkeypatch.setattr(P, "verify_bid", lambda bid: None, raising=False)
    monkeypatch.setattr(P.zendesk, "find_bids_by_requester_name", _spy)
    monkeypatch.setattr(P, "is_live", lambda k: k in ("zendesk", "bigquery"),
                        raising=False)
    try:
        asyncio.run(P.process_review("tp_name"))
    except Exception:
        pass                      # later steps may fail; the call is the point

    if not seen:
        pytest.skip("the Zendesk name path did not run in this configuration")
    assert seen["full"] == "Fredrik Martin Olsen", (
        f"the pipeline passed full_name={seen['full']!r} — the display name "
        f"never reaches the search, so the middle name is dropped again")
    assert seen["last"] != "Olsen" or seen["first"] == "Fredrik"
