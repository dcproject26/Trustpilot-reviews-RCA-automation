"""Purging reviews must take their dependents with them.

There is no other delete path in this codebase and nothing here is undoable from
inside the app, so the parts that decide WHAT goes are driven directly rather
than trusted: the boundary lookup, the selection, and the cascade.

An orphan is the quiet failure here. Deleting only `reviews` leaves rca_drafts,
review_metrics and run_jobs rows that no screen shows and no query cleans up —
and an orphaned queued run_job has the drain loop reaching for a review that no
longer exists.
"""
from datetime import datetime

import pytest

from tools.purge_reviews import boundary, reviews_before, purge


def _seed(live_db, rid, when, status="draft", author="A"):
    s = live_db.SessionLocal()
    try:
        s.add(live_db.Review(id=rid, rating=1, author=author,
                             body_original="b", status=status,
                             received_at=when))
        s.add(live_db.RcaDraft(id=f"draft_{rid}", review_id=rid, booking={}))
        s.add(live_db.ReviewMetric(review_id=rid, received_at=when,
                                   channel="C", rating=1, language="en"))
        s.add(live_db.RunJob(id=f"job_{rid}", review_id=rid, reason="re-run",
                             status="queued", attempts=0,
                             created_at=when, updated_at=when))
        s.commit()
    finally:
        s.close()


@pytest.fixture()
def seeded(live_db):
    _seed(live_db, "tp_old1", datetime(2026, 8, 10, 9, 0))
    _seed(live_db, "tp_old2", datetime(2026, 8, 14, 9, 0), status="sent")
    _seed(live_db, "tp_edge", datetime(2026, 8, 15, 10, 44, 49))
    _seed(live_db, "tp_new1", datetime(2026, 8, 15, 20, 0))
    return live_db


# ── the boundary ───────────────────────────────────────────────────────────

def test_an_unknown_boundary_refuses_and_says_what_to_do(seeded):
    s = seeded.SessionLocal()
    try:
        row, why = boundary(s, seeded, "tp_does_not_exist")
        assert row is None
        assert "no review" in why
        # It must not stop at "not found": in a project with a Development
        # database beside a Production one, the likeliest cause is the wrong
        # connection, and the refusal has to say so and how to change it.
        assert "Development" in why and "DATABASE_URL" in why, why
    finally:
        s.close()


def test_a_boundary_with_no_date_refuses_rather_than_guessing(seeded):
    s = seeded.SessionLocal()
    try:
        s.add(seeded.Review(id="tp_undated", rating=1, body_original="b",
                            status="new", received_at=None))
        s.commit()
        row, why = boundary(s, seeded, "tp_undated")
        assert row is None
        assert "received_at" in why
    finally:
        s.close()


# ── the selection ──────────────────────────────────────────────────────────

def test_the_boundary_review_itself_is_kept(seeded):
    """STRICTLY before. Deleting the row the caller named as the edge is the
    off-by-one nobody notices until the review they were protecting is gone."""
    s = seeded.SessionLocal()
    try:
        edge, _ = boundary(s, seeded, "tp_edge")
        ids = [r.id for r in reviews_before(s, seeded, edge.received_at)]
        assert "tp_edge" not in ids
        assert ids == ["tp_old1", "tp_old2"]
    finally:
        s.close()


def test_nothing_after_the_boundary_is_selected(seeded):
    s = seeded.SessionLocal()
    try:
        edge, _ = boundary(s, seeded, "tp_edge")
        assert "tp_new1" not in [r.id for r in reviews_before(s, seeded, edge.received_at)]
    finally:
        s.close()


def test_an_undated_review_is_never_swept_up(seeded):
    """A NULL received_at is not "before" anything — comparing it would be a
    guess, and this is the one operation where a guess is unrecoverable."""
    s = seeded.SessionLocal()
    try:
        s.add(seeded.Review(id="tp_undated", rating=1, body_original="b",
                            status="new", received_at=None))
        s.commit()
        edge, _ = boundary(s, seeded, "tp_edge")
        assert "tp_undated" not in [r.id for r in reviews_before(s, seeded, edge.received_at)]
    finally:
        s.close()


# ── the cascade ────────────────────────────────────────────────────────────

def test_dependents_go_with_the_review(seeded):
    """THE ORPHAN TEST. Deleting only `reviews` leaves rows nothing shows."""
    s = seeded.SessionLocal()
    try:
        counts = purge(s, seeded, ["tp_old1", "tp_old2"])
        assert counts == {"reviews": 2, "drafts": 2, "metrics": 2, "jobs": 2}
        assert s.query(seeded.RcaDraft).filter(
            seeded.RcaDraft.review_id.in_(["tp_old1", "tp_old2"])).count() == 0
        assert s.query(seeded.ReviewMetric).filter(
            seeded.ReviewMetric.review_id.in_(["tp_old1", "tp_old2"])).count() == 0
        assert s.query(seeded.RunJob).filter(
            seeded.RunJob.review_id.in_(["tp_old1", "tp_old2"])).count() == 0
    finally:
        s.close()


def test_the_survivors_are_untouched(seeded):
    s = seeded.SessionLocal()
    try:
        purge(s, seeded, ["tp_old1", "tp_old2"])
        left = {r.id for r in s.query(seeded.Review).all()}
        assert left == {"tp_edge", "tp_new1"}
        # and their dependents are still there
        assert s.query(seeded.RcaDraft).filter(
            seeded.RcaDraft.review_id == "tp_edge").first() is not None
        assert s.query(seeded.RunJob).filter(
            seeded.RunJob.review_id == "tp_new1").first() is not None
    finally:
        s.close()


def test_purging_nothing_deletes_nothing(seeded):
    s = seeded.SessionLocal()
    try:
        assert purge(s, seeded, []) == {"reviews": 0, "drafts": 0,
                                        "metrics": 0, "jobs": 0}
        assert s.query(seeded.Review).count() == 4
    finally:
        s.close()


def test_the_refusal_says_how_many_are_in_the_database_it_searched(seeded):
    """"no review X" is the same sentence for an id that is wrong and one that
    lives in the OTHER database. The count tells them apart at a glance: 4
    reviews here when production holds 56 says which one you are connected to.

    WHICH database is named by the connect banner in server/db.py, printed once
    per process for every tool that opens a connection — so it does not have to
    be restated in each refusal, and a tool nobody has written yet gets it too.
    """
    s = seeded.SessionLocal()
    try:
        _, why = boundary(s, seeded, "tp_only_in_production")
        assert "4 review(s)" in why, why
        assert "DATABASE_URL" in why, why
    finally:
        s.close()

def test_purging_everything_takes_the_queued_jobs_too(seeded):
    """THE BUG CONSOLIDATION FIXED. This tool predates run_jobs and deleted
    reviews, drafts and metrics only — so a purge left queued jobs behind and
    the drain loop reached for reviews that no longer existed."""
    s = seeded.SessionLocal()
    try:
        counts = purge(s, seeded, None)          # None = everything
        assert counts["reviews"] == 4
        assert counts["jobs"] == 4, "queued run_jobs survived a full purge"
        assert s.query(seeded.RunJob).count() == 0
    finally:
        s.close()


# ── the script itself, not just its parts ──────────────────────────────────

def test_running_the_script_end_to_end_works(tmp_path):
    """DRIVES main(), which the tests above do not.

    THE BUG THIS CATCHES, found only by a human running it: main() assigned
    `before = {...}` in one branch, which made `before` a LOCAL name for the
    whole function and shadowed the module-level `before()` the other branch
    called — UnboundLocalError, on the real production database, on the one
    command that matters. Every test above passed, because they import the
    helpers and never execute main().

    A subprocess, so this is the actual entry point with the actual argv.
    """
    import os
    import subprocess
    import sys
    from datetime import datetime

    db_file = tmp_path / "purge.db"
    url = f"sqlite:///{db_file}"
    env = {**os.environ, "DATABASE_URL": url, "PYTEST_CURRENT_TEST": ""}

    seed = f"""
import os
os.environ["DATABASE_URL"] = {url!r}
import importlib, server.config as cfg; importlib.reload(cfg)
import server.db as d; importlib.reload(d)
d.init_db()
from datetime import datetime
s = d.SessionLocal()
for rid, when in [("tp_old", datetime(2026,8,10)), ("tp_edge", datetime(2026,8,15))]:
    s.add(d.Review(id=rid, rating=1, body_original="b", status="draft",
                   received_at=when))
    s.add(d.RcaDraft(id="dr_"+rid, review_id=rid, booking={{}}))
s.commit(); s.close()
"""
    assert subprocess.run([sys.executable, "-c", seed], capture_output=True,
                          text=True, env=env).returncode == 0

    def run(*a):
        return subprocess.run([sys.executable, "tools/purge_reviews.py", *a],
                              capture_output=True, text=True, env=env)

    dry = run("--before", "tp_edge")
    assert dry.returncode == 0, dry.stderr
    assert "Traceback" not in dry.stderr, dry.stderr
    assert "tp_old" in dry.stdout, dry.stdout
    assert "Dry run" in dry.stdout, dry.stdout

    applied = run("--before", "tp_edge", "--apply")
    assert applied.returncode == 0, applied.stderr
    assert "Traceback" not in applied.stderr, applied.stderr
    assert "deleted:" in applied.stdout, applied.stdout


def test_running_the_script_with_no_boundary_also_works(tmp_path):
    """The other branch of main() — the one whose local assignment caused the
    shadowing. Both paths have to be executed, not just imported."""
    import os
    import subprocess
    import sys

    url = f"sqlite:///{tmp_path / 'all.db'}"
    env = {**os.environ, "DATABASE_URL": url, "PYTEST_CURRENT_TEST": ""}
    subprocess.run([sys.executable, "-c",
                    f"import os; os.environ['DATABASE_URL']={url!r}\n"
                    "import importlib, server.config as c; importlib.reload(c)\n"
                    "import server.db as d; importlib.reload(d); d.init_db()"],
                   capture_output=True, text=True, env=env)
    r = subprocess.run([sys.executable, "tools/purge_reviews.py"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_a_database_with_no_tables_is_named_not_a_driver_error(tmp_path):
    """Pointed at a fresh or wrong DATABASE_URL the tool raised "no such table:
    reviews" from the driver. That is what a WRONG DATABASE looks like from
    here, and the message has to say so rather than leave the reader reading
    sqlite internals."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'empty.db'}",
           "PYTEST_CURRENT_TEST": ""}
    r = subprocess.run([sys.executable, "tools/purge_reviews.py"],
                       capture_output=True, text=True, env=env)
    assert "REFUSING" in r.stdout, r.stdout + r.stderr
    assert "no `reviews` table" in r.stdout, r.stdout
    assert "Traceback" not in r.stderr, r.stderr


# ── a database never reached vs one reached and empty ───────────────────────
#
# These two printed the SAME sentence. `try: db.query(Review).count() except
# Exception:` caught a wrong password, a dead host and a missing table alike,
# and announced all of them as "connected, but this database has no `reviews`
# table" — a guard committing the bug it was written to catch.
#
# It cost a real purge on production: a redacted password was pasted, nothing
# connected, and the tool reported the production database as empty. The only
# evidence was the ABSENCE of db.py's "[db] connected to ..." banner.

def test_a_database_that_was_never_reached_does_not_report_on_its_contents():
    """THE POINT. No connection means nothing is known about the contents, and
    claiming a table is missing is a claim about contents."""
    from sqlalchemy import create_engine
    from tools.purge_reviews import preflight

    out = preflight(create_engine(
        "postgresql://u:p@nonexistent.invalid:5432/db",
        connect_args={"connect_timeout": 3}))
    assert "could not connect" in out, out
    assert "no `reviews` table" not in out, (
        "a database that was never reached was reported as one with no "
        "reviews table — the two are indistinguishable again")


def test_the_connection_failure_quotes_the_driver():
    """"password authentication failed" and "could not translate host name"
    send you to completely different places, so the driver's own words are
    passed through rather than summarised into "connection failed"."""
    from sqlalchemy import create_engine
    from tools.purge_reviews import preflight

    out = preflight(create_engine(
        "postgresql://u:p@nonexistent.invalid:5432/db",
        connect_args={"connect_timeout": 3}))
    assert "could not translate host name" in out, out


def test_a_reachable_database_with_no_schema_still_says_so(tmp_path):
    """The other half must keep working — this is the case the guard was
    originally for, and fixing one must not lose the other."""
    from sqlalchemy import create_engine
    from tools.purge_reviews import preflight

    out = preflight(create_engine(f"sqlite:///{tmp_path / 'empty.db'}"))
    assert "no `reviews` table" in out, out
    assert "could not connect" not in out, out


def test_a_healthy_database_passes_preflight(live_db):
    from tools.purge_reviews import preflight
    assert preflight(live_db.engine) == ""


def test_the_script_refuses_a_bad_password_without_claiming_it_is_empty():
    """End to end, through main() — the layer the earlier tests all skipped,
    which is how an UnboundLocalError shipped from this same file."""
    import os
    import subprocess
    import sys

    env = {**os.environ,
           "DATABASE_URL": "postgresql://u:p@nonexistent.invalid:5432/db",
           "PYTEST_CURRENT_TEST": ""}
    r = subprocess.run([sys.executable, "tools/purge_reviews.py",
                        "--before", "tp_x"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert "REFUSING" in r.stdout, r.stdout + r.stderr
    assert "could not connect" in r.stdout, r.stdout
    assert "no `reviews` table" not in r.stdout, r.stdout
    assert "Traceback" not in r.stderr, r.stderr
