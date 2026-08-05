"""The match trail must not contradict the card it sits on.

One review, BID 33204378, produced all of this at once:

    T1  BID from the review — not verified in BigQuery
    !   Weak BID — number found in text, but booking guest
        'FjpJxbSfpb65bny/…' scores 0.0 and no venue match (date only).
    ✓   Indicators: venue='—' · city='—' · visit≈'—'
    ✓   Author parsed: first='Ioan' last='None'
    ✓   5 booking(s) match the indicators from this review (name).
    !   BID 33204378 taken from the review (regex) but NOT verified — BigQuery
        did not return this booking.

…beside a Booking details panel showing the booking: Pena Palace & Park
Tickets, TGID 11899, vendor Parques de Sintra, visit 2026-08-04. So BigQuery
had returned it, twice over, while two lines said it had not.

Five faults, four of them fixable without touching how matching behaves:

  * the floor line and the "not verified" tier label — already dead at HEAD,
    asserted here so they stay dead;
  * "Weak BID — number found in text" reading as "the id went nowhere", when
    what happened is that it resolved to a booking that disagrees with the
    review. Different fact, different response;
  * `last='None'` — Python's None in a sentence a person reads;
  * a green tick on an extraction that found nothing, and the word
    "indicators" meaning the extraction on one line and the search on the next.

The fifth — the PII hash printed as a guest name — is deliberately untouched
and raised with the user: dropping it reads better, keeping it lets someone
confirm the comparison actually ran.
"""
from datetime import date, datetime

import pytest

from server.pipeline import complete_booking_row


# ── the floor cannot fire while there are candidates to pick ───────────────

def test_the_unverified_floor_is_dead_when_candidates_exist():
    """The two lines that contradicted each other came from the floor firing
    over a review that had a picker open. Driven through the tier rule that
    decides what the card claims."""
    from server.tiers import classify, is_unverified

    class _R:
        status = "draft"

    class _D:
        candidate_state = True
        selected_candidate_bid = None
        candidates_list = [{"id": "1"}]
        booking = {}

    assert classify(_R(), _D()) == "candidates"
    assert is_unverified(_D()) is False, (
        "the card would still label this 'BID from the review — not verified "
        "in BigQuery' over a list of five real bookings")


def test_unverified_is_only_ever_the_floor_marker():
    from server.tiers import is_unverified

    class _D:
        booking = {"id": "1", "_unverified": True}

    assert is_unverified(_D()) is True
    _D.booking = {"id": "1"}
    assert is_unverified(_D()) is False


# ── the booking row is completed, and says so when it is not ──────────────

NARROW = {"id": "33204378", "experienceName": "Pena Palace & Park Tickets",
          "tgid": "11899", "tid": "22782", "vendorName": "Parques",
          "date_of_visit": "2026-08-04", "matchReasons": ["venue", "date"],
          "narrowing_path": "venue_date_30_auto"}
FULL = {"id": "33204378", "experienceName": "Pena Palace & Park Tickets",
        "date_of_booking": "2026-07-30 11:02:00", "date_of_visit": "2026-08-04",
        "fulfilmentType": "AUTOMATED", "vendorName": "Parques de Sintra",
        "primary_guest_name": "Customer Ops Lead"}


def test_the_fields_the_matching_query_never_selected_are_filled():
    """Fulfilment type, booking date and lead time were "—" on a booking that
    has all three. The narrowing query does not select them; nobody asked."""
    got, _ = complete_booking_row(NARROW, lambda bid: FULL)
    assert got["date_of_booking"] == "2026-07-30 11:02:00"
    assert got["fulfilmentType"] == "AUTOMATED"


def test_the_match_path_keeps_what_it_decided():
    """Matching chose this booking and carries things the warehouse row does
    not know about. The merge fills gaps; it does not overrule."""
    got, _ = complete_booking_row(NARROW, lambda bid: FULL)
    assert got["matchReasons"] == ["venue", "date"]
    assert got["narrowing_path"] == "venue_date_30_auto"
    assert got["vendorName"] == "Parques", \
        "the warehouse overwrote a value the match path had already resolved"


def test_a_lookup_that_returns_nothing_does_not_claim_the_booking_is_empty():
    """"BigQuery did not return this booking" was printed beside a booking
    BigQuery had returned. Whatever this says, it must not say that."""
    _, entry = complete_booking_row(NARROW, lambda bid: None)
    assert entry["mark"] == "warn"
    assert "not because the booking has none" in entry["text"]
    assert "did not return this booking" not in entry["text"]


def test_a_lookup_that_raises_says_it_raised():
    """An exception turned into "not found" is how a false sentence got onto
    the card in the first place."""
    _, entry = complete_booking_row(
        NARROW, lambda bid: (_ for _ in ()).throw(RuntimeError("boom")))
    assert entry["mark"] == "warn"
    assert "RuntimeError" in entry["text"]
    assert "did not fetch it" in entry["text"]


def test_the_three_outcomes_read_differently():
    a = complete_booking_row(NARROW, lambda b: FULL)[1]["text"]
    b = complete_booking_row(NARROW, lambda b: None)[1]["text"]
    c = complete_booking_row(
        NARROW, lambda b: (_ for _ in ()).throw(ValueError()))[1]["text"]
    assert len({a, b, c}) == 3


def test_a_complete_row_reports_nothing():
    """A line saying "nothing needed completing" on every card is furniture.
    The panel itself shows the fields."""
    got, entry = complete_booking_row(FULL, lambda bid: FULL)
    assert entry is None
    assert got["fulfilmentType"] == "AUTOMATED"


def test_a_booking_with_no_id_is_left_alone():
    assert complete_booking_row({}, lambda bid: FULL) == ({}, None)
    assert complete_booking_row(None, lambda bid: FULL) == (None, None)


# ── the wording, driven through the pipeline that writes it ───────────────

def _trail_of(rid, db):
    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == rid).first()
        return " ".join(t.get("text", "") for t in ((d and d.confidence_trail) or []))
    finally:
        s.close()


def _no_model(P, monkeypatch):
    """No Anthropic key in CI, and the indicator call is reached by any review
    with no mock booking. The call is stubbed to return an EMPTY extraction,
    which is the case these tests are about."""
    async def _call(*a, **k):
        return '{"experience_or_venue": null, "city_or_country": null, '\
               '"visit_date_hint": null, "guest_name": null, "pax": null, '\
               '"issue_terms": []}'
    monkeypatch.setattr(P.claude, "_call", _call)
    # Author parsing and indicator extraction live inside the LIVE branch;
    # with BigQuery reported dead the run takes the mock path and neither line
    # is ever written. Every warehouse call below is already wrapped, so a
    # "live" BigQuery that then fails on every query exercises exactly the
    # path these sentences are written on.
    monkeypatch.setattr(P, "is_live", lambda name: name == "bigquery")


def _run(db, rid, monkeypatch, **review_kw):
    import asyncio
    import importlib
    import server.pipeline as P
    importlib.reload(P)
    _no_model(P, monkeypatch)
    s = db.SessionLocal()
    kw = {"slack_channel": "C_MOCK_ORM", "rating": 1, "status": "new",
          "received_at": datetime(2026, 8, 1)}
    kw.update(review_kw)
    s.add(db.Review(id=rid, slack_ts=rid, **kw))
    s.commit()
    s.close()
    try:
        asyncio.run(P.process_review(rid))
    except Exception:
        pass
    return _trail_of(rid, db)


def test_a_surname_we_do_not_have_is_not_the_word_None(live_db, monkeypatch):
    """`last='None'` was on screen. Python's None in a sentence an associate
    reads is a value that looks like data and is not."""
    trail = _run(live_db, "tp_n1", monkeypatch, author="Ioan",
                 body_original="Terrible experience, nobody helped.")
    assert "last='None'" not in trail, trail[:500]
    assert "Author parsed" in trail, "the step did not run at all"
    assert "no surname in the display name" in trail


def test_a_surname_we_do_have_is_still_printed(live_db, monkeypatch):
    trail = _run(live_db, "tp_n2", monkeypatch, author="Ioan Popescu",
                 body_original="Terrible experience, nobody helped.")
    assert "last='Popescu'" in trail


def test_an_extraction_that_found_nothing_is_not_a_green_tick(live_db, monkeypatch):
    """A ✓ on "venue='—' · city='—' · visit≈'—'" ticks a step that found
    nothing. Nothing found is a finding; it is what sends the search down the
    weakest path it has."""
    import server.pipeline as P
    db = live_db
    s = db.SessionLocal()
    s.add(db.Review(id="tp_n3", slack_ts="tp_n3", slack_channel="C_MOCK_ORM",
                    rating=1, author="Ioan", body_original="Terrible.",
                    status="new", received_at=datetime(2026, 8, 1)))
    s.commit()
    s.close()

    marks = {}
    import asyncio
    import importlib
    importlib.reload(P)
    _no_model(P, monkeypatch)
    try:
        asyncio.run(P.process_review("tp_n3"))
    except Exception:
        pass
    s = db.SessionLocal()
    d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == "tp_n3").first()
    for step in (d.confidence_trail or []):
        if "Extracted from review" in step.get("text", ""):
            marks[step["text"]] = step["mark"]
    s.close()
    assert marks, "the extraction step no longer writes a line at all"
    for text, mark in marks.items():
        if "venue='—'" in text and "city='—'" in text and "visit≈'—'" in text:
            assert mark == "warn", f"an empty extraction is ticked as a pass: {text}"
            assert "nothing usable was found" in text


def test_extraction_and_search_do_not_share_the_word_indicators():
    """Two adjacent lines used "indicators" for two different things: what was
    pulled OUT of the review, and what bookings were matched ON."""
    src = open("server/pipeline.py", encoding="utf-8").read()
    # A NEGATIVE assertion, which CLAUDE.md allows: unreachability cannot
    # defeat "this string appears nowhere".
    assert '"<strong>Indicators:</strong> "' not in src, (
        "the extraction step is headed 'Indicators:' again, one line above "
        "'match the indicators from this review'")


def test_the_weak_bid_line_says_the_booking_was_returned():
    """"Weak BID — number found in text" reads as though the id went nowhere.
    BigQuery returned a booking for it; we scored that booking and it
    disagrees. The reader cannot act on those two the same way."""
    src = open("server/pipeline.py", encoding="utf-8").read()
    assert "<strong>Weak BID</strong> — number found in text" not in src
    assert "booking that does not match this review" in src
