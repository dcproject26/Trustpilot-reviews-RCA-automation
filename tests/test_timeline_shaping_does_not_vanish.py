"""The whole timeline fell back to raw ticket bodies because of one cut object.

What reached the card:

    chat  System event   (21:18:14) Headout: Hi, I'm Skyler 👋 Let me know…
    api   System event   ![Logo](https://cdn-imgix-open.headout.com/emails/…

Those are `_fallback_shape` labels over unprocessed comment bodies. The chain:

  * `shape_timeline_events` asked for max_tokens=3000. The prompt requires one
    shaped entry per raw event, the fetch caps at 41 (20 + marker + 20), and
    an entry with idx_range/time/thread/actor/label/summary runs 60-100
    tokens. Forty of those is past 3000, so the answer was cut mid-array.
  * `_safe_parse_events` needs a CLOSED `]`. Both of its attempts failed on a
    truncated tail, so it returned [] — and forty complete shaped entries were
    thrown away because the forty-first was incomplete.
  * `_shape_via_claude` fell back to raw bodies for EVERY event.

The RCA call next door sizes for its own shape AND repairs a truncated tail —
"closing the open string and braces so a long RCA degrades to a partial one
instead of vanishing". The timeline path did neither.
"""
import pytest

from server.services.zendesk import _safe_parse_events, _salvage_objects


COMPLETE = '[{"idx_range":[0],"label":"A","keep":true},' \
           '{"idx_range":[1],"label":"B","keep":true}]'
TRUNCATED = '[{"idx_range":[0],"label":"A","keep":true},' \
            '{"idx_range":[1],"label":"B","keep":true},' \
            '{"idx_range":[2],"lab'


def test_a_complete_response_still_parses():
    assert len(_safe_parse_events(COMPLETE)) == 2


def test_a_truncated_response_keeps_what_completed():
    """THE DEFECT. Two whole entries survive a cut third instead of all three
    being lost."""
    got = _safe_parse_events(TRUNCATED)
    assert len(got) == 2, got
    assert [e["label"] for e in got] == ["A", "B"], got


def test_a_truncated_response_is_not_an_empty_list():
    """[] is what sends the caller to `_fallback_shape` for every event."""
    assert _safe_parse_events(TRUNCATED) != []


def test_fences_and_a_truncated_tail_together():
    """The model wraps its answer AND the answer is cut. Both handled."""
    got = _safe_parse_events("```json\n" + TRUNCATED)
    assert len(got) == 2, got


def test_a_brace_inside_a_summary_does_not_close_an_object():
    """Summaries contain braces and quotes. A regex would end the object at
    the first '}' inside a value and salvage nonsense."""
    got = _salvage_objects('[{"summary":"cost {x} and \\"quoted\\""},'
                           '{"summary":"b"},{"sum')
    assert len(got) == 2, got
    assert got[0]["summary"] == 'cost {x} and "quoted"', got[0]


def test_nothing_salvageable_is_still_empty():
    """A response with no complete object left has genuinely nothing, and must
    not fabricate one."""
    assert _safe_parse_events('[{"idx_range":[0],"lab') == []
    assert _safe_parse_events("total nonsense") == []


def test_an_unbalanced_closing_brace_does_not_break_the_scan():
    got = _salvage_objects('}}{"label":"A"}')
    assert [o["label"] for o in got] == ["A"], got


def test_the_shaping_call_asks_for_enough_tokens():
    """A budget that cannot hold one entry per event guarantees the truncation
    above on every long case. Sized like the RCA call, for the same reason."""
    src = open("server/services/claude.py", encoding="utf-8").read()
    i = src.index("async def shape_timeline_events")
    j = src.index("async def", i + 10)
    seg = src[i:j]
    assert "max_tokens=3000" not in seg, "the shaping budget is back to 3000"
    assert "max_tokens=16000" in seg, seg[-200:]


# ── a shorter timeline and a complete one must not look alike ─────────────

def test_the_shaping_counts_are_stamped_when_rows_are_lost():
    """"10 events became 8" is a judgement the model made — collapsing, or
    keep:false — and the reader cannot see it. A booking with eight events and
    a ten-event booking shaped down to eight render identically."""
    src = open("server/services/zendesk.py", encoding="utf-8").read()
    assert '"_shape_counts"' in src or "_shape_counts=" in src, \
        "nothing records how many raw events became how many rows"
    assert "_dropped_by_model += 1" in src, \
        "events dropped on keep:false are not counted"


def test_the_trail_reports_what_the_shaping_removed():
    src = open("server/pipeline.py", encoding="utf-8").read()
    assert "<strong>Events timeline:</strong>" in src
    assert "ticket event(s) read" in src
    assert "Nothing was deleted" in src, \
        "the line must say the events still exist on the ticket"


def test_no_count_is_stamped_when_nothing_was_lost():
    """A count on every healthy booking is the noise that makes a reader stop
    reading the ones that mean something.

    DRIVEN, NOT SPELLED. This asserted the literal condition
    `if out and len(raw_events) != len(out):` appeared in the source. When a
    second reason to stamp was added — an actor the summariser had invented —
    the condition legitimately changed and this failed, while the behaviour it
    protects was untouched. A source assertion on a POSITIVE behaviour pins the
    wording rather than the rule, which is why CLAUDE.md allows them only for
    negatives and for client-side JavaScript.
    """
    import asyncio
    import json
    from server.services import zendesk as Z
    import server.services.claude as _cl

    raws = [{"idx": 0, "actor": "guest", "thread": "email", "raw_body": "hi",
             "time": "07 Aug 12:00", "time_sort": "2026-08-07T12:00:00+00:00",
             "ticket_id": "1", "is_internal": False}]
    shapes = [{"idx_range": [0], "actor": "guest", "label": "Guest wrote in",
               "summary": "Asked a question", "keep": True,
               "thread": "email", "time": "07 Aug 12:00"}]

    async def _fake(prompt):
        return json.dumps({"events": shapes})

    old_fn = getattr(_cl, "shape_timeline_events", None)
    _cl.shape_timeline_events = _fake
    try:
        out = asyncio.run(Z._shape_via_claude(raws, {}, "", ""))
    finally:
        if old_fn is not None:
            _cl.shape_timeline_events = old_fn

    assert len(out) == len(raws), "nothing should have been collapsed here"
    assert not any(e.get("_shape_counts") for e in out if isinstance(e, dict)), \
        "a complete, uncorrected timeline still carried a count"
