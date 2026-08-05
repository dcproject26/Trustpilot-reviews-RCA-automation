"""The indicator check has to survive the trip from the checker to the card.

`test_bid_indicator_check.py` covers the checker itself. This covers the wire,
which is where the equivalent check for content families lost two mutations:
the payload key set to None, and the body_original fallback dropped. Both left
the whole suite green while the card silently stopped saying anything.

Three joints, and each fails invisibly:

  1. `_indicator_match` reading the wrong text, or none.
  2. the author and the review's own date not reaching the checker — the date
     and guest signals go quietly dead and every review reports "unchecked",
     which is a legitimate answer and so raises nothing.
  3. `"indicator_match": _indicator_match(d)` never reaching the payload.

And one more that is not a wire at all: the pipeline has to write the result
onto the confidence trail, or a check that ran and agreed is indistinguishable
from a check nobody ever wired in.
"""
from datetime import date, datetime

import pytest

import server.api as api


class _Review:
    def __init__(self, english=None, original=None, author=None, received_at=None):
        self.body_english = english
        self.body_original = original
        self.author = author
        self.received_at = received_at


class _Draft:
    """Only the attributes _indicator_match and _draft_dict actually read."""
    def __init__(self, review, booking):
        self.review = review
        self.booking = booking


PARIS = "Our morning at the Eiffel Tower was a shambles, nobody helped us."
ROME = {"id": "1", "experienceName": "Colosseum Skip-the-Line Tour",
        "date_of_visit": "2026-07-18", "primary_guest_name": "Marta Ruiz"}


# ── 1. the check runs on the text that is actually there ───────────────────

def test_an_english_body_is_checked():
    got = api._indicator_match(_Draft(_Review(english=PARIS), ROME))
    assert got["state"] == "mismatch", got


def test_a_review_with_only_an_original_body_is_still_checked():
    """The mutation that survived on the content check, transplanted. A review
    with no English translation would be read as empty text and reported as
    "unchecked" — and a review in another language is exactly where a guest is
    likeliest to have quoted the wrong reference number."""
    got = api._indicator_match(_Draft(_Review(original=PARIS), ROME))
    assert got["state"] == "mismatch", (
        f"state={got['state']!r} — the untranslated body was never read, and "
        f"the card reports that as 'we could not tell'")


def test_the_author_reaches_the_checker():
    """Drop the author and the guest signal goes permanently unchecked. It
    reports a legitimate state, so nothing anywhere goes red."""
    got = api._indicator_match(_Draft(
        _Review(english="awful", author="Marta Ruiz"), ROME))
    guest = next(s for s in got["signals"] if s["name"] == "guest")
    assert guest["state"] == "match", (
        "the review's author never reached the checker, so the guest name was "
        "compared against nothing")


def test_the_review_date_reaches_the_checker():
    """Without it, "we went in July" cannot be resolved to a year and the date
    signal is dead — silently, and on every review."""
    got = api._indicator_match(_Draft(
        _Review(english="we went in July", received_at=datetime(2026, 8, 1)), ROME))
    d = next(s for s in got["signals"] if s["name"] == "date")
    assert d["state"] == "match", d


def test_no_review_at_all_is_unchecked_not_a_crash():
    got = api._indicator_match(_Draft(None, ROME))
    assert got["state"] == "unchecked"


def test_a_broken_check_returns_unchecked_rather_than_raising(monkeypatch):
    """A hint on a card, never a gate."""
    import server.bid_indicator_check as bic
    monkeypatch.setattr(bic, "check",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    got = api._indicator_match(_Draft(_Review(english=PARIS), ROME))
    assert got["state"] == "unchecked"
    assert got["why"], "a check that did not run says nothing about why"


# ── 2. and the answer reaches the payload the dashboard reads ──────────────

def test_the_draft_payload_carries_the_indicator_match(monkeypatch):
    seen = {}

    def _fake(d):
        seen["called"] = True
        return {"state": "mismatch", "signals": [], "contradictions": ["city"],
                "agreements": [], "checked": 1, "why": "under test"}

    monkeypatch.setattr(api, "_indicator_match", _fake)

    from server.db import RcaDraft
    d = RcaDraft(id="d1", review_id="tp_1", booking=ROME)
    out = api._draft_dict(d)

    assert seen.get("called"), "_draft_dict never asks for the indicator check"
    assert "indicator_match" in out, \
        "the payload has no indicator_match key — the warning can never draw"
    assert out["indicator_match"]["state"] == "mismatch"


def test_the_payload_key_is_not_hardcoded_to_a_constant(monkeypatch):
    from server.db import RcaDraft
    d = RcaDraft(id="d1", review_id="tp_1", booking=ROME)

    monkeypatch.setattr(api, "_indicator_match", lambda _d: {"state": "match"})
    a = api._draft_dict(d)["indicator_match"]
    monkeypatch.setattr(api, "_indicator_match", lambda _d: {"state": "unchecked"})
    b = api._draft_dict(d)["indicator_match"]

    assert a != b, "the payload does not track what the checker returned"


def test_the_two_checks_are_not_the_same_answer_twice():
    """content_match compares product families; this compares the trip. A
    museum review against a museum booking in another city passes the first
    and fails the second, and a payload where both keys carry one result would
    hide exactly that case."""
    d = _Draft(_Review(english="The Louvre museum was closed when we arrived"),
               {"id": "1", "experience": "Vatican Museum Priority Entrance",
                "experienceName": "Vatican Museum Priority Entrance"})
    assert api._content_match(d)["state"] == "match", \
        "both sides are museums — the family check should be happy"
    assert api._indicator_match(d)["state"] == "mismatch", \
        "different cities — the trip check should not be"


# ── 3. the pipeline records what the check said ────────────────────────────

def test_the_pipeline_writes_the_result_onto_the_trail(live_db, monkeypatch):
    """Driven through process_review, not asserted against the source.

    The trail is where a reader finds out what ran. A check wired into the
    payload but not onto the trail is invisible in the two states that are not
    a mismatch — and "we compared nothing" then looks exactly like "we did not
    look", which is the failure this project keeps repeating.
    """
    import asyncio
    import importlib
    import server.pipeline as P
    importlib.reload(P)

    db = live_db
    s = db.SessionLocal()
    # tp_002 is a mock fixture: the Vatican Museums, in Rome. The review text
    # is about Paris, so the two contradict.
    s.add(db.Review(id="tp_002", slack_ts="2.0", slack_channel="C_MOCK_ORM",
                    rating=1, author="Mariusz",
                    body_original="Nasza wizyta w Paryzu",
                    body_english=PARIS, status="new",
                    received_at=datetime(2026, 8, 1)))
    s.commit()
    s.close()

    try:
        asyncio.run(P.process_review("tp_002"))
    except Exception:
        pass          # later steps may fail; the trail is written before them

    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == "tp_002").first()
        assert d is not None, "no draft row — the run never reached the persist"
        texts = " ".join(t.get("text", "") for t in (d.confidence_trail or []))
        assert "Indicator" in texts, (
            "the run wrote no indicator line at all: agreement, contradiction "
            "and 'nothing to compare' are all reported as silence")
        assert "Indicators disagree" in texts, (
            f"a Paris review against a Rome booking produced no contradiction "
            f"line. Trail: {texts[:400]}")
    finally:
        s.close()


def test_the_trail_line_says_so_when_there_was_nothing_to_compare(live_db):
    """The other half, and the one that decays quietly: a run whose check
    found nothing must still say the check ran."""
    import asyncio
    import importlib
    import server.pipeline as P
    importlib.reload(P)

    db = live_db
    s = db.SessionLocal()
    s.add(db.Review(id="tp_001", slack_ts="1.0", slack_channel="C_MOCK_ORM",
                    rating=1, author="Mathilde Valet",
                    body_original="Catastrophique.",
                    body_english="Absolutely terrible, never again.",
                    status="new", received_at=datetime(2026, 8, 1)))
    s.commit()
    s.close()

    try:
        asyncio.run(P.process_review("tp_001"))
    except Exception:
        pass

    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == "tp_001").first()
        texts = " ".join(t.get("text", "") for t in (d.confidence_trail or []))
        assert "Indicators could not be checked" in texts, (
            f"a review naming nothing produced no line — silence here reads as "
            f"agreement. Trail: {texts[:400]}")
    finally:
        s.close()
