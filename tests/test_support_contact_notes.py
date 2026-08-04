"""The guest's conversations with us — every field of them reaches a reader.

"customer interaction part - support - guest - will only consists of the
support tickets with the guest ... here you will have to write the time stamp
when the guest reached out ... check how long did the guest wait before getting
connected with an associate and what did we respond and guest response etc."

Rule 10b asks the model for seven fields per contact. The projection in
`rca_v4_validate` accepted four and dropped the rest on the floor, silently —
`_rows` builds `{k: ... for k in fields}` and anything outside `fields` is
gone. That is this codebase's first rule exactly: a model that answered and a
model that did not produce an identical card, so no run could ever show the
rule was not working.

`wait_for_human`, `guest_replied` and `outcome` are the load-bearing ones. No
frame carries them and nothing else in the pipeline computes them, so if the
projection drops them there is no second source and the field is unreadable by
construction.

Driven through `validate_rca_v4`, not asserted at source: a field name present
in a tuple proves nothing about whether a row carrying it survives.
"""
import pytest

from server.services.rca_v4_validate import (CONTACT_CHANNELS, CONTACT_FIELDS,
                                             validate)


def validate_rca_v4(rca):
    """The projection alone. `validate` returns (blob, trail); the trail is
    checked directly where a test is about the trail."""
    return validate(rca)[0]


def _rca(**over):
    """A minimal v4 blob with one support contact."""
    note = {"zd_ref": "ZD-4491", "summary": "Guest asked to cancel",
            "time": "22 Jul 15:41", "channel": "chat",
            "guest_said": "Wants to cancel, unwell",
            "we_said": "Skylar replied with the policy link",
            "wait_for_human": "18 minutes",
            "guest_replied": "Asked for a human",
            "outcome": "Escalated to CE"}
    note.update(over)
    return {"support_interaction_notes": [note]}


def _contact(rca=None, **over):
    out = validate_rca_v4(rca or _rca(**over))
    rows = out["support_interaction_notes"]
    assert rows, "the contact was dropped entirely"
    return rows[0]


# ── the narrative survives the projection ──────────────────────────────────

@pytest.mark.parametrize("field,value", [
    ("guest_said",     "Wants to cancel, unwell"),
    ("we_said",        "Skylar replied with the policy link"),
    ("wait_for_human", "18 minutes"),
    ("guest_replied",  "Asked for a human"),
    ("outcome",        "Escalated to CE"),
])
def test_each_narrative_field_reaches_the_reader(field, value):
    assert _contact()[field] == value, (
        f"{field} was dropped by the projection. The model can answer it and "
        f"nobody can ever read it.")


def test_the_three_fields_nothing_else_computes_are_kept():
    """wait_for_human, guest_replied and outcome have no second source. A
    frame carries a time, a channel and a per-event guestSaid; it carries
    none of these three."""
    c = _contact()
    for f in ("wait_for_human", "guest_replied", "outcome"):
        assert c.get(f), f


def test_the_old_four_fields_still_work():
    """The projection was widened, not replaced."""
    c = _contact()
    assert c["zd_ref"] == "ZD-4491"
    assert c["summary"] == "Guest asked to cancel"


def test_a_field_the_model_left_null_stays_null():
    """The rule tells it to leave an undetectable field blank. A blank that
    comes back as a string would be a value nobody wrote."""
    c = _contact(wait_for_human=None)
    assert c["wait_for_human"] is None


def test_non_values_are_not_smuggled_in_as_text():
    """"Unknown" rendered in a wait-time column reads as a measurement."""
    for junk in ("Unknown", "N/A", "-", "TBD", "?"):
        assert _contact(wait_for_human=junk)["wait_for_human"] is None, junk


# ── time and channel ───────────────────────────────────────────────────────

def test_the_time_the_guest_reached_out_survives():
    """It was struck from the schema on the reasoning that the frame owns it.
    True for a contact WITH a frame; a contact with no frame then rendered a
    dash — the same dash a broken lookup renders."""
    assert _contact()["time"] == "22 Jul 15:41"


def test_the_channel_survives():
    assert _contact()["channel"] == "chat"


@pytest.mark.parametrize("ch", CONTACT_CHANNELS)
def test_every_channel_in_the_vocabulary_is_accepted(ch):
    assert _contact(channel=ch)["channel"] == ch


def test_the_channel_vocabulary_matches_what_the_frames_produce():
    """A pill must read the same whether the frame supplied it or the model
    did. zendesk._map_channel is the other end of this."""
    from server.services.zendesk import _map_channel
    produced = {_map_channel(v) for v in
                ("chat", "voice", "email", "web", "whatsapp", "phone")}
    assert produced <= set(CONTACT_CHANNELS), produced - set(CONTACT_CHANNELS)


def test_a_channel_outside_the_vocabulary_is_dropped_not_shown():
    """An invented channel renders as a pill indistinguishable from a real
    one. Null renders no pill at all."""
    assert _contact(channel="carrier pigeon")["channel"] is None


def test_a_rejected_channel_is_reported_not_silently_dropped():
    """CLAUDE.md: a coercion is 'we changed the model's answer', and the
    reader is entitled to know."""
    _, trail = validate(_rca(channel="carrier pigeon"))
    joined = " ".join(str(n) for n in trail)
    assert "carrier pigeon" in joined, (
        f"the channel was coerced with no word to the reader: {joined!r}")


def test_the_raw_channel_is_kept():
    assert _contact(channel="carrier pigeon")["channel_raw"] == "carrier pigeon"


# ── the guard that was already there still holds ───────────────────────────

def test_a_nothing_found_row_is_still_not_dressed_as_a_contact():
    """The empty state says it better than a numbered row with blank columns —
    and widening the projection must not have reopened this."""
    out = validate_rca_v4({"support_interaction_notes": [
        {"summary": "No guest contact found on this booking"}]})
    assert out["support_interaction_notes"] == []


def test_a_real_contact_is_not_swallowed_by_that_guard():
    """The inverse bug: a filter that eats real rows is worse than one that
    lets a placeholder through."""
    assert len(validate_rca_v4(_rca())["support_interaction_notes"]) == 1


def test_a_contact_with_only_a_narrative_field_still_counts():
    """`_rows` drops a row where every column is empty. A contact carrying
    nothing but an outcome is thin, not empty — and before the projection was
    widened it WAS empty, so it vanished."""
    out = validate_rca_v4({"support_interaction_notes": [
        {"outcome": "Dropped, no reply from us"}]})
    assert len(out["support_interaction_notes"]) == 1
    assert out["support_interaction_notes"][0]["outcome"] == "Dropped, no reply from us"


def test_every_declared_field_is_actually_projected():
    """CONTACT_FIELDS is the contract. A name added to it and not honoured by
    the projection would be the same silent drop in a new coat."""
    filled = {f: f"v-{f}" for f in CONTACT_FIELDS if f != "channel"}
    filled["channel"] = "chat"
    c = _contact({"support_interaction_notes": [filled]})
    for f in CONTACT_FIELDS:
        assert f in c, f
        assert c[f] is not None, f


# ── ordering ───────────────────────────────────────────────────────────────

def test_contacts_keep_the_order_the_model_gave_them():
    """"if there are multiple, then list is chronologically" — the model is
    told to emit them in order, so the projection must not reorder or the
    instruction is unenforceable."""
    out = validate_rca_v4({"support_interaction_notes": [
        {"summary": "first", "time": "22 Jul 10:00"},
        {"summary": "second", "time": "22 Jul 15:00"},
        {"summary": "third", "time": "23 Jul 09:00"}]})
    assert [r["summary"] for r in out["support_interaction_notes"]] == \
        ["first", "second", "third"]
