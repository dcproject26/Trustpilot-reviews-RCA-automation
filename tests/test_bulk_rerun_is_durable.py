""""Fix incomplete" survives the container, and its progress can be seen from
any instance.

THE TWO DEFECTS THIS CLOSES. The bulk re-run was the ONE path still scheduling
work with a fire-and-forget BackgroundTask and keeping its counters in a
module-level dict, while every other re-run path — per-review, candidate
confirmed, bid set by hand, slack refresh — already used durable job rows:

  * the work might never run. A BackgroundTask executes AFTER the response, and
    an autoscale container can be reclaimed the moment the response is sent.
    That is the same defect this project measured at "0 of 9 re-runs executed".
  * the progress could not be seen. A poll landing on another instance found a
    batch that process had never heard of, and reported it as not running — so
    the button looked broken even when the work was fine.

A batch is now a shared `reason` on ordinary job rows, so the drain loop needs
no special case and progress is a query rather than a memory.
"""
import pytest

from server import jobs


@pytest.fixture()
def db(live_db, monkeypatch):
    """Point jobs.py at this test's throwaway database."""
    monkeypatch.setattr(jobs, "_db", lambda: live_db)
    return live_db


def _seed(db, *rids):
    s = db.SessionLocal()
    try:
        for rid in rids:
            s.add(db.Review(id=rid, slack_ts=f"{rid}.0", slack_channel="C",
                            rating=1, body_original="x", status="new"))
        s.commit()
    finally:
        s.close()


def _set_status(db, review_id, status, last_error=None):
    s = db.SessionLocal()
    try:
        q = s.query(db.RunJob).filter(db.RunJob.review_id == review_id)
        vals = {db.RunJob.status: status}
        if last_error is not None:
            vals[db.RunJob.last_error] = last_error
        q.update(vals, synchronize_session=False)
        s.commit()
    finally:
        s.close()


# ── the work is queued durably, not fired and forgotten ─────────────────────

def test_a_batch_writes_one_job_row_per_review(db):
    _seed(db, "tp_1", "tp_2", "tp_3")
    out = jobs.start_batch(["tp_1", "tp_2", "tp_3"])
    assert out["queued"] == ["tp_1", "tp_2", "tp_3"]
    s = db.SessionLocal()
    try:
        rows = s.query(db.RunJob).filter(db.RunJob.reason == out["reason"]).all()
        assert len(rows) == 3
        assert {r.status for r in rows} == {"queued"}
    finally:
        s.close()


def test_a_review_already_booked_is_reported_not_silently_skipped(db):
    """enqueue dedupes, which is right — a bulk run must not start a second run
    over a review someone is already re-running. But then "60 queued" and "60
    looked at, 12 of which were already booked" are the same number, and the
    second is what happened."""
    _seed(db, "tp_1", "tp_2")
    jobs.enqueue("tp_1", "re-run")          # someone is already re-running it
    out = jobs.start_batch(["tp_1", "tp_2"])
    assert out["queued"] == ["tp_2"]
    assert out["already_booked"] == ["tp_1"]


# ── progress is a query, so any instance can answer it ──────────────────────

def test_progress_is_read_from_the_rows_not_from_memory(db):
    _seed(db, "tp_1", "tp_2", "tp_3")
    jobs.start_batch(["tp_1", "tp_2", "tp_3"])
    _set_status(db, "tp_1", "done")
    _set_status(db, "tp_2", "running")

    st = jobs.batch_status()
    assert st["total"] == 3
    assert st["done"] == 1
    assert st["remaining"] == 2
    assert st["running"] is True
    assert st["current"] == "tp_2"


def test_a_dead_job_counts_as_failed_and_never_as_done(db):
    """A bar that reaches 100% while three reviews failed reads as a clean
    run."""
    _seed(db, "tp_1", "tp_2")
    jobs.start_batch(["tp_1", "tp_2"])
    _set_status(db, "tp_1", "done")
    _set_status(db, "tp_2", "dead", last_error="boom")

    st = jobs.batch_status()
    assert st["done"] == 1, "a failed run was counted as completed work"
    assert st["failed"] == 1
    assert st["running"] is False
    assert st["finished_at"] is not None
    bad = [r for r in st["results"] if not r["ok"]]
    assert bad and bad[0]["error"] == "boom", st["results"]


def test_a_finished_batch_reports_not_running(db):
    _seed(db, "tp_1")
    jobs.start_batch(["tp_1"])
    _set_status(db, "tp_1", "done")
    st = jobs.batch_status()
    assert st["running"] is False
    assert st["done"] == st["total"] == 1


def test_no_batch_at_all_is_a_clean_empty_not_a_crash(db):
    st = jobs.batch_status()
    assert st["running"] is False
    assert st["total"] == 0


def test_only_the_newest_batch_is_reported(db):
    """Two runs on the same day must not add up into one bar."""
    _seed(db, "tp_1", "tp_2")
    jobs.start_batch(["tp_1"])
    _set_status(db, "tp_1", "done")
    jobs.start_batch(["tp_2"])
    st = jobs.batch_status()
    assert st["total"] == 1
    assert st["current"] == "" or st["remaining"] == 1


def test_an_eta_is_withheld_until_something_has_finished(db):
    """With no completed run there is no rate to project from, and a number
    invented before the first one lands is a guess wearing a clock."""
    _seed(db, "tp_1", "tp_2")
    jobs.start_batch(["tp_1", "tp_2"])
    assert jobs.batch_status()["eta_s"] is None
    _set_status(db, "tp_1", "done")
    assert jobs.batch_status()["eta_s"] is not None


# ── cancelling ──────────────────────────────────────────────────────────────

def test_cancel_drops_the_queued_but_leaves_the_running(db):
    """A running job holds a lease on an instance that is mid-pipeline; killing
    its row would leave a half-written draft. Cancelling is "start no more",
    not "abandon what is open"."""
    _seed(db, "tp_1", "tp_2", "tp_3")
    jobs.start_batch(["tp_1", "tp_2", "tp_3"])
    _set_status(db, "tp_1", "running")

    dropped = jobs.cancel_batch()
    assert dropped == 2, f"dropped {dropped}"

    s = db.SessionLocal()
    try:
        still = {r.review_id: r.status for r in s.query(db.RunJob).all()}
    finally:
        s.close()
    assert still["tp_1"] == "running", "a mid-pipeline run was killed"
    assert still["tp_2"] == "dead" and still["tp_3"] == "dead"


def test_cancelling_nothing_is_not_an_error(db):
    assert jobs.cancel_batch() == 0
