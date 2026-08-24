"""The DSS gate on approved replies.

The macro list files the same scenario several times, differing only by what it
promises — HOC, partial refund, full refund. The guest's review is identical
across them, so nothing in the review can choose, and keyword overlap least of
all: they share almost every word. The DSS is the thing that says which remedy
the case is entitled to, so it gates the macro set before anything picks from
it.

What is asserted here is the guarantee that has to hold whatever the selector
later does: a reply must never promise a remedy the playbook did not name.
"""
import json
import pathlib

import pytest

from server.services.reply_macro import (
    REMEDIES, macro_promises, dss_permits, macro_is_permitted, gate, gate_note)


# ── what a reply promises ───────────────────────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    ("We've processed a refund of the full amount.", {"refund_full"}),
    ("I've initiated the refund to your original payment method.", {"refund_full"}),
    ("We have issued a partial refund for the closed section.", {"refund_partial"}),
    ("I've added 50% HOC to your account.", {"credit_hoc"}),
    ("Here's a coupon code EXPLORE6 for your next booking.", {"coupon"}),
    ("I've resent your tickets just now.", {"reschedule"}),
])
def test_a_promise_in_the_body_is_read(body, expected):
    assert macro_promises(body) == expected


def test_a_reply_that_promises_nothing_reads_as_nothing():
    """31 of the 80 live TP macros promise nothing — acknowledgement, asking
    for information, an ETA. That is a real answer, not a failed parse: those
    are exactly the macros that stay available when the DSS prescribes no
    remedy."""
    assert macro_promises(
        "Hey <first name>, I'm sorry about this. Could you share your booking "
        "reference so I can look into it?") == set()
    assert macro_promises("") == set()
    assert macro_promises(None) == set()


def test_a_reply_can_promise_more_than_one_thing():
    got = macro_promises("We've processed a refund and added credits to your account.")
    assert got == {"refund_full", "credit_hoc"}


# ── what the DSS permits ────────────────────────────────────────────────────

def test_a_dss_action_naming_a_remedy_permits_it():
    permitted, reason = dss_permits({"action": "Issue a coupon code EXPLORE8.",
                                     "match_score": 5})
    assert "coupon" in permitted
    assert reason == ""


def test_a_conditional_dss_action_permits_every_branch_it_names():
    """A DSS action is a decision tree, not one remedy. Resolving the branch
    needs facts the pipeline does not have, so every remedy it MENTIONS is
    permitted and choosing between them stays the associate's call — the same
    call the booking-value note leaves them."""
    permitted, _ = dss_permits({
        "action": "If the guest's claim is False - Issue Coupon code EXPLORE6 - "
                  "If True - 50% HOC for the service issue or cost of service "
                  "provided in credits",
        "match_score": 5})
    assert {"coupon", "credit_hoc"} <= permitted


def test_no_dss_row_permits_nothing_and_says_which_empty_it_is():
    permitted, reason = dss_permits({"match_score": 0, "fallback": "No DSS available"})
    assert permitted == set()
    assert "no DSS row matched" in reason


def test_an_absent_dss_lookup_is_a_different_empty_from_a_miss():
    """Both narrow the reply to remedy-free macros; one is the playbook being
    unavailable and the other is a case it is silent about, and a reader
    deciding whether to trust the draft needs to know which."""
    _, missed = dss_permits({"match_score": 0, "fallback": "No DSS available"})
    _, absent = dss_permits({})
    assert missed != absent
    assert "unavailable" in absent


def test_a_matched_row_with_no_remedy_is_its_own_reason():
    _, reason = dss_permits({"action": "Apologise and explain the venue's "
                                       "opening hours.", "match_score": 5})
    assert "names no remedy" in reason


# ── the gate itself ─────────────────────────────────────────────────────────

def test_every_promised_remedy_must_be_permitted_not_merely_one():
    """Not "any overlap". A macro promising a full refund AND credits, against
    a DSS that named only credits, would pass an overlap test and put an
    unprescribed refund in front of the guest."""
    assert macro_is_permitted({"credit_hoc", "refund_full"}, {"credit_hoc"}) is False
    assert macro_is_permitted({"credit_hoc"}, {"credit_hoc", "refund_full"}) is True


def test_a_macro_that_promises_nothing_always_passes():
    assert macro_is_permitted(set(), set()) is True
    assert macro_is_permitted(set(), {"coupon"}) is True


def test_the_gate_keeps_only_what_the_playbook_authorised():
    macros = [
        {"situation": "missed tour - HOC", "response": "I've added 50% HOC."},
        {"situation": "missed tour - partial", "response": "We issued a partial refund."},
        {"situation": "missed tour - ack", "response": "I'm sorry about this."},
    ]
    kept, dropped, _ = gate(macros, {"action": "Offer 50% HOC in credits.",
                                     "match_score": 5})
    situations = {k["situation"] for k in kept}
    assert situations == {"missed tour - HOC", "missed tour - ack"}
    assert [d["situation"] for d in dropped] == ["missed tour - partial"]


def test_the_gate_attaches_what_each_reply_commits_us_to():
    kept, _, _ = gate([{"situation": "s", "response": "I've added HOC."}],
                      {"action": "credits", "match_score": 5})
    assert kept[0]["_promises"] == ["credit_hoc"]


def test_with_no_dss_only_remedy_free_replies_survive():
    """The rule as specified: never guess between refund variants when nothing
    prescribed one."""
    macros = [
        {"situation": "refund", "response": "We processed a refund."},
        {"situation": "ack", "response": "Could you share your booking reference?"},
    ]
    kept, dropped, reason = gate(macros, {"match_score": 0, "fallback": "x"})
    assert [k["situation"] for k in kept] == ["ack"]
    assert [d["situation"] for d in dropped] == ["refund"]
    assert reason


# ── the trail line ──────────────────────────────────────────────────────────

def test_withheld_macros_are_counted_in_words():
    """"No macro fits" and "eleven fitted and every one promised a remedy the
    playbook did not authorise" are the same empty list and different
    problems."""
    _, dropped, reason = gate(
        [{"situation": "r", "response": "We processed a refund."}],
        {"action": "Offer credits.", "match_score": 5})
    note = gate_note([], dropped, reason, {"credit_hoc"})
    assert "1 approved macro(s) were withheld" in note
    assert "refund_full" in note


def test_a_clean_gate_says_nothing():
    kept, dropped, reason = gate(
        [{"situation": "s", "response": "I've added HOC."}],
        {"action": "Offer credits.", "match_score": 5})
    assert gate_note(kept, dropped, reason, {"credit_hoc"}) == ""


# ── against the real macro list, not a fixture ──────────────────────────────

VENDORED = (pathlib.Path(__file__).resolve().parent.parent
            / "server" / "data" / "canned_macros.json")


def _tp_macros():
    tabs = json.loads(VENDORED.read_text(encoding="utf-8"))
    rows = tabs.get("ORM main ( TP ) Macro") or []
    return [{"situation": (r[0] or "").replace("\n", " ").strip(),
             "response": r[1] if len(r) > 1 else ""}
            for r in rows[1:] if (r[0] or "").strip()]


def test_every_live_macro_classifies():
    """No macro may be unclassifiable — an unreadable promise would have to be
    either withheld always or passed always, and both are wrong."""
    for m in _tp_macros():
        got = macro_promises(m["response"])
        assert got <= set(REMEDIES), f"{m['situation']}: {got}"


def test_the_live_list_has_remedy_free_replies_to_fall_back_on():
    """The no-DSS rule is only usable because such macros exist. If the sheet
    ever loses them, that rule silently becomes "no reply, ever"."""
    free = [m for m in _tp_macros() if not macro_promises(m["response"])]
    assert len(free) >= 10, (
        f"only {len(free)} macros promise nothing — with no DSS row there is "
        f"almost nothing left to send")


def test_the_same_scenario_variants_are_told_apart_on_the_real_list():
    """The case that motivated the whole gate, asserted against the real
    sheet rather than a fixture built to pass."""
    missed = [m for m in _tp_macros() if "Missed the tour" in m["situation"]]
    assert len(missed) >= 3, missed
    promises = [tuple(sorted(macro_promises(m["response"]))) for m in missed]
    assert len(set(promises)) > 1, (
        f"every 'missed the tour' macro reads as promising the same thing "
        f"({promises}) — the DSS cannot choose between them")
