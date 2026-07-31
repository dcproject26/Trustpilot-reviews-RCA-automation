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


def test_send_does_not_post_an_rca_that_is_already_in_the_thread():
    """Send closes the review AND posts the RCA. "Post to thread" exists so
    the RCA can go to the team while the reply is still being edited - so
    using both, which is the documented workflow, posted it twice."""
    body = _fn(API, "send_review")
    assert "not d.rca_posted_at" in body, \
        "send re-posts an RCA that is already in the thread"


# ── flagging to the business team ───────────────────────────────────────────

def test_a_flagged_review_still_reads_as_flagged_after_a_reload():
    """ins.completion.flagged was only ever set in memory by the click
    handler. A reload cleared it, the button re-armed, and a second click
    sent another message to #biz-supply-ops."""
    assert "r.flagToBizState = draft.flag_to_biz_state" in CLIENT
    assert "r.flagToBizState === 'sent'" in CLIENT, \
        "the flagged button state must come from the server, not only memory"


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
