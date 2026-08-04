"""Does the review describe the experience the booking is for?

"if the review is talking about a city card but the experience with the
booking id is of a guided tour, to flag it in the booking match section. this
can happen when the guest/reviewer provided incorrect info. but be careful
while adding this, it should not break the existing flow."

A guest who quotes the wrong reference number produces a match that passes
every check the pipeline makes — the id is real, the booking exists, the dates
line up — and describes a different product entirely. Every other check asks
whether the BOOKING is coherent, not whether it is the one the review is about.

"Be careful" is the load-bearing instruction, so the tests below are mostly
about NOT firing. A false flag sends an associate to re-match a correct
booking, and enough of those teach them to ignore it — at which point it is
worse than absent.

Three states, never a bool: `unchecked` is a real answer and must not be
mistaken for `match`.
"""
import pytest

from server.booking_match_check import check, family_of


def _b(exp):
    return {"id": "1", "experience": exp}


# ── it fires on a clear disagreement ───────────────────────────────────────

def test_a_city_card_review_against_a_guided_tour_booking():
    got = check("I bought the city card and it never worked", _b("Rome Guided Tour"))
    assert got["state"] == "mismatch"
    assert got["review_family"] == "city card"
    assert got["booking_family"] == "guided tour"


def test_the_reason_names_both_sides():
    got = check("our guide was late", _b("Venice City Pass"))
    assert "guided tour" in got["why"] and "city card" in got["why"]


@pytest.mark.parametrize("review,exp", [
    ("the cruise was cancelled",            "Louvre Museum Entry"),
    ("the museum was closed",               "Seine River Cruise"),
    ("our airport transfer never arrived",  "Colosseum Guided Tour"),
])
def test_other_clear_disagreements(review, exp):
    assert check(review, _b(exp))["state"] == "mismatch"


# ── it stays quiet when it should ──────────────────────────────────────────

def test_the_same_family_is_a_match():
    got = check("the guided tour was excellent", _b("Rome Guided Tour"))
    assert got["state"] == "match"


def test_a_review_naming_no_product_is_unchecked_not_a_match():
    """The distinction this returns a dict for. "We did not check" must never
    be reported as "we checked and it is fine"."""
    got = check("terrible service, very rude staff", _b("Rome Guided Tour"))
    assert got["state"] == "unchecked"
    assert "does not name a product" in got["why"]


def test_a_booking_with_no_experience_name_is_unchecked():
    got = check("the city card never worked", {"id": "1"})
    assert got["state"] == "unchecked"
    assert "no experience name" in got["why"]


def test_an_unrecognised_experience_name_is_unchecked():
    got = check("the city card never worked", _b("Bundle XYZ-4471"))
    assert got["state"] == "unchecked"


@pytest.mark.parametrize("word", ["ticket", "tickets", "entry", "admission"])
def test_generic_words_are_not_a_family(word):
    """Almost every review about almost every product says one of these. A
    family built on them would match everything and disagree with everything —
    a flag on every card, which is a flag on none."""
    assert family_of(f"my {word} did not arrive") is None


def test_a_review_mentioning_a_ticket_for_a_tour_does_not_fire():
    got = check("my tickets never arrived", _b("Rome Guided Tour"))
    assert got["state"] != "mismatch"


# ── it cannot break the existing flow ──────────────────────────────────────

@pytest.mark.parametrize("review,booking", [
    (None, None), ("", {}), (None, {"id": "1"}), ("x", None),
    ("x", "not a dict"), ("x", {"experience": None}),
])
def test_it_survives_anything_it_is_handed(review, booking):
    got = check(review, booking)
    assert got["state"] in ("match", "mismatch", "unchecked")


def test_every_answer_carries_the_same_keys():
    """The renderer reads these. A key present on one path and absent on
    another is how a card works on one review and throws on the next."""
    keys = {"state", "review_family", "booking_family", "experience", "why"}
    for r, b in [("city card", _b("Guided Tour")), ("nothing", _b("Guided Tour")),
                 ("city card", {}), ("city card", _b("Bundle XYZ"))]:
        assert set(check(r, b)) == keys, check(r, b)


def test_the_api_never_raises_and_says_so_when_it_did_not_run():
    """It is a hint on a card, never a gate. A wrong answer must not be able
    to stop a draft rendering."""
    from server.api import _content_match
    class Broken:
        booking = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        review = None
    got = _content_match(Broken())
    assert got["state"] == "unchecked"
    assert "did not run" in got["why"]
