"""Venue extraction worked; venue → TGID resolution did not.

THE REPORTED CARD, for a review about the Colosseum in Rome:

    EXTRACTED FROM REVIEW
      Venue / experience   premo tickets for collosseum
      City / country       Rome, Italy
      Venue hints used     premo tickets for collosseum · Rome, Italy
    !  Venues extracted but no TGIDs resolved
    ⚠ No venue agreement — ranked by visit-date proximity only
    #32183810  Rulantica Water Park Entry Tickets · Europa-Park Resort
    #30086155  One World Observatory Standard Tickets
    #32607226  Borghese Gallery Entry Tickets

A German water park and a New York observatory offered for a Colosseum
review. Three causes, all real:

  a. THE HINT IS A PHRASE, NOT A VENUE. The resolver matched the whole string
     against experience_name with LIKE '%...%'. Guests write sentences, not
     catalogue entries, so "premo tickets for collosseum" could never match.
  b. THE VENUE IS MISSPELLED. Exact matching will keep failing on this.
  c. THE CITY WAS PASSED AS A VENUE. "Rome, Italy" went into venue_hints
     beside the venue, guaranteeing a miss and then being reported as one of
     the "venues extracted".

And a fourth thing, which is what actually reached the associate:

  d. WITH NO VENUE RESOLVED, DATE PROXIMITY IS NOT A SHORTLIST. It is noise
     with a ranking on it.
"""
import pytest

from server.services.venue_resolver import venue_tokens, fuzzy_budget
from server.pipeline import candidates_are_noise


# ── (a) the phrase is broken into words that could name a venue ────────────

def test_the_venue_token_survives_its_filler():
    assert venue_tokens("premo tickets for collosseum") == ["collosseum"], (
        "the venue is still buried in the phrase, so LIKE '%phrase%' will "
        "keep matching nothing")


def test_generic_travel_words_are_not_venues():
    """A hint resolved on "tickets" matches the entire catalogue, which is the
    same as matching none — except that it looks like a hit."""
    assert venue_tokens("skip the line tickets") == []
    assert venue_tokens("premium guided tour entry") == []


def test_a_real_venue_name_still_resolves_to_itself():
    """The control. A filter that dropped everything would pass every test
    above and break every working lookup."""
    assert "colosseum" in venue_tokens("Colosseum Guided Tour")
    assert "sagrada" in venue_tokens("Sagrada Familia skip the line")


def test_short_words_are_dropped():
    """Three or four characters inside a LIKE '%..%' matches by accident."""
    assert venue_tokens("zoo spa bar") == []


def test_the_longest_token_comes_first():
    """The most specific word is the one most likely to be the venue."""
    toks = venue_tokens("Louvre Museum Paris entry")
    assert toks and toks[0] == "louvre", toks


def test_tokens_are_deduplicated():
    assert venue_tokens("Colosseum colosseum COLOSSEUM") == ["colosseum"]


def test_an_empty_or_missing_hint_yields_nothing():
    for h in ("", None, "   ", ",,,"):
        assert venue_tokens(h) == []


# ── (b) a bounded tolerance for misspellings ───────────────────────────────

def test_a_long_misspelled_venue_gets_a_budget():
    """"collosseum" for "colosseum" is one insertion. Guests do this
    constantly and exact matching will keep missing them."""
    assert fuzzy_budget("collosseum") >= 1


def test_short_words_get_no_tolerance_at_all():
    """A LOOSE VENUE MATCH IS WORSE THAN NONE — it produces a confident wrong
    booking instead of admitting defeat. At five characters an edit distance
    of two reaches a large part of the dictionary: rome / roma / rope / role.
    """
    for w in ("italy", "paris", "rome", "spa", "museum"):
        assert fuzzy_budget(w) == 0, f"{w!r} was given a spelling budget"


def test_the_budget_never_exceeds_a_quarter_of_the_word():
    """The tolerance grows with the evidence, not with our optimism."""
    for w in ("colosseum", "collosseum", "sagradafamilia", "rijksmuseum"):
        assert fuzzy_budget(w) <= max(1, len(w) // 4)
        assert fuzzy_budget(w) <= 2


# ── (d) a date-only shortlist is withheld ──────────────────────────────────

def _c(**kw):
    base = {"id": "1", "score_venue": 0.0, "score_name": 0.0,
            "score_ticket": 0.0, "venue_signal": False}
    base.update(kw)
    return base


def test_the_reported_shortlist_is_noise():
    """Three bookings, nothing agreeing but the date."""
    assert candidates_are_noise([_c(id="32183810"), _c(id="30086155"),
                                 _c(id="32607226")]) is True


def test_one_venue_agreement_saves_the_whole_list():
    """Weak is not the same as baseless. A real signal keeps the list, or the
    guard would hide matches that are simply hard."""
    assert candidates_are_noise([_c(), _c(venue_signal=True)]) is False
    assert candidates_are_noise([_c(), _c(score_venue=1.5)]) is False


def test_a_name_or_ticket_agreement_also_saves_it():
    assert candidates_are_noise([_c(score_name=3.0)]) is False
    assert candidates_are_noise([_c(score_ticket=1.0)]) is False


def test_an_empty_list_is_not_noise():
    """Nothing to withhold is a different fact from a list withheld, and the
    trail line must not fire for it."""
    assert candidates_are_noise([]) is False
    assert candidates_are_noise(None) is False


def test_candidates_with_no_scores_at_all_are_left_alone():
    """A path that recorded no sub-scores cannot be SHOWN to be noise, and an
    unproven claim is not grounds for dropping somebody's only lead."""
    assert candidates_are_noise([{"id": "1", "experience": "x"}]) is False


def test_malformed_rows_do_not_raise():
    """It runs on every unmatched review; an exception here would take the
    whole run down for a shape nobody anticipated."""
    assert candidates_are_noise([None, "x", 7]) is False


# ── (c) the city is not a venue ────────────────────────────────────────────

def test_the_city_is_not_passed_to_the_venue_resolver():
    """NEGATIVE source assertion, which CLAUDE.md permits: unreachability
    cannot defeat "this string appears nowhere". The behaviour is a two-line
    list literal with no seam to drive, and the defect was that a second entry
    existed in it at all.
    """
    import pathlib
    src = pathlib.Path("server/pipeline.py").read_text(encoding="utf-8")
    i = src.find("venue_hints = [h for h in (")
    assert i > 0, "the venue_hints construction moved — re-anchor this test"
    block = src[i:i + 260]
    assert "city_or_country" not in block, (
        "the city is still being passed to the venue resolver: no experience "
        "is named 'Rome, Italy', so it guarantees a miss and is then reported "
        "as one of the venues we extracted")
    assert "experience_or_venue" in block, (
        "the venue itself is no longer being passed either")
