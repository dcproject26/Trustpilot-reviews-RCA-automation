"""What the card shows for a contact is what the Slack post carries.

"if i am removing certain fields from the outbound message, it should not go
on slack" — the same principle in reverse. A field the dashboard draws and the
post drops means the analyst approved a contact summary that leadership never
reads, and neither side can tell.

`wait_for_human`, `guest_replied` and `outcome` are the ones at risk: no
Zendesk frame carries them, so the formatter has no second source to fall back
on. If `_contact_narrative` is not called they are simply absent, and an
absent line looks exactly like a contact that had nothing to report.

Driven through `format_rca_slack`, which is what Send actually posts.
"""
from types import SimpleNamespace

from server.services.slack import format_rca_slack

NARR = {
    "guest_said": "Wanted to cancel, unwell",
    "we_said": "Skylar sent the policy link",
    "wait_for_human": "18 minutes",
    "guest_replied": "Asked for a human",
    "outcome": "Escalated to CE",
}

FRAME = {"ticket_id": "34011401", "time": "22 Jul 15:41", "thread": "chat",
         "guestSaid": "Where are my tickets?", "weDid": "Acknowledged.",
         "gap": "", "time_sort": "2026-07-22T15:41:00Z"}

REVIEW = SimpleNamespace(rating=1, author="David")


def _draft(notes, frames=()):
    return SimpleNamespace(
        booking={"id": "32908218"},
        rca_v3={"support_interaction_notes": notes,
                "what_went_wrong": {"guest_issues": []},
                "flags": [], "booking_logs": [], "takedown": {"verdict": "No"}},
        l1="Operations Issue", l2="Ticket Issues", sub_theme="C. Ticket Delayed",
        primary_scenario="Tickets sent late", overlay_scenarios=[],
        wwr_scenarios=[], wwr_chain=[], support_interaction_frames=list(frames),
        sp_interaction_frames=[], area_of_improving=[], actions_taken={},
        resolution="Full refund issued.", checklist_answers=[], tldr="",
        insights={"_window_days": 90})


def _post(notes, frames=()):
    return format_rca_slack(REVIEW, _draft(notes, frames))


# ── a contact joined to a Zendesk frame ────────────────────────────────────

def _matched():
    return _post([{"zd_ref": "ZD-34011401", "summary": "Guest chased tickets",
                   **NARR}], [FRAME])


def test_the_frame_still_carries_the_post():
    """The precondition. If the contact block itself vanished, every
    assertion below would pass for the wrong reason."""
    out = _matched()
    assert "Customer / CE interactions" in out
    assert "Guest chased tickets" in out


def test_every_narrative_field_goes_out(
):
    out = _matched()
    for key, value in NARR.items():
        assert value in out, (
            f"{key} is on the card and not in the post — the analyst approved "
            f"something leadership will not read")


def test_the_three_with_no_other_source_go_out():
    """A frame carries a time, a channel and a per-event guestSaid. It carries
    none of these, so a formatter that skips them loses them outright."""
    out = _matched()
    for key in ("wait_for_human", "guest_replied", "outcome"):
        assert NARR[key] in out, key


def test_the_labels_say_which_field_is_which():
    out = _matched()
    for label in ("wait for human", "guest replied", "outcome"):
        assert label in out.lower(), label


# ── a contact Zendesk has no frame for ─────────────────────────────────────

def _orphan():
    return _post([{"zd_ref": None, "summary": "Called about the refund",
                   "time": "23 Jul 09:14", "channel": "call", **NARR}])


def test_an_unmatched_contact_carries_its_narrative_too():
    out = _orphan()
    for key, value in NARR.items():
        assert value in out, key


def test_an_unmatched_contact_carries_its_time_and_channel():
    """Both were being dropped by the projection, so the formatter's own
    handling of them had never once run on real data."""
    out = _orphan()
    assert "23 Jul 09:14" in out, "the time the guest reached out is missing"
    assert "call" in out


def test_it_is_still_marked_unverified():
    assert "unverified" in _orphan()


# ── silence stays silence ──────────────────────────────────────────────────

def test_a_field_the_model_left_null_prints_nothing():
    """A printed 'wait for human: —' reports a blank as something measured."""
    out = _post([{"zd_ref": "ZD-34011401", "summary": "Guest chased tickets",
                  "guest_said": "Where are my tickets?",
                  "wait_for_human": None, "guest_replied": None,
                  "outcome": None}], [FRAME])
    assert "wait for human" not in out.lower(), \
        "a blank was printed as a measurement"
    assert "guest replied" not in out.lower()
    assert "Where are my tickets?" in out, \
        "the field that DID come back stopped printing"


def test_a_contact_with_no_narrative_prints_no_stray_lines():
    out = _post([{"zd_ref": "ZD-34011401", "summary": "Guest chased tickets"}],
                [FRAME])
    for label in ("wait for human", "guest replied", "outcome:"):
        assert label not in out.lower(), label


def test_no_contact_at_all_still_says_so():
    assert "No guest contact found on this booking" in _post([])


# ── the helper on its own ──────────────────────────────────────────────────

def test_the_helper_returns_nothing_for_a_note_with_nothing():
    from server.services.slack import _contact_narrative
    assert _contact_narrative({"summary": "x"}) == []
    assert _contact_narrative(None) == []
    assert _contact_narrative("not a dict") == []


def test_the_helper_keeps_the_order_the_contact_happened_in():
    """Came with, we said, waited, said back, ended. Out of order it reads as
    a field dump rather than a conversation."""
    from server.services.slack import _contact_narrative
    lines = _contact_narrative(dict(NARR))
    assert [l.split(":")[0].strip() for l in lines] == [
        "guest", "we", "wait for human", "guest replied", "outcome"]
