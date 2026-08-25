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


# ── a claimed row a dead worker left behind is not "current" ────────────────
# THE BUG THESE CLOSE, observed on the repl. A `git pull` restarted the app
# mid-batch. The row claimed by the dying container kept status `running` on a
# lease with fourteen minutes left, so `batch_status` — which picked
# `running[0]` out of an unordered list — put that review on the progress bar
# and it never moved. Two rows were `running` in a drain that runs one at a
# time, which is itself the tell.
#
# The discriminator is already written every stage: note_progress bumps
# updated_at on the row a worker is actually on, so a live claim moves and an
# abandoned one is frozen at the instant it was claimed.

def _touch(db, review_id, seconds_ago):
    """Backdate a row's updated_at, as an abandoned claim's would be."""
    from datetime import datetime, timedelta
    s = db.SessionLocal()
    try:
        s.query(db.RunJob).filter(db.RunJob.review_id == review_id).update(
            {db.RunJob.updated_at: datetime.utcnow() - timedelta(seconds=seconds_ago)},
            synchronize_session=False)
        s.commit()
    finally:
        s.close()


def test_current_names_the_run_a_worker_is_on_not_an_abandoned_claim(db):
    _seed(db, "tp_dead", "tp_live", "tp_wait")
    jobs.start_batch(["tp_dead", "tp_live", "tp_wait"])
    _set_status(db, "tp_dead", "running")
    _set_status(db, "tp_live", "running")
    _touch(db, "tp_dead", 40 * 60)      # claimed, then its container died
    _touch(db, "tp_live", 5)            # writing progress right now

    st = jobs.batch_status()
    assert st["current"] == "tp_live", (
        f"the bar named {st['current']!r} — a review nothing is processing")
    assert st["current_state"] == "running"
    assert st["stalled"] == 1, "the abandoned claim was not counted"


def test_every_claim_frozen_reads_as_stalled_and_never_as_starting(db):
    """An empty `current` had three causes and the bar said "starting" to all
    three. After a restart mid-batch that word is false: nothing is draining,
    and nothing will until the leases lapse."""
    _seed(db, "tp_a", "tp_b")
    jobs.start_batch(["tp_a", "tp_b"])
    _set_status(db, "tp_a", "running")
    _touch(db, "tp_a", 40 * 60)

    st = jobs.batch_status()
    assert st["current"] == ""
    assert st["current_state"] == "stalled", st["current_state"]
    assert st["stalled"] == 1
    # It is unfinished work, not lost work: it is reclaimed on lease lapse.
    assert st["remaining"] == 2 and st["failed"] == 0


def test_queued_with_nothing_claimed_is_waiting_not_running(db):
    _seed(db, "tp_a", "tp_b")
    jobs.start_batch(["tp_a", "tp_b"])
    st = jobs.batch_status()
    assert st["current"] == "" and st["current_state"] == "waiting"
    assert st["stalled"] == 0


def test_a_finished_batch_claims_no_state_at_all(db):
    _seed(db, "tp_a")
    jobs.start_batch(["tp_a"])
    _set_status(db, "tp_a", "done")
    assert jobs.batch_status()["current_state"] == ""


def test_no_batch_reports_the_same_keys_as_a_live_one(db):
    """A caller reading current_state must not get one shape when a batch
    exists and a shorter one when none does — that is how a client ends up
    branching on undefined and rendering the wrong empty."""
    _seed(db, "tp_a")
    jobs.start_batch(["tp_a"])
    live = set(jobs.batch_status())
    s = db.SessionLocal()
    try:
        s.query(db.RunJob).delete()
        s.commit()
    finally:
        s.close()
    assert set(jobs.batch_status()) == live


def test_liveness_uses_the_same_threshold_as_the_in_process_reader(db):
    """A run must not read alive in the bulk bar and stalled on its own card.
    tiers.liveness is the other reader; both take STALL_AFTER_S."""
    from datetime import datetime, timedelta
    from server.tiers import STALL_AFTER_S

    class _Row:
        created_at = None
        def __init__(self, ago):
            self.updated_at = datetime.utcnow() - timedelta(seconds=ago)

    assert jobs.worker_liveness(_Row(STALL_AFTER_S - 5))[0] == "running"
    assert jobs.worker_liveness(_Row(STALL_AFTER_S + 5))[0] == "stalled"


def test_a_row_with_no_timestamps_at_all_is_not_reported_as_moving(db):
    """created_at is the floor when updated_at is missing; both missing must
    not read as "moved just now" — that would call a row nobody has touched a
    live worker."""
    from datetime import datetime, timedelta
    from server.tiers import STALL_AFTER_S

    class _Row:
        updated_at = None
        created_at = datetime.utcnow() - timedelta(seconds=STALL_AFTER_S + 60)

    assert jobs.worker_liveness(_Row())[0] == "stalled"
