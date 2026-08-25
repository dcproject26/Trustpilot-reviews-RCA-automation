"""A Zendesk rate limit is a volume problem, not an empty result.

MEASURED FROM THE CALL PATTERN. One review costs roughly 15-20 Zendesk calls:
three searches, then comments and side conversations per ticket, plus user
lookups. "Fix incomplete" over sixty reviews is on the order of a thousand
calls against a per-minute, per-account limit. Nothing handled 429 — only 401 —
so the first rate-limited search raised, the timeline came back EMPTY, and the
card showed a booking with no events. The associate re-runs, which costs more
calls, which makes it worse.

Two guarantees here, and the second is the rule-1 one:

  * a 429 is retried with backoff, honouring Retry-After, bounded so a retry
    loop cannot outlive the pipeline's own RUN_TIMEOUT_S budget;
  * when it persists it is raised as ZendeskRateLimited and reported AS a rate
    limit — never as "this booking has no tickets", and never as the generic
    "the lookup failed", because those two send the reader to do the one thing
    that makes it worse.
"""
import pytest

from server.services import zendesk as Z
from server.pipeline import timeline_entry


class _Resp:
    def __init__(self, status=429, retry_after=None):
        self.status_code = status
        self.headers = {} if retry_after is None else {"Retry-After": str(retry_after)}


class _Err(Exception):
    def __init__(self, msg="", response=None, status_code=None):
        super().__init__(msg)
        self.response = response
        if status_code is not None:
            self.status_code = status_code


# ── recognising it ──────────────────────────────────────────────────────────

def test_a_429_response_is_a_rate_limit():
    assert Z.rate_limit_wait(_Err("boom", response=_Resp(429))) == 0.0


def test_retry_after_is_honoured():
    """Zendesk states the real number; guessing a smaller one burns a call."""
    assert Z.rate_limit_wait(_Err("boom", response=_Resp(429, 17))) == 17.0


def test_a_wild_retry_after_is_capped():
    """A retry loop that outlives RUN_TIMEOUT_S turns a slow lookup into a
    killed run."""
    got = Z.rate_limit_wait(_Err("boom", response=_Resp(429, 9999)))
    assert got == Z._RATE_LIMIT_MAX_WAIT_S


@pytest.mark.parametrize("msg", ["429 Too Many Requests",
                                 "rate limit exceeded",
                                 "TooManyRequests: too many requests"])
def test_it_is_recognised_from_the_message_too(msg):
    """Not every client surfaces a response object."""
    assert Z.rate_limit_wait(_Err(msg)) is not None


def test_an_ordinary_error_is_not_a_rate_limit():
    assert Z.rate_limit_wait(_Err("500 Internal Server Error")) is None
    assert Z.rate_limit_wait(_Err("401 Unauthorized")) is None


# ── retrying it ─────────────────────────────────────────────────────────────

def test_a_transient_rate_limit_is_retried_and_succeeds(monkeypatch):
    monkeypatch.setattr(Z, "_RATE_LIMIT_MAX_WAIT_S", 0)
    calls = []

    def _fn():
        calls.append(1)
        if len(calls) < 2:
            raise _Err("429 Too Many Requests", response=_Resp(429, 0))
        return "the data"

    assert Z.zd_call(_fn, "probe") == "the data"
    assert len(calls) == 2, "it did not retry"


def test_a_persistent_rate_limit_raises_its_own_error(monkeypatch):
    monkeypatch.setattr(Z, "_RATE_LIMIT_MAX_WAIT_S", 0)

    def _fn():
        raise _Err("429 Too Many Requests", response=_Resp(429, 0))

    with pytest.raises(Z.ZendeskRateLimited) as ei:
        Z.zd_call(_fn, "probe")
    # The message has to carry the instruction, because the obvious one is wrong.
    assert "volume problem" in str(ei.value)
    assert "makes it worse" in str(ei.value)


def test_retries_are_bounded(monkeypatch):
    monkeypatch.setattr(Z, "_RATE_LIMIT_MAX_WAIT_S", 0)
    calls = []

    def _fn():
        calls.append(1)
        raise _Err("429", response=_Resp(429, 0))

    with pytest.raises(Z.ZendeskRateLimited):
        Z.zd_call(_fn, "probe")
    assert len(calls) == Z._RATE_LIMIT_ATTEMPTS, \
        f"tried {len(calls)} times; a retry loop must not outlive the run budget"


def test_a_non_rate_limit_error_is_raised_immediately(monkeypatch):
    calls = []

    def _fn():
        calls.append(1)
        raise ValueError("something else broke")

    with pytest.raises(ValueError):
        Z.zd_call(_fn, "probe")
    assert len(calls) == 1, "a real failure was retried as though it were a 429"


def test_a_successful_call_is_not_retried():
    calls = []
    assert Z.zd_call(lambda: (calls.append(1), "ok")[1], "probe") == "ok"
    assert len(calls) == 1


# ── reporting it ────────────────────────────────────────────────────────────

def test_the_trail_says_rate_limited_not_lookup_failed():
    """The reader's next action differs. "The lookup failed" and "no tickets"
    both mean re-run it; a rate limit is the one empty timeline where
    re-running is exactly wrong."""
    e = Z.ZendeskRateLimited("search: Zendesk rate-limited us 3 times.")
    entry = timeline_entry("33543686", [], [], e)
    assert entry is not None
    t = entry["text"]
    assert "rate-limited" in t.lower()
    assert "nothing is broken" in t
    assert "makes it worse" in t
    assert "the search broke" not in t, \
        "a rate limit is reported as a broken lookup"


def test_an_ordinary_failure_still_reads_as_a_failure():
    """The rate-limit branch must not swallow the generic one."""
    entry = timeline_entry("33543686", [], [], RuntimeError("connection reset"))
    assert "lookup failed" in entry["text"]


def test_events_present_still_says_nothing():
    assert timeline_entry("33543686", [{"time": "x"}], ["1"], None) is None
