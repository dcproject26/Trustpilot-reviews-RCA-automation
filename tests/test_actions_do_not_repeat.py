"""Actions that already went out must not offer to go out again.

Four separate places recorded state on the server, sent it to the browser on
every load, and then rendered a button as though nothing had happened. Two of
them post into a Slack channel people are reading, so the second click is not
a harmless no-op - it is a duplicate arriving in front of the team.

These are read as source assertions rather than by driving a browser: the
client is one HTML file with no build step and no test harness, and a wrong
answer here is a duplicate Slack post, which is worth guarding even crudely.
"""
import re

import pytest


API    = open("server/api.py", encoding="utf-8").read()
CLIENT = open("client/index.html", encoding="utf-8").read()
PIPE   = open("server/pipeline.py", encoding="utf-8").read()


def _fn(src: str, name: str) -> str:
    """The body of a top-level def, up to the next one."""
    i = src.find(f"def {name}(")
    assert i > 0, f"{name} not found"
    j = src.find("\n@", i)
    k = src.find("\ndef ", i + 1)
    end = min(x for x in (j, k, len(src)) if x > 0)
    return src[i:end]


# ── posting the RCA into the review's Slack thread ──────────────────────────

def test_posting_the_rca_twice_requires_asking_twice():
    """Without this the endpoint posted every time it was called, and the
    button re-armed itself after 2.5 seconds."""
    body = _fn(API, "post_rca_to_thread")
    assert "if d.rca_posted_at and not force:" in body, \
        "post-rca will happily post a second copy with no one asking for one"
    assert "already_posted" in body


def test_the_client_knows_the_rca_is_already_posted():
    assert "r.rcaPostedAt   = draft.rca_posted_at" in CLIENT, \
        "the server sends rca_posted_at on every load; something must read it"
    assert "if (slackPostBtn && r.rcaPostedAt)" in CLIENT, \
        "the button must show the post already happened"
    assert "'?force=true'" in CLIENT, \
        "a deliberate second post must say so explicitly"


# "Send does not re-post an RCA already in the thread" used to live here as
# `assert "not d.rca_posted_at" in body`. It broke when the condition was
# rewritten from `and not d.rca_posted_at` into its own `elif` branch — the
# behaviour was identical and the test failed on the spelling, which is the
# exact weakness CLAUDE.md names. It is now driven against the endpoint in
# tests/test_route_to_sent.py::test_send_does_not_repost_an_rca_already_posted,
# which counts the Slack posts instead of reading the source.


# ── flagging to the business team ───────────────────────────────────────────

def test_a_flagged_review_still_reads_as_flagged_after_a_reload():
    """ins.completion.flagged was only ever set in memory by the click
    handler, so a reload cleared it and the button re-armed."""
    assert "r.flagToBizState = draft.flag_to_biz_state" in CLIENT
    assert "r.flagToBizState === 'sent'" in CLIENT, \
        "the flagged button state must come from the server, not only memory"


def test_flagging_to_biz_actually_calls_the_server():
    """The button built a local actions_taken entry, set flagged = true in
    memory and closed. It said "✓ Flagged · sent to Slack" and never called
    anything. The endpoint worked the whole time; nothing invoked it."""
    i = CLIENT.find("#flag-send-btn")
    handler = CLIENT[i:i + 1600]
    assert "/flag-to-biz" in handler, \
        "the Flag to Biz button does not send anything"
    assert "send: true" in handler


def test_there_is_no_invented_slack_channel():
    """#biz-supply-ops does not exist in the workspace. It was the fallback
    destination here, so anything routed to it went nowhere while the UI
    reported success. Everything goes to the review's own thread."""
    # Comments may name it — explaining why it is gone is the point. Code
    # must not.
    code = "\n".join(l for l in API.splitlines()
                     if not l.lstrip().startswith("#"))
    assert "biz-supply-ops" not in code, \
        "the invented channel is still a value in the code, not just a note"
    assert "flag-channel" not in CLIENT, \
        "the channel input is back; there is only one destination"
    assert "channel: str" not in API[API.find("class FlagToBiz"):
                                      API.find("class FlagToBiz") + 400]


def test_the_numbers_biz_acts_on_are_fields_not_prose():
    """Completion rate, TGID, TID and VID used to be baked into the message
    body, so correcting a wrong TGID meant editing prose and what went out was
    whatever survived the edit. They are inputs now, and they reach Slack in a
    fixed shape."""
    import types
    from server.api import _biz_facts
    B = types.SimpleNamespace
    line = _biz_facts(B(completion_rate=87.4, tgid="12345", tid="678", vid="9012"))
    assert line == "Completion 87.4% · TGID 12345 · TID 678 · VID 9012"
    # A missing one is omitted, not printed as None.
    assert _biz_facts(B(completion_rate=90.0, tgid="", tid=None, vid="9012")) \
        == "Completion 90% · VID 9012"
    assert _biz_facts(B(completion_rate=None, tgid=None, tid=None, vid=None)) == ""

    for field in ("flag-completion", "flag-tgid", "flag-tid", "flag-vid"):
        assert f'id="{field}"' in CLIENT, f"{field} is missing from the modal"
    i = CLIENT.find("#flag-send-btn")
    handler = CLIENT[i:i + 2000]
    for key in ("completion_rate", "tgid", "tid", "vid"):
        assert key in handler, f"{key} is collected but never sent"


def test_the_facts_reach_the_slack_message():
    """The numbers are the point of the flag: a message saying "completion is
    low" without them is a claim the Biz team cannot act on.

    It used to assert TWO uses — the message and an actions_taken entry. Flag
    to Biz no longer writes into actions_taken: that column is the fixes and
    the rows a person typed, and a second writer is how it came to carry four
    kinds of row under one heading. The flag is recorded on the draft's own
    flag_to_biz state and in the Slack thread it was posted to."""
    body = _fn(API, "flag_to_biz")
    assert "_biz_facts(body)" in body, "the numbers never reach the Slack message"


def test_flagging_to_biz_does_not_write_into_actions_taken():
    """NEGATIVE, so a second writer cannot quietly come back."""
    body = _fn(API, "flag_to_biz")
    assert 'd.actions_taken =' not in body, \
        "flag_to_biz is writing into actions_taken again"


def test_flagging_without_a_slack_thread_is_refused_not_redirected():
    body = _fn(API, "flag_to_biz")
    assert "has no Slack thread" in body, \
        "a review with no thread must be told, not silently posted elsewhere"


# ── the one candidate the associate is asked to judge ───────────────────────

def test_an_unverified_booking_id_reaches_the_picker_in_the_right_shape():
    """verify_bid returns date_of_visit and no match reasons; the picker reads
    visitDate and matchReasons. So the candidate the system explicitly does
    NOT trust was the one whose card showed no date and no reasons."""
    assert "_shape_weak_bid(bq_row, _why)" in PIPE, \
        "the weak-BID candidate is going to the picker unshaped"
    shaper = _fn(PIPE, "_shape_weak_bid")
    for key in ("visitDate", "experienceDate", "matchReasons", "experienceName"):
        assert f'"{key}"' in shaper, f"the picker reads {key} and it is not set"


def test_the_weak_bid_card_says_what_is_weak_about_it():
    i = PIPE.find("_why = [")
    assert i > 0
    block = PIPE[i:i + 500]
    assert "no venue match" in block and "name" in block, \
        "an associate confirming an unverified id needs to see why it is unverified"


# ── the guard that made all four findable ───────────────────────────────────

@pytest.mark.parametrize("field", ["rca_posted_at", "flag_to_biz_state", "sent_at"])
def test_state_the_server_sends_is_read_by_the_client(field):
    """Every one of these bugs was the same shape: the server recorded it, the
    API sent it, and nothing in the client ever looked. Adding a field to
    _draft_dict without a consumer is how it happens."""
    assert f'"{field}"' in API, f"{field} is no longer sent"
    assert field in CLIENT, f"the client never reads {field} — is it rendered?"


# ── a prescription written three ways is one piece of work ────────────────
#
# The reported CO tab carried 32 rows. Among them:
#
#   "Require an agent to contact the guest proactively whenever a
#    system-initiated reschedule changes the start time…"
#   "Require a proactive notification to the guest whenever a vendor
#    reassignment changes the confirmed start time."
#   "Notify the guest proactively whenever a reschedule results in a vendor or
#    time change that differs from what was confirmed."
#
# One instruction, three times. They survived because an ACTION is a
# prescription — the same instruction written twice shares its subject and its
# object and little else — while the threshold was set for FINDINGS, where two
# facts sharing half their words are usually two facts.

from server.checklist import _is_repeat, _remember, _dedup_key

RESTATEMENTS = [
    "Require an agent to contact the guest proactively whenever a "
    "system-initiated reschedule changes the start time, before the "
    "rescheduling window closes.",
    "Require a proactive notification to the guest whenever a vendor "
    "reassignment changes the confirmed start time.",
    "Notify the guest proactively whenever a reschedule results in a vendor "
    "or time change that differs from what was confirmed.",
]
DISTINCT = ("Require RO to verify the new operator confirmed pickup time "
            "against the rescheduled slot before sending any confirmation.")


def _keep(rows, group="action"):
    seen, out = {"exact": set(), "tokens": {}}, []
    for r in rows:
        if _is_repeat(r, seen, group):
            continue
        _remember(r, seen, group)
        out.append(r)
    return out


def test_three_wordings_of_one_instruction_become_one_row():
    assert len(_keep(RESTATEMENTS)) == 1, _keep(RESTATEMENTS)


def test_a_genuinely_different_action_is_not_swallowed():
    """The threshold has to keep real work. This one names a different team
    doing a different check at a different moment."""
    assert len(_keep(RESTATEMENTS + [DISTINCT])) == 2, _keep(RESTATEMENTS + [DISTINCT])


def test_the_threshold_sits_in_a_measured_gap():
    """NOT a picked number. The restatements sit at 0.50 containment with each
    other and the distinct action at 0.20-0.30 against all three, so the
    threshold falls in daylight rather than through a cluster. If this gap
    ever closes, the dial is guessing again and this says so."""
    ks = [_dedup_key(r) for r in RESTATEMENTS]
    kd = _dedup_key(DISTINCT)

    def _ov(a, b):
        return len(a & b) / max(1, min(len(a), len(b)))

    same = [_ov(ks[i], ks[j]) for i in range(3) for j in range(i + 1, 3)]
    diff = [_ov(k, kd) for k in ks]
    assert min(same) >= 0.5, same
    assert max(diff) < 0.5, diff
    assert min(same) - max(diff) >= 0.15, (same, diff)


def test_findings_keep_the_stricter_threshold():
    """A fact sharing half its words with another fact is usually a second
    fact. Loosening actions must not loosen findings."""
    assert len(_keep(RESTATEMENTS, group="finding")) == 3


def test_a_short_row_does_not_swallow_a_longer_one():
    """Containment over a two-word set measures nothing. "Resend the tickets
    to the guest" reduces to {resend, ticket}; against {refund, second,
    ticket} the shared word "ticket" is 1/2 = 0.5 containment, so a refund got
    absorbed into a resend.

    The min-token guard checked the INCOMING row and not the one it was
    compared against."""
    rows = ["Resend the tickets to the guest", "Refund the second ticket"]
    assert len(_keep(rows)) == 2, _keep(rows)


def test_the_guard_applies_to_findings_too():
    rows = ["Tickets were resent", "Refund of the second ticket"]
    assert len(_keep(rows, group="finding")) == 2, _keep(rows, group="finding")
