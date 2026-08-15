"""The picker shows a name you can recognise and a score you can trust.

Both defects were on the ONE screen where an associate decides which booking
is right, and both made that decision harder in a way that looked like the
matching being wrong.
"""
import pytest

from server.api import _looks_like_hash, _scrub_candidate_names
from tests.conftest import read_source

B64 = "jVwe+fjfm48WSok1xEK+I/8fnIoV+kY8P8z7xxk+NM8="
HEX = "a3f9c2e7b1d84f60a9c2"


# ── the name ───────────────────────────────────────────────────────────────

def test_a_base64_digest_is_recognised_as_a_hash():
    """Hex was the whole test, and the warehouse also stores base64 digests —
    so every one of them rendered as the guest's name. Base64 is never hex, so
    no widening of the hex alphabet would have caught it."""
    assert _looks_like_hash(B64)


def test_a_hex_digest_still_is():
    assert _looks_like_hash(HEX)


@pytest.mark.parametrize("name", [
    "Evelyn van Dijk-Schoe", "Steve Reeves", "Roisin Sheehy",
    "Christopherson",            # long and unspaced, and still a name
    "Jean-Luc", "Li", "",
])
def test_a_real_name_is_not_mistaken_for_one(name):
    """A dedupe that eats a real name is worse than showing a digest: the
    associate loses the only field they can recognise the booking by."""
    assert not _looks_like_hash(name), name


def test_the_zendesk_name_is_used_where_the_warehouse_holds_a_digest():
    """That copy exists on the shortlist for exactly this case."""
    got = _scrub_candidate_names([
        {"id": "1", "primary_guest_name": B64, "zendesk_guest_name": "Steve Reeves"}])
    assert got[0]["primary_guest_name"] == "Steve Reeves", got


def test_an_unrecoverable_digest_leaves_the_slot_EMPTY():
    """Blank says "we have no name". A digest says nothing at all, and looks
    like data."""
    got = _scrub_candidate_names([{"id": "2", "primary_guest_name": B64}])
    assert got[0]["primary_guest_name"] == "", got


def test_a_readable_name_is_left_alone():
    got = _scrub_candidate_names([
        {"id": "3", "primary_guest_name": "Evelyn van Dijk-Schoe"}])
    assert got[0]["primary_guest_name"] == "Evelyn van Dijk-Schoe"


def test_a_zendesk_name_that_is_ALSO_a_digest_does_not_get_substituted():
    got = _scrub_candidate_names([
        {"id": "4", "primary_guest_name": B64, "zendesk_guest_name": HEX}])
    assert got[0]["primary_guest_name"] == "", got


def test_it_does_not_raise_on_junk():
    for bad in (None, [], [None], ["nope"], [{}]):
        _scrub_candidate_names(bad)


def test_the_serve_path_actually_scrubs():
    """A scrubber wired into no path looks exactly like one that works."""
    import inspect
    import server.api as api
    src = inspect.getsource(api)
    assert '"candidates_list":    _scrub_candidate_names(' in src, \
        "the picker is served unscrubbed candidates"


# ── the score label ────────────────────────────────────────────────────────

def test_the_client_carries_every_score_term():
    """CLIENT-SIDE JS with no harness — a source assertion, and CLAUDE.md's
    stated exception. The remap builds a fixed shape and drops anything not
    named in it; it carried venue and date only, so the card printed two terms
    of a five-term sum and the order looked inverted."""
    src = read_source("client/index.html")
    i = src.index("r.candidatesList = draft.candidates_list.map(c => ({")
    remap = src[i:src.index("}));", i)]   # to the end of the block, not a fixed slice
    for field in ("score_ticket", "score_both", "score_name"):
        assert field in remap, f"the remap drops {field}, so the label lies"


def test_the_two_term_label_is_gone():
    """NEGATIVE assertion — unreachability cannot defeat it."""
    src = read_source("client/index.html")
    assert "venue ${c.scoreVenue} + date ${c.scoreDate}" not in src


def test_the_banner_no_longer_claims_date_only_ranking():
    """It said "Ranked by visit-date proximity only" while the sort was the
    full five-term score. By the rule the banner claimed, the ordering on
    screen genuinely was wrong."""
    src = read_source("client/index.html")
    assert "Ranked by visit-date proximity only" not in src
    assert "Ranked on the whole match score" in src


def test_the_score_helper_names_all_five_terms():
    """CLIENT-SIDE JS source assertion, the sanctioned exception.

    Checking the REMAP is not enough: a term carried into the client and then
    left out of the label is the same lie by a different route, and a mutation
    deleting one from the helper survived the rest of this file. The ranking
    is pipeline._score = venue + date + ticket + both + name; the label has to
    be able to name each one."""
    src = read_source("client/index.html")
    i = src.index("function _candScore(c) {")
    helper = src[i:src.index("\nfunction ", i + 10)]
    for term in ("scoreVenue", "scoreDate", "scoreTicket", "scoreBoth",
                 "scoreName"):
        assert term in helper, f"_candScore cannot show {term}"
