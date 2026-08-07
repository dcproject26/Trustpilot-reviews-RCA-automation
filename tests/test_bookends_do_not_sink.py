""""Booking created" rendered at the BOTTOM of the timeline.

MEASURED with scripts/trace_shaping.py on booking 32885089:

    unknown   Booking created   <-- NO READABLE TIME, sinks to the end
    unknown   Review posted     <-- NO READABLE TIME, sinks to the end

A bookend carries no `idx_range`, so there is no raw event to copy a time
from, and the model answered "unknown" for both. The client sorts on the
displayed time and sinks a row it cannot read to the END — so the booking's
creation rendered under the review it precedes by two weeks.

On screen that is indistinguishable from an event that happened last, which is
why it read as the chronology being broken rather than as two rows missing a
value.

Both dates are in hand. A bookend is first or last BY DEFINITION; asking a
model to remember which is a question that did not need asking.
"""
import asyncio
import types

import pytest

from server.services import zendesk as zd


def _shape(monkeypatch, answer, booking=None, pub=""):
    async def _fake(prompt):
        return answer
    monkeypatch.setattr(zd, "_safe_parse_events", zd._safe_parse_events)
    import server.services.claude as claude
    monkeypatch.setattr(claude, "shape_timeline_events", _fake)
    return asyncio.run(zd._shape_via_claude(
        [{"idx": 0, "time": "21 Jul 15:28 IST", "time_sort": "2026-07-21T09:58:00",
          "thread": "email", "actor": "system", "ticket_id": "1",
          "raw_body": "Booking confirmed"}],
        booking or {}, "review body", pub))


ANSWER = """[
 {"idx_range": [], "thread": "booking", "actor": "creation",
  "time": "unknown", "label": "Booking created", "summary": "", "keep": true},
 {"idx_range": [0], "thread": "email", "actor": "system",
  "time": "21 Jul 15:28 IST", "label": "Booking confirmed", "summary": "x", "keep": true},
 {"idx_range": [], "thread": "review", "actor": "review",
  "time": "unknown", "label": "Review posted", "summary": "", "keep": true}]"""


def test_the_booking_bookend_is_stamped_from_the_booking_record(monkeypatch):
    rows = _shape(monkeypatch, ANSWER,
                  booking={"creationDate": "2026-07-21 15:28"})
    created = next(r for r in rows if r["label"] == "Booking created")
    assert created["time_sort"], "the booking bookend still has no sort value"


def test_the_review_bookend_is_stamped_from_the_publication_date(monkeypatch):
    rows = _shape(monkeypatch, ANSWER, pub="2026-08-05 04:56")
    posted = next(r for r in rows if r["label"] == "Review posted")
    assert posted["time_sort"], "the review bookend still has no sort value"


def test_a_stamped_bookend_no_longer_sinks(monkeypatch):
    """The consequence, which is the whole reported symptom: with a sort value
    the booking's creation sorts ABOVE the event that follows it."""
    # The raw event is 09:58 UTC. `_normalize_time` reads a bare stamp as UTC
    # and displays IST, so the creation date has to be earlier in UTC terms
    # for the assertion to mean what it says.
    rows = _shape(monkeypatch, ANSWER,
                  booking={"creationDate": "2026-07-21 08:00"},
                  pub="2026-08-05 04:56")
    by = {r["label"]: r["time_sort"] for r in rows}
    assert by["Booking created"] < by["Booking confirmed"], by
    assert by["Review posted"] > by["Booking confirmed"], by


def test_a_bookend_the_model_DID_time_is_left_alone(monkeypatch):
    """The stamp is a fallback. A model that echoed a real time correctly must
    not have it overwritten by the booking record."""
    answer = ANSWER.replace(
        '"time": "unknown", "label": "Booking created"',
        '"time": "20 Jul 09:00 IST", "label": "Booking created"')
    rows = _shape(monkeypatch, answer, booking={"creationDate": "2026-07-21 08:00"})
    created = next(r for r in rows if r["label"] == "Booking created")
    assert "20 Jul" in created["time"], created


def test_no_date_in_the_record_does_not_invent_one(monkeypatch):
    """Nothing to stamp from is a real state. Inventing a time would put a row
    in a place the records do not support, which is worse than an undated row
    a reader can see is undated."""
    rows = _shape(monkeypatch, ANSWER, booking={}, pub="")
    created = next(r for r in rows if r["label"] == "Booking created")
    assert not created["time_sort"], created


# ── the publication date reaches the model in the same timezone as the rest ─

def test_the_review_date_is_converted_before_the_model_sees_it(monkeypatch):
    """It arrives as UTC and every raw event in the same prompt is an IST
    display string. The model copied the digits and labelled them IST, so a
    review published 12:06 UTC rendered "02 Aug 12:06 IST" instead of 17:36 —
    and sorted BEFORE the escalation and the guest chat that preceded it."""
    seen = {}

    async def _fake(prompt):
        seen["prompt"] = prompt
        return ANSWER
    import server.services.claude as claude
    monkeypatch.setattr(claude, "shape_timeline_events", _fake)
    asyncio.run(zd._shape_via_claude([], {}, "body", "2026-08-02 12:06"))
    assert "02 Aug 17:36 IST" in seen["prompt"], \
        "the model is still shown the raw UTC publication date"
    assert "2026-08-02 12:06" not in seen["prompt"], seen["prompt"][-400:]


def test_a_publication_date_that_cannot_be_parsed_is_passed_through(monkeypatch):
    """Converting nothing must not blank the value — the model still needs
    whatever we have for the review bookend."""
    seen = {}

    async def _fake(prompt):
        seen["prompt"] = prompt
        return ANSWER
    import server.services.claude as claude
    monkeypatch.setattr(claude, "shape_timeline_events", _fake)
    asyncio.run(zd._shape_via_claude([], {}, "body", "sometime on Tuesday"))
    assert "sometime on Tuesday" in seen["prompt"]
