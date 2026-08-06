"""A refused Slack post says which refusal it was, and whether to click again.

`post_to_thread` returned None for every outcome — rate-limited, wrong
channel, revoked token, message too long — AND for MOCK_MODE, where nothing
was wrong at all. The caller turned all of that into one sentence:

    "Slack rejected the post - check the bot's channel membership and scopes."

which names the one cause it was written for and is wrong about the rest. A
rate limit clears by itself; that sentence sent the reader to audit the app's
channel list instead of waiting ten seconds. This is CLAUDE.md §1 twice over:
a broken mechanism and a working one producing identical output, and an error
that does not name what would work.

The part that changes what the reader does is the VERDICT — retry / fix /
manual — so that is what is pinned hardest here.

WHAT MUST NOT HAPPEN, and what the last test is for: the review moving to
Sent. Nothing was posted, so a Sent row would be a lie, and it would be told
in the one tab that exists to say what still needs doing.
"""
import pytest

from server.services.slack import (POST_ERRORS, post_failure_sentence,
                                   last_post_failure)


# ── the sentence ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code", sorted(POST_ERRORS))
def test_every_known_code_says_what_would_work(code):
    """"An error should name what would work" — so `next` is never empty."""
    got = post_failure_sentence(code)
    assert got["why"].strip(), code
    assert got["next"].strip(), code
    assert got["code"] == code


@pytest.mark.parametrize("code", sorted(POST_ERRORS))
def test_every_known_code_has_one_of_the_three_verdicts(code):
    """A fourth value would reach `retryable` as a falsy unknown and quietly
    turn a retryable failure into one the reader is told to hand-copy."""
    assert post_failure_sentence(code)["verdict"] in ("retry", "fix", "manual"), code


def test_the_reasons_are_distinguishable_from_each_other():
    """The whole point. Two codes sharing a sentence puts the reader back to
    guessing, which is the state this replaced."""
    whys = {post_failure_sentence(c)["why"] for c in POST_ERRORS}
    assert len(whys) == len(POST_ERRORS), f"{len(POST_ERRORS)} codes, {len(whys)} sentences"


def test_a_rate_limit_is_retryable_and_does_not_mention_channels():
    """The case the old sentence was worst on: temporary, self-clearing, and
    it sent the reader to audit channel membership."""
    got = post_failure_sentence("ratelimited")
    assert got["verdict"] == "retry", got
    assert "channel" not in got["why"].lower(), got["why"]


def test_not_in_channel_asks_for_the_invite_rather_than_a_hand_copy():
    """One invite makes the button work. Telling this reader to paste by hand
    would be advice that leaves the cause in place for every later review."""
    got = post_failure_sentence("not_in_channel")
    assert got["verdict"] == "fix", got
    assert "invite" in got["next"].lower(), got["next"]


def test_a_revoked_token_is_not_offered_as_retryable():
    """Clicking again cannot help, and offering it wastes the reader's time on
    a loop that has no exit."""
    for code in ("token_revoked", "invalid_auth", "account_inactive"):
        assert post_failure_sentence(code)["verdict"] == "manual", code


def test_msg_too_long_points_at_the_editor_on_this_screen():
    got = post_failure_sentence("msg_too_long")
    assert got["verdict"] == "fix", got
    assert "shorten" in got["next"].lower(), got["next"]


# ── the code this build has never seen ──────────────────────────────────────

def test_an_unknown_code_is_not_flattened_into_a_known_one():
    """Slack adds error codes. Guessing which known one it resembles asserts
    a cause nobody established — the exact fault being fixed."""
    got = post_failure_sentence("some_new_slack_code")
    assert "some_new_slack_code" in got["why"], got["why"]
    assert got["code"] == "some_new_slack_code"


def test_an_unknown_code_takes_the_cautious_verdict():
    """"We do not know whether retrying helps" must not present as "retry"."""
    assert post_failure_sentence("some_new_slack_code")["verdict"] == "manual"


def test_an_unknown_code_says_it_is_unknown_rather_than_implying_guidance():
    """So the reader can tell "this build has no advice" from advice."""
    got = post_failure_sentence("some_new_slack_code")
    assert "no guidance" in got["next"].lower(), got["next"]


# ── staleness ───────────────────────────────────────────────────────────────

def test_the_module_records_a_failure_shape_that_the_api_can_read():
    """Four keys, because api.py indexes all four. A rename here that left the
    caller reading a missing key would fall to its "no reason" branch and
    report every rejection as reasonless."""
    assert set(last_post_failure) == {"code", "why", "verdict", "next"}


def test_a_successful_post_does_not_inherit_the_previous_failure():
    """`last_post_failure` is module state. Left uncleared, the reason from a
    post that failed an hour ago attaches to one that succeeded — and the
    reader is told a post that went through was refused.

    post_to_thread clears it on entry; this drives that clearing directly
    because reaching the real call needs a live Slack.
    """
    from server.services import slack as S
    S.last_post_failure.update(post_failure_sentence("ratelimited"))
    assert S.last_post_failure["why"]

    # The first thing post_to_thread does, and the thing that must not be
    # dropped when that function is next edited.
    import inspect
    src = inspect.getsource(S.post_to_thread)
    body = src.split("\n", 1)[1]
    assert "last_post_failure.update" in body.split("if MOCK_MODE")[0], (
        "post_to_thread must clear last_post_failure BEFORE it can return, "
        "or a stale reason survives a successful post")


# ── and the thing that must never happen ────────────────────────────────────

def test_a_refused_post_never_marks_the_review_sent():
    """NEGATIVE source assertion — permitted by CLAUDE.md because
    unreachability cannot defeat "this string appears nowhere".

    The failure branch must raise before touching rca_posted_at. If an edit
    ever moves the assignment above the guard, a post Slack refused would show
    as posted and the review would leave the queue with nothing in the thread.
    """
    import inspect
    from server import api
    src = inspect.getsource(api.post_rca_to_thread)
    head, _, tail = src.partition("if ts is None and not MOCK_MODE:")
    assert tail, "the Slack-refused guard has been renamed or removed"
    assert "rca_posted_at =" not in head, (
        "rca_posted_at is set before the refused-post guard runs")


# ── driven through the real function, not just its data ────────────────────
#
# A mutation removing `last_post_failure.update(post_failure_sentence(code))`
# from post_to_thread SURVIVED the tests above: they check the shape of the
# dict and that it is cleared, but nothing ever drove the except branch that
# fills it. The recording could be deleted outright and every test stayed
# green — a guard wired into no path, which is the first failure CLAUDE.md §1
# names.

class _Boom(Exception):
    """A SlackApiError as far as _api_error_code is concerned: the useful part
    lives in .response['error'], not in str(e)."""
    def __init__(self, code):
        super().__init__("The request to the Slack API failed.")
        self.response = {"error": code}


class _RaisingClient:
    def __init__(self, code):
        self._code = code
    def chat_postMessage(self, **kw):
        raise _Boom(self._code)


@pytest.fixture
def refusing(monkeypatch):
    """post_to_thread with a client that always refuses, and MOCK_MODE off so
    the real path runs."""
    from server.services import slack as S
    def _set(code):
        monkeypatch.setattr(S, "MOCK_MODE", False)
        monkeypatch.setattr(S, "_user", _RaisingClient(code))
        monkeypatch.setattr(S, "_bot", _RaisingClient(code))
        return S
    return _set


def _post(S):
    import asyncio
    return asyncio.run(S.post_to_thread("C1", "1.0", "body", as_user=True))


def test_a_refusal_records_the_code_it_was_given(refusing):
    S = refusing("not_in_channel")
    assert _post(S) is None
    assert S.last_post_failure["code"] == "not_in_channel", S.last_post_failure


def test_a_refusal_records_the_sentence_and_the_verdict(refusing):
    S = refusing("ratelimited")
    _post(S)
    assert S.last_post_failure["verdict"] == "retry", S.last_post_failure
    assert S.last_post_failure["why"], S.last_post_failure
    assert S.last_post_failure["next"], S.last_post_failure


def test_two_different_refusals_do_not_report_the_same_thing(refusing):
    """The defect in one assertion: every rejection used to share a sentence
    naming channel membership."""
    S = refusing("ratelimited")
    _post(S)
    first = dict(S.last_post_failure)
    S = refusing("token_revoked")
    _post(S)
    assert dict(S.last_post_failure) != first


def test_a_later_successful_post_clears_the_recorded_failure(monkeypatch):
    """The staleness guard, driven rather than read off the source. A reason
    left behind attaches to a post that went through, and the reader is told
    a successful post was refused."""
    from server.services import slack as S
    import asyncio

    class _OK:
        def chat_postMessage(self, **kw):
            return {"ts": "1.5"}

    S.last_post_failure.update(post_failure_sentence("ratelimited"))
    monkeypatch.setattr(S, "MOCK_MODE", False)
    monkeypatch.setattr(S, "_user", _OK())
    assert asyncio.run(S.post_to_thread("C1", "1.0", "body")) == "1.5"
    assert S.last_post_failure["why"] == "", S.last_post_failure


def test_mock_mode_is_not_recorded_as_a_refusal(monkeypatch):
    """MOCK_MODE returns None too, and it is not a failure — it is the system
    doing as configured. Reporting it as a rejection is the inverse bug: a
    healthy run made to look broken."""
    from server.services import slack as S
    import asyncio
    monkeypatch.setattr(S, "MOCK_MODE", True)
    assert asyncio.run(S.post_to_thread("C1", "1.0", "body")) is None
    assert S.last_post_failure["code"] == "", S.last_post_failure
    assert S.last_post_failure["why"] == "", S.last_post_failure
