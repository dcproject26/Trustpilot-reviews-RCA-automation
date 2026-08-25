"""The run diagnostic names the fate of each unfinished row.

WHY IT EXISTS. "The bar will not go away" has five causes that look identical
from the dashboard, and two of them are opposites:

    a claim frozen with retries LEFT   it comes back when the lease lapses
    a claim frozen with retries SPENT  nothing will ever claim it again

The first is work in progress; the second is a finished-but-unreported
deadlock. Telling someone to wait is right for one and wrong for the other, and
the dashboard shows the same "re-running 0/1" for both.

Driven, not source-asserted: what matters is the sentence a reader gets.
"""
import importlib.util
from datetime import datetime, timedelta

import pytest

from server import jobs
from server.tiers import STALL_AFTER_S


def _tool():
    spec = importlib.util.spec_from_file_location("check_runs_tool",
                                                  "tools/check_runs.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Row:
    def __init__(self, **kw):
        self.status = "running"
        self.attempts = 1
        self.max_attempts = 3
        self.lease_expires_at = None
        self.claimed_by = "w1"
        self.review_id = "tp_x"
        self.reason = "fix-incomplete:abc"
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        for k, v in kw.items():
            setattr(self, k, v)


NOW = datetime.utcnow()


def _v(**kw):
    return _tool().verdict(_Row(**kw), NOW, STALL_AFTER_S)


# ── the two that look the same and are opposites ────────────────────────────

def test_a_frozen_claim_with_retries_spent_is_called_stranded():
    state, why = _v(lease_expires_at=NOW - timedelta(minutes=5), attempts=3)
    assert state == "STRANDED", (state, why)
    assert "NOTHING will ever look at it again" in why, why


def test_a_frozen_claim_with_retries_left_is_called_reclaimable():
    state, why = _v(lease_expires_at=NOW - timedelta(minutes=5), attempts=1)
    assert state == "reclaimable", (state, why)
    assert "claims it" in why, why


def test_the_two_do_not_share_a_sentence():
    """The whole point. One says wait, the other says nothing is coming."""
    _s1, stranded = _v(lease_expires_at=NOW - timedelta(minutes=5), attempts=3)
    _s2, retry = _v(lease_expires_at=NOW - timedelta(minutes=5), attempts=1)
    assert stranded != retry
    assert "attempt(s) left" not in stranded, \
        "a stranded run was described as having retries coming"


def test_stranded_matches_what_reap_abandoned_actually_reaps(db_free=None):
    """The tool must not invent its own idea of stranded. Same three
    conditions the query uses: running, lease lapsed, attempts spent."""
    # lease still live -> not stranded, whatever the attempts
    assert _v(lease_expires_at=NOW + timedelta(minutes=5), attempts=9)[0] != "STRANDED"
    # not running -> not stranded
    assert _v(status="queued", lease_expires_at=NOW - timedelta(minutes=5),
              attempts=9)[0] != "STRANDED"
    # attempts left -> not stranded
    assert _v(lease_expires_at=NOW - timedelta(minutes=5), attempts=2)[0] != "STRANDED"


# ── the other three ─────────────────────────────────────────────────────────

def test_a_moving_claim_reads_as_running():
    state, why = _v(lease_expires_at=NOW + timedelta(minutes=10),
                    updated_at=NOW - timedelta(seconds=4))
    assert state == "running", (state, why)
    assert "4s ago" in why, why


def test_a_live_lease_that_stopped_moving_is_not_reported_as_healthy():
    """The instance died and the lease has not lapsed yet. It is not draining
    and it is not yet reclaimable — the honest answer is when it becomes
    reclaimable, not "a worker is on it"."""
    state, why = _v(lease_expires_at=NOW + timedelta(minutes=3),
                    updated_at=NOW - timedelta(seconds=STALL_AFTER_S + 60))
    assert state == "claimed-quiet", (state, why)
    assert "reclaimable" in why, why


def test_a_queued_row_says_what_its_silence_would_mean():
    state, why = _v(status="queued")
    assert state == "queued"
    assert "no drain loop is running" in why, why


# ── the tool runs end to end and does not write unless asked ────────────────

def _run(args=()):
    import subprocess
    import sys
    return subprocess.run([sys.executable, "tools/check_runs.py", *args],
                          capture_output=True, text=True, timeout=180)


def test_it_runs_against_a_real_database_without_a_traceback():
    r = _run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr[-600:]
    assert "run_jobs" in r.stdout


def test_an_empty_table_is_a_clean_answer_not_a_blank():
    """Nothing queued must SAY nothing is queued. A silent report over an empty
    table reads as a tool that did not run."""
    out = _run().stdout
    assert "unfinished runs" in out
    assert ("nothing is queued or running" in out
            or "unfinished runs (" in out), out


def test_it_is_read_only_without_the_flags():
    """--reap and --close are the only things that write."""
    src = open("tools/check_runs.py", encoding="utf-8").read()
    body = src[src.find("def main("):]
    i = body.find("reap_abandoned()")
    assert i != -1, "the reap path is gone"
    assert "if args.reap or args.close" in body[:i], \
        "reap_abandoned is reachable without a flag; the tool is not read-only"
    j = body.find("cancel_batch()")
    assert j != -1 and "if args.close" in body[:j], \
        "cancel_batch is reachable without --close"


def test_it_delegates_rather_than_reimplementing_the_reap():
    src = open("tools/check_runs.py", encoding="utf-8").read()
    assert "jobs.reap_abandoned" in src, "the tool reaps by its own rules"
    assert "STALL_AFTER_S" in src, "the tool invented its own stall threshold"
