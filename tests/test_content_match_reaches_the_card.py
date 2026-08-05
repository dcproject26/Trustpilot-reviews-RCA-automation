"""The content check has to survive the trip from the checker to the card.

Two mutations survived the 68-mutation pass, and both are here. Neither is a
defect in behaviour — the code is right. Nothing proved it stays right, which
is the same thing one refactor later.

  1. `"content_match": _content_match(d)` mutated to `None` in `_draft_dict`.
     The whole suite stayed green. The mismatch row would silently stop
     rendering and the card would look entirely normal — every other field
     present, nothing to suggest a check had been dropped.

  2. The `body_original` fallback removed. A review with no English
     translation would be read as empty text, so `review_family` comes back
     None and the state is "unchecked" — reported as "we could not tell"
     when the truth is "we looked at nothing". Reviews in other languages are
     exactly the ones where a guest is most likely to quote a wrong booking
     reference.

`tests/test_content_match.py` covers the checker itself and
`test_content_match_ui.py` covers what the card draws with each state. This
file covers the wire between them, which was the part nothing touched.
"""
import pytest

import server.api as api


class _Review:
    def __init__(self, english=None, original=None):
        self.body_english = english
        self.body_original = original


class _Draft:
    """Only the attributes _content_match and _draft_dict actually read."""
    def __init__(self, review, booking):
        self.review = review
        self.booking = booking


CITY_CARD = "The city card never worked at any attraction, we were turned away."
GUIDED = {"experienceName": "Guided walking tour of the Colosseum"}


# ── 1. the check runs on the text that is actually there ───────────────────

def test_an_english_body_is_checked():
    got = api._content_match(_Draft(_Review(english=CITY_CARD), GUIDED))
    assert got["state"] == "mismatch", got
    assert got["review_family"] == "city card"


def test_a_review_with_only_an_original_body_is_still_checked():
    """The surviving mutation. Drop the fallback and this review reads as
    "unchecked" — indistinguishable from one we genuinely could not classify,
    on precisely the reviews where a wrong booking reference is likeliest."""
    got = api._content_match(_Draft(_Review(original=CITY_CARD), GUIDED))
    assert got["state"] == "mismatch", (
        f"state={got['state']!r} — a review with no English translation was "
        f"never read, and the card reports that as 'we could not tell'")
    assert got["review_family"] == "city card"


def test_the_english_body_wins_when_both_are_present():
    """Both exist on most rows. The order has to be deterministic or two runs
    on one review disagree."""
    got = api._content_match(_Draft(
        _Review(english=CITY_CARD, original="Il tour guidato era ottimo"), GUIDED))
    assert got["review_family"] == "city card"


def test_no_review_at_all_is_unchecked_not_a_crash():
    got = api._content_match(_Draft(None, GUIDED))
    assert got["state"] == "unchecked"


def test_a_broken_check_returns_unchecked_rather_than_raising(monkeypatch):
    """It is a hint on a card, never a gate. A wrong answer here must not be
    able to stop a draft rendering."""
    import server.booking_match_check as bmc
    monkeypatch.setattr(bmc, "check",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    got = api._content_match(_Draft(_Review(english=CITY_CARD), GUIDED))
    assert got["state"] == "unchecked"
    assert got["why"], "a check that did not run says nothing about why"


# ── 2. and the answer reaches the payload the dashboard reads ──────────────

def test_the_draft_payload_carries_the_content_match(monkeypatch):
    """The other surviving mutation: `content_match` set to None in
    `_draft_dict` left every test green. The dashboard reads this key and
    draws nothing when it is absent, so the row vanishes with no other
    symptom."""
    seen = {}

    def _fake(d):
        seen["called"] = True
        return {"state": "mismatch", "review_family": "city card",
                "booking_family": "guided tour", "experience": "X",
                "why": "under test"}

    monkeypatch.setattr(api, "_content_match", _fake)

    from server.db import RcaDraft
    d = RcaDraft(id="d1", review_id="tp_1", booking=GUIDED)
    out = api._draft_dict(d)

    assert seen.get("called"), "_draft_dict never asks for the content check"
    assert "content_match" in out, \
        "the payload has no content_match key — the mismatch row can never draw"
    assert out["content_match"]["state"] == "mismatch", out["content_match"]


def test_the_payload_key_is_not_hardcoded_to_a_constant(monkeypatch):
    """Asserting one value would pass against `"content_match": {...}` written
    inline. Two different answers must produce two different payloads."""
    from server.db import RcaDraft
    d = RcaDraft(id="d1", review_id="tp_1", booking=GUIDED)

    monkeypatch.setattr(api, "_content_match", lambda _d: {"state": "match"})
    a = api._draft_dict(d)["content_match"]
    monkeypatch.setattr(api, "_content_match", lambda _d: {"state": "unchecked"})
    b = api._draft_dict(d)["content_match"]

    assert a != b, "the payload does not track what the checker returned"
