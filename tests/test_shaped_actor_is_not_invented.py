"""The summariser does not get to decide who acted.

WHAT SHIPPED. `_ACTORS` contains "guest", so the only check on a shaped actor —
"is it a known actor" — waves through a model that has relabelled vendor and
internal traffic as the guest. On one card 23 of 29 frames came back like this:

    guest / api   "Vendor requested cancellation of all July bookings"
    guest / api   "Internal escalation raised for urgent team review"
    guest / api   "Confirmed passport requirement is mandatory per vendor"

Every one carried no guest words, because no guest said anything. It surfaced
as "wordless guest frames" — which reads like a rendering fault, and was
actually our own internal escalation attributed to the customer on their case
timeline.

WHO ACTED IS A FACT IN THE RAW EVENT. The summariser writes the label and the
summary; it does not get a vote on this. When it says "guest" and no raw event
behind the row was the guest, the record wins.

A COLLAPSED ROW STAYS SAFE. `srcs` holds every raw event behind an entry, so a
run that genuinely mixes a guest message with system rows still has a guest
among them and keeps its label — the correction only fires when there is no
guest anywhere in the source.
"""
import asyncio
import json

import pytest

from server.services import zendesk as Z


def _raw(idx, actor, thread="api", body="x"):
    return {"idx": idx, "actor": actor, "thread": thread, "raw_body": body,
            "time": "07 Aug 12:00", "time_sort": f"2026-08-07T12:0{idx}:00+00:00",
            "ticket_id": "1", "is_internal": False}


def _shaped(idx, actor, label="L", summary="S"):
    return {"idx_range": [idx], "actor": actor, "label": label,
            "summary": summary, "keep": True, "thread": "api",
            "time": "07 Aug 12:00"}


def _run(raws, shapes):
    """Drive the REAL shaper with only the model call stubbed.

    The actor decision lives inside _shape_via_claude, after the model answers.
    A test that rebuilt that merge would be testing the rebuild — the mistake
    trace_card.py made once, inventing a shape and then seeding tests to match
    it. Only the network call is replaced.
    """
    import server.services.claude as _cl

    async def _fake(prompt):
        return json.dumps({"events": shapes})

    old = getattr(_cl, "shape_timeline_events", None)
    _cl.shape_timeline_events = _fake
    try:
        return asyncio.run(Z._shape_via_claude(raws, {}, "", ""))
    finally:
        if old is not None:
            _cl.shape_timeline_events = old


def _counts(out):
    return next((e.get("_shape_counts") for e in out
                 if isinstance(e, dict) and e.get("_shape_counts")), None)


def test_a_guest_label_over_system_events_is_corrected():
    """THE 23-ROW CASE. No guest in the source, so the row is not the guest's."""
    out = _run([_raw(0, "system")], [_shaped(0, "guest")])
    assert out[0]["actor"] == "system", \
        "the summariser's guest label survived over a system event"


def test_a_real_guest_row_keeps_its_label():
    """The converse, and why this is not "never trust the model": when the raw
    event IS the guest, the label is right and must stand."""
    out = _run([_raw(0, "guest")], [_shaped(0, "guest")])
    assert out[0]["actor"] == "guest"


def test_a_collapsed_row_containing_a_guest_keeps_the_guest_label():
    """A run mixing a guest message with system rows is still the guest's
    story — the prompt asks for exactly that collapsing, and correcting it
    would undo a legitimate summary."""
    out = _run([_raw(0, "guest"), _raw(1, "system")],
               [dict(_shaped(0, "guest"), idx_range=[0, 1])])
    assert out[0]["actor"] == "guest"


def test_other_actors_are_not_second_guessed():
    """Only `guest` is corrected. An agent row mislabelled as system is a
    different problem with a different fix, and widening this rule would let it
    silently rewrite attributions nobody has evidence about."""
    out = _run([_raw(0, "system")], [_shaped(0, "co")])
    assert out[0]["actor"] == "co"


def test_the_correction_is_counted_and_carried():
    """Counted, or it is a silent rewrite. `_shape_counts` is how the pipeline
    puts it on the trail."""
    out = _run([_raw(0, "system"), _raw(1, "system")],
               [_shaped(0, "guest"), _shaped(1, "guest")])
    c = _counts(out)
    assert c, "no counts were stamped, so nothing can report the repair"
    assert c["actor_corrected"] == 2


def test_counts_are_stamped_even_when_no_row_was_collapsed():
    """The stamp used to be written only when the row count changed, so a run
    that corrected actors but collapsed nothing carried no counts at all — the
    repair happened and the card said nothing."""
    out = _run([_raw(0, "system")], [_shaped(0, "guest")])
    c = _counts(out)
    assert c and c["actor_corrected"] == 1


def test_a_clean_run_reports_no_correction():
    """"Looked and found nothing" must be distinguishable from "did not look",
    and a card reporting a repair on every review teaches the reader to skip
    the line."""
    out = _run([_raw(0, "guest")], [_shaped(0, "guest")])
    c = _counts(out)
    assert not c or c.get("actor_corrected") == 0


def test_the_pipeline_puts_the_correction_on_the_trail():
    """NEGATIVE-paired source assertion. The count existing is worth nothing if
    nothing renders it — the failure this repo opens with."""
    import inspect
    from server import pipeline
    src = inspect.getsource(pipeline.process_review)
    assert "actor_corrected" in src, "the trail no longer reads the count"
    assert "re-attributed" in src, "the trail no longer says what happened"
