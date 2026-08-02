"""Timeline summaries are complete sentences, and a cut says it is ours.

A real card read:

    Guest requested refund citing WhatsApp cancellation notice from local
    operator; agent cited non-cancellable p...

The cap was 110 characters, set to protect a narrow column that had since
learned to wrap, and the cost was the reader guessing at the missing half. A
bare "…" is also exactly how a model trails off, so a truncation could not be
told from the model's own phrasing — an "I cut this" that reads as "there was
nothing more".
"""
from server.services.zendesk import _clip


def test_a_summary_under_the_cap_is_untouched():
    s = ("Guest requested refund citing WhatsApp cancellation notice from local "
         "operator; agent cited non-cancellable policy; left unresolved")
    assert _clip(s, 600) == s
    assert "cut at" not in _clip(s, 600)


def test_the_sentence_that_prompted_this_now_survives():
    """The exact string from the screenshot, at the cap the shaper uses."""
    s = ("Guest requested refund citing WhatsApp cancellation notice from local "
         "operator; agent cited non-cancellable policy")
    assert _clip(s, 600) == s, "the summary from the screenshot is still cut"


def test_a_cut_says_it_is_ours():
    """Not a bare ellipsis. The reader has to be able to tell "there is more"
    from "the model stopped there"."""
    got = _clip("word " * 400, 600)
    assert "cut at 600 chars" in got
    assert not got.endswith("…")


def test_a_cut_lands_on_a_word_boundary():
    """"non-cancellable p" is unreadable in a way "non-cancellable" is not."""
    s = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
    got = _clip(s, 30)
    body = got.split(" […cut")[0]
    assert s.startswith(body)
    assert s[len(body):len(body) + 1] in ("", " "), \
        f"cut mid-word: ...{body[-12:]!r}"


def test_a_single_long_token_is_still_cut_rather_than_kept():
    """One 900-character token has no word boundary to fall back on, and
    returning it whole would defeat the backstop entirely."""
    got = _clip("x" * 900, 600)
    assert len(got) < 700
    assert "cut at" in got


def test_the_shaper_delivers_the_whole_summary(monkeypatch):
    """Driven through the shaping step, not read off the constant.

    Asserting the number 600 appears in zendesk.py would pass just as happily
    against a build where the summary is clipped somewhere else first. This
    puts a 190-character summary through the real path and reads what comes
    out the other end.
    """
    import asyncio
    import json
    import server.services.zendesk as Z
    from server.services import claude as _claude

    long_but_reasonable = (
        "Guest requested refund citing WhatsApp cancellation notice from the "
        "local operator; agent cited the non-cancellable policy and asked for "
        "a screenshot; ticket left open pending vendor confirmation")
    assert len(long_but_reasonable) > 110, "the fixture must exceed the old cap"

    shaped = [{"idx_range": [0], "time": "30 Jul 15:33", "thread": "chat",
               "actor": "guest", "label": "Guest chat",
               "summary": long_but_reasonable, "keep": True}]

    async def _fake(prompt):
        return json.dumps(shaped)

    monkeypatch.setattr(_claude, "shape_timeline_events", _fake)
    raw = [{"idx": 0, "time": "30 Jul 15:33", "time_sort": "2026-07-30T15:33:00",
            "thread": "chat", "actor": "guest", "ticket_id": "34256902",
            "body": long_but_reasonable}]
    out = asyncio.run(Z._shape_via_claude(raw, {"id": "1"}, "bad", "2026-08-01"))

    assert out, "the shaper dropped the event"
    assert out[0]["summary"] == long_but_reasonable, (
        f"a {len(long_but_reasonable)}-character summary is still being cut: "
        + out[0]["summary"])
