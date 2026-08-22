"""An unfinished BigQuery query must not read as an empty warehouse.

THE BUG THIS ENDS. The REST shim (`Client.query`) called `jobs.query` with a
60s server-side `timeoutMs` and then read `rows` straight off the response. But
when the query has not finished COMPUTING in that window, BigQuery answers
HTTP 200 with `jobComplete:false` and NO schema and NO rows — the job is still
running and must be polled via `getQueryResults`. The shim only followed a
`pageToken` (pagination), never `jobComplete`, so a slow query fell through as
`_QueryJob([])`.

That empty list is indistinguishable from "the warehouse has no such row": a
`verify_bid` on a real booking returned None and the review rendered as
untraceable; insights returned zeros that read as "searched and found nothing".
It is CLAUDE.md rule 1 in the layer it matters most in, and it slipped past the
`BQQueryTimeout` guard because it comes back as a 200, not as a timeout.

`google-cloud-bigquery`'s own client polls `jobComplete` internally; this shim
is the deployment path when no service-account key is set, so only it was
affected — but that is production. These tests drive the shim with a faked HTTP
layer: an incomplete first answer must be polled to completion, and a job that
never completes must RAISE, never return [].
"""
import time

import pytest

from server.services import bq_connector as bq


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeRequests:
    """Stands in for the `requests` module inside bq_connector. `post_payloads`
    and `get_payloads` are consumed in order; the last one repeats."""

    def __init__(self, post_payloads, get_payloads):
        self.post_payloads = list(post_payloads)
        self.get_payloads = list(get_payloads)
        self.post_calls = 0
        self.get_calls = 0

    def _next(self, seq, which):
        if which == "post":
            self.post_calls += 1
        else:
            self.get_calls += 1
        if len(seq) > 1:
            return seq.pop(0)
        return seq[0]

    def post(self, *a, **k):
        return _Resp(self._next(self.post_payloads, "post"))

    def get(self, *a, **k):
        # getQueryResults long-polls server-side; here it returns at once, so a
        # small sleep keeps the deadline test from busy-spinning.
        time.sleep(0.02)
        return _Resp(self._next(self.get_payloads, "get"))


_COMPLETE = {
    "jobComplete": True,
    "schema": {"fields": [{"name": "bid", "type": "STRING"}]},
    "rows": [{"f": [{"v": "33204378"}]}],
}
_INCOMPLETE = {"jobComplete": False, "jobReference": {"jobId": "job_1", "location": "US"}}


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setattr(bq, "_settings", lambda force=False: ("tok", "proj"))
    monkeypatch.setattr(bq, "MOCK_MODE", False)


def test_an_incomplete_first_answer_is_polled_to_completion(monkeypatch):
    """jobComplete:false on the POST → poll getQueryResults, and return the rows
    it finally carries. The row exists; the query was just slow."""
    fake = _FakeRequests(post_payloads=[_INCOMPLETE],
                         get_payloads=[_COMPLETE])
    monkeypatch.setattr(bq, "requests", fake)
    rows = bq.run_query("select bid from x")
    assert rows == [{"bid": "33204378"}], rows
    assert fake.get_calls == 1, "it did not poll getQueryResults for the result"


def test_a_job_that_never_completes_raises_rather_than_returning_empty(monkeypatch):
    """THE POINT. [] would mean 'no such booking' to every caller. A job stuck
    on jobComplete:false must raise, not come back empty."""
    fake = _FakeRequests(post_payloads=[_INCOMPLETE],
                         get_payloads=[_INCOMPLETE])   # never completes
    monkeypatch.setattr(bq, "requests", fake)
    monkeypatch.setattr(bq, "BQ_QUERY_TIMEOUT_S", 0.2)
    with pytest.raises(bq.BQQueryTimeout):
        bq.run_query("select bid from x")


def test_the_incomplete_timeout_says_it_is_our_limit_not_a_result(monkeypatch):
    fake = _FakeRequests(post_payloads=[_INCOMPLETE], get_payloads=[_INCOMPLETE])
    monkeypatch.setattr(bq, "requests", fake)
    monkeypatch.setattr(bq, "BQ_QUERY_TIMEOUT_S", 0.2)
    try:
        bq.run_query("select 1")
        raise AssertionError("should have raised")
    except bq.BQQueryTimeout as e:
        msg = str(e).lower()
        assert "not a result" in msg
        assert "may well exist" in msg


def test_a_complete_first_answer_is_not_polled(monkeypatch):
    """Regression: the fast path is untouched — a query that finishes in the
    initial jobs.query call returns its rows with no getQueryResults round-trip."""
    fake = _FakeRequests(post_payloads=[_COMPLETE], get_payloads=[_COMPLETE])
    monkeypatch.setattr(bq, "requests", fake)
    rows = bq.run_query("select bid from x")
    assert rows == [{"bid": "33204378"}]
    assert fake.get_calls == 0, "it polled getQueryResults for an already-complete job"


def test_a_missing_jobComplete_key_is_treated_as_complete(monkeypatch):
    """Defensive: a payload with no jobComplete key (older/edge responses) is
    read as complete, so this fix never turns a real answer into a poll loop."""
    payload = {"schema": {"fields": [{"name": "bid", "type": "STRING"}]},
               "rows": [{"f": [{"v": "1"}]}]}
    fake = _FakeRequests(post_payloads=[payload], get_payloads=[payload])
    monkeypatch.setattr(bq, "requests", fake)
    assert bq.run_query("select 1") == [{"bid": "1"}]
    assert fake.get_calls == 0
