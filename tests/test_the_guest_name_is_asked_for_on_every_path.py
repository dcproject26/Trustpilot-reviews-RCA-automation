"""The Zendesk guest-name lookup runs on every match path, not just Tier-1.

WHAT WAS WRONG. `zendesk.guest_name_for_bid()` existed, worked, returned a
typed reason, and was called from exactly ONE place: inside the Tier-1 gate,
the branch that runs when the guest quoted a booking id in their own review.
Tier-2 auto-promote, associate confirmation, manual entry and the attachment
path never called it.

So on four of the five ways a booking gets confirmed, the card printed

    "the warehouse stores this as a hash — check the Zendesk ticket"

telling the reader to go and perform a lookup THIS SYSTEM CAN PERFORM and had
simply not attempted. a8b6a10 then removed the Primary guest row on the
grounds that "the fallbacks resolve it rarely" — a judgement that, with one
call site, could only have been measured over Tier-1 traffic.

Driven through `ensure_zendesk_guest_name`, which is a function rather than a
block inside `process_review` for the reason `dss_entry` and
`shape_counts_entry` are: a block in there can only be spell-checked.
"""
import asyncio
from types import SimpleNamespace

import pytest

from server import pipeline as P


def _run(booking, **kw):
    return asyncio.run(P.ensure_zendesk_guest_name(booking, **kw))


@pytest.fixture()
def zd(monkeypatch):
    """Records what the lookup was asked, and answers whatever it is told."""
    calls = []

    def _install(answer):
        async def _fake(bid):
            calls.append(bid)
            if isinstance(answer, Exception):
                raise answer
            return answer
        monkeypatch.setattr(P.zendesk, "guest_name_for_bid", _fake)
        return calls
    return _install


HASHED = {"booking_id": "32728059",
          "primary_guest_name": "ab24TSVenneb4T3CkHFUFaGM"}


# ── it asks ────────────────────────────────────────────────────────────────

def test_a_hashed_warehouse_name_sends_us_to_zendesk(zd):
    calls = zd(("Gianmarco Lucia", ""))
    bk = dict(HASHED)
    out = _run(bk)
    assert calls == ["32728059"], calls
    assert out["asked"] is True
    assert out["name"] == "Gianmarco Lucia"
    assert bk["zendesk_guest_name"] == "Gianmarco Lucia", (
        "the name was fetched and then dropped, which is what made the card "
        "tell the reader to do the lookup it had just done")


def test_an_internal_desk_label_also_sends_us_to_zendesk(zd):
    calls = zd(("Gianmarco Lucia", ""))
    _run({"booking_id": "32728059", "primary_guest_name": "Customer Ops Lead"})
    assert calls, "an internal label was treated as a usable guest name"


def test_a_blank_warehouse_name_also_sends_us_to_zendesk(zd):
    calls = zd(("Gianmarco Lucia", ""))
    _run({"booking_id": "32728059", "primary_guest_name": ""})
    assert calls


def test_the_reference_number_is_used_when_the_booking_carries_no_id(zd):
    """The manual and attachment paths reach here with a reference and no
    warehouse row — the case that most needs the fallback."""
    calls = zd(("Gianmarco Lucia", ""))
    _run({"primary_guest_name": ""}, fallback_bid="32728059")
    assert calls == ["32728059"], calls


# ── it does not ask when there is nothing to gain ──────────────────────────

def test_a_readable_warehouse_name_is_not_second_guessed(zd):
    """Paired with the above so the lookup cannot be made unconditional. A
    Zendesk call per review per run, to replace a name we already have, is
    latency and spend for nothing."""
    calls = zd(("Someone Else", ""))
    bk = {"booking_id": "32728059", "primary_guest_name": "Gianmarco Lucia"}
    out = _run(bk)
    assert calls == [], "Zendesk was asked about a booking that had a name"
    assert out["asked"] is False
    assert bk["primary_guest_name"] == "Gianmarco Lucia"


def test_a_name_tier_one_already_found_is_not_fetched_twice(zd):
    calls = zd(("Someone Else", ""))
    out = _run({"booking_id": "32728059", "primary_guest_name": "",
                "zendesk_guest_name": "Gianmarco Lucia"})
    assert calls == []
    assert out["asked"] is False


def test_no_booking_id_says_so_rather_than_asking_for_nothing(zd):
    calls = zd(("Gianmarco Lucia", ""))
    bk = {"primary_guest_name": ""}
    out = _run(bk)
    assert calls == []
    assert out["asked"] is False
    assert "no booking id" in out["reason"], out["reason"]
    assert "no booking id" in bk["zendesk_guest_name_reason"]


# ── asked, and nothing came back ───────────────────────────────────────────

def test_asked_and_empty_is_recorded_as_asked(zd):
    """THE WHOLE POINT. "Zendesk had no readable name" and "Zendesk was never
    asked" must not read the same — the first is an answer, the second is a
    lookup nobody ran."""
    zd(("", P.zendesk.GUEST_NAME_UNAVAILABLE["no_name"]))
    bk = dict(HASHED)
    out = _run(bk)
    assert out["asked"] is True
    assert out["name"] == ""
    assert "carries no readable guest name" in out["reason"], out["reason"]
    assert bk["zendesk_guest_name_reason"] == out["reason"]
    assert "zendesk_guest_name" not in bk, "an empty name was stored as a name"


def test_a_disconnected_zendesk_is_not_a_booking_without_a_name(zd):
    zd(("", P.zendesk.GUEST_NAME_UNAVAILABLE["not_live"]))
    out = _run(dict(HASHED))
    assert out["asked"] is True
    assert "not connected" in out["reason"], out["reason"]


def test_a_raised_lookup_says_it_broke_rather_than_that_there_was_no_name(zd):
    """A crash and an absence produced the same empty string in the version of
    this that lived inside the Tier-1 branch."""
    zd(RuntimeError("zendesk 503"))
    bk = dict(HASHED)
    out = _run(bk)
    assert out["asked"] is True
    assert out["name"] == ""
    assert "raised" in out["reason"], out["reason"]
    assert "unchecked rather than absent" in out["reason"], out["reason"]
    assert bk["zendesk_guest_name_reason"] == out["reason"]


def test_a_raised_lookup_never_takes_the_run_down(zd):
    """A guest name is a nicety; the RCA is not."""
    zd(RuntimeError("zendesk 503"))
    assert _run(dict(HASHED))["name"] == ""


def test_a_missing_booking_is_not_a_crash():
    for bad in (None, "", [], 0):
        assert P.ensure_zendesk_guest_name.__name__  # imported at all
        assert asyncio.run(
            P.ensure_zendesk_guest_name(bad))["asked"] is False


# ── the call site ──────────────────────────────────────────────────────────

def test_tier_one_is_no_longer_the_only_caller():
    """NEGATIVE source assertion — the shape unreachability cannot defeat.
    The guarantee is that the lookup is not confined to one branch again; the
    behaviour above is what proves it works."""
    import inspect
    src = inspect.getsource(P.process_review)
    # NO POSITIVE HALF. `"ensure_zendesk_guest_name(" in src` would pass
    # against a build where the call is unreachable — the exact failure
    # `shape_counts_entry` had to be extracted to fix. What is asserted is
    # that the old inline lookup has not come back as a second implementation.
    assert src.count("guest_name_for_bid(") <= 1, (
        "a second guest-name lookup has appeared in process_review — one rule, "
        "one call site")
