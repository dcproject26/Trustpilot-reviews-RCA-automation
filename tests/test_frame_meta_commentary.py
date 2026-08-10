"""A field that does not apply is empty, not an explanation of why.

WHAT SHIPPED. Every one of guestSaid / weDid / guestReply is rendered as what
that person said — on the card, and in quotation marks in the Slack post. Most
timeline events are one-sided, so the model is routinely asked what the guest
said about an agent's reply. Nothing in the prompt said what to do with a
field that does not apply, so it explained itself into the field:

    07 Aug 18:32 IST  guest   N/A — this is the guest's reply event
    09 Aug 07:02 IST  co      Not applicable — this event is an outbound agent
    09 Aug 12:24 IST  co      N/A — this is an agent response event, not a...

Read on the card, that is the guest and the agent talking nonsense at each
other. It is the same defect as the fabricated timeline rows: commentary
addressed to us, sitting where content is expected, in a place whose framing
says everything in it is real.

The prompt now forbids it (rule 2a) and the coercion enforces it, because a
prompt is a request — the rule it replaced was followed exactly.
"""
import asyncio
import json

import pytest

from server.services import claude as C


# ── the matcher ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "N/A — this is the guest's reply event",
    "N/A - this is an agent response event, not a guest message",
    "n/a",
    "NA",
    "Not applicable — this event is an outbound agent response",
    "not applicable",
    "None",
    "nil",
    "This is an agent response, not a guest message",
    "The event is a system notification",
    "No guest message on this event",
    "Does not apply — agent-side event",
])
def test_commentary_about_the_field_is_recognised(text):
    # THROUGH _blank_meta, not the raw pattern. The first version asserted
    # `_NOT_APPLICABLE.match(text)` while the code had moved to `.search()`,
    # so the test anchored no matter what the pattern said — and a mutation
    # deleting the `^` survived twice, because nothing in the suite exercised
    # the anchoring the way the code does. A test that calls a different
    # method than the call site is testing a different function.
    out, blanked = C._blank_meta({"guestSaid": text})
    assert blanked == ["guestSaid"], f"{text!r} was not caught"
    assert out["guestSaid"] == ""


@pytest.mark.parametrize("text", [
    "Guest asked for the approximate arrival time at Bran Castle.",
    "Agent Sujaan apologised and issued a 25% credit.",
    "The refund policy is not applicable here, the guest was told.",
    "Nothing further was raised on this thread.",
    "No CE action on this thread",
    "Thanks — none of the castles were as described.",
])
def test_a_real_message_is_left_alone(text):
    """ANCHORED AT THE START, deliberately. A genuine message can contain "not
    applicable" or "none" partway through, and blanking those would delete
    something a person actually wrote — which is worse than the bug, because
    the row still renders and now misquotes them."""
    out, blanked = C._blank_meta({"guestSaid": text})
    assert blanked == [], f"{text!r} was wrongly caught"
    assert out["guestSaid"] == text, "a real message was altered"


# ── the coercion, and what it reports ───────────────────────────────────────

def test_the_offending_fields_are_blanked_and_named():
    frame = {"guestSaid": "N/A — this is the guest's reply event",
             "weDid": "Agent Parneet confirmed the 25% credit.",
             "guestReply": "Not applicable — no reply on this event",
             "gap": ""}
    out, blanked = C._blank_meta(frame)
    assert out["guestSaid"] == "" and out["guestReply"] == ""
    assert out["weDid"] == "Agent Parneet confirmed the 25% credit.", \
        "it blanked a real message"
    assert sorted(blanked) == ["guestReply", "guestSaid"]


def test_a_clean_frame_reports_nothing_blanked():
    """The other half of the rule: this has to be able to say it looked and
    found nothing, or every frame reads as having been repaired."""
    frame = {"guestSaid": "Guest asked which castle was first.",
             "weDid": "", "guestReply": "", "gap": ""}
    out, blanked = C._blank_meta(frame)
    assert blanked == []
    assert out == frame


def test_gap_is_not_touched():
    """`gap` is picked from a fixed vocabulary, not written as speech, and
    "None" is a legitimate value there. Blanking it would silently drop a
    classification."""
    out, blanked = C._blank_meta({"guestSaid": "", "weDid": "", "guestReply": "",
                                  "gap": "None"})
    assert out["gap"] == "None" and blanked == []


# ── the call site, which is where it has to actually run ────────────────────

def test_the_live_path_blanks_and_warns(monkeypatch, caplog):
    """DRIVING summarise_support_event, not _blank_meta. A coercion written and
    not wired is the failure CLAUDE.md opens with, and it would look identical
    from here — same tests green, same commentary reaching the card."""
    import logging
    monkeypatch.setattr(C, "is_live", lambda svc: True)

    async def _fake(prompt, max_tokens=500):
        return json.dumps({"guestSaid": "N/A — this is an agent response event",
                           "weDid": "Agent explained the pricing structure.",
                           "guestReply": "", "gap": ""})
    monkeypatch.setattr(C, "_call", _fake)

    with caplog.at_level(logging.WARNING, logger="server.services.claude"):
        got = asyncio.run(C.summarise_support_event(
            {"actor": "co", "ticket_id": "34523302"}, None, None))
    assert got["guestSaid"] == "", "the commentary reached the card"
    assert got["weDid"] == "Agent explained the pricing structure."
    # getMessage(), not `message % args`: `message` is already interpolated on
    # some records and re-applying args raises on those.
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "blanked meta-commentary" in said, "the coercion was silent"
    assert "guestSaid" in said and "34523302" in said, \
        "it did not say which field on which ticket"


def test_a_clean_answer_passes_through_without_a_warning(monkeypatch,
                                                        caplog):
    """A warning on every frame is noise, and noise is how a real one gets
    scrolled past."""
    import logging
    monkeypatch.setattr(C, "is_live", lambda svc: True)

    async def _fake(prompt, max_tokens=500):
        return json.dumps({"guestSaid": "Guest asked about the return transfer.",
                           "weDid": "", "guestReply": "", "gap": ""})
    monkeypatch.setattr(C, "_call", _fake)

    with caplog.at_level(logging.WARNING, logger="server.services.claude"):
        got = asyncio.run(
            C.summarise_support_event({"actor": "guest"}, None, None))
    assert got["guestSaid"] == "Guest asked about the return transfer."
    assert "blanked" not in " ".join(r.getMessage() for r in caplog.records)


def test_the_prompt_states_the_rule():
    """NEGATIVE-ish source assertion, and the reason it earns its place: the
    coercion is a net, not a fix. If the prompt stops asking, every frame gets
    repaired at the boundary and the log fills with warnings that read as a
    model fault rather than a missing instruction."""
    from server import prompts
    p = prompts.support_event_prompt({"actor": "co"}, None, None)
    assert "DOES NOT APPLY" in p
    assert '"N/A"' in p and "Not applicable" in p
