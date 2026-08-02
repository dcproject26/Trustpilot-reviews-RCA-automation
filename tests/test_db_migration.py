"""Moving the deployment onto one database, without losing or doubling a row.

The workspace is on Helium and the published deployment is still on the
Neon-backed legacy instance, because Replit's upgrade touched only the
workspace and deployment secrets are a separate store. Both sides hold rows
the other does not.

pg_dump is the obvious tool and it is wrong here: a plain restore into a
populated target fails on duplicate keys, and --clean drops the rows the
target has that the source does not — which is the 30 reviews the workspace
has been producing since the split. This copies row by row, skipping anything
whose primary key is already on the target.

Two sqlite files stand in for the two Postgres instances. That is exactly why
_identity() answers for sqlite too: a migration I cannot test without two live
Postgres instances is one I would be running against real data untested.
"""
import subprocess
import sys
from datetime import datetime

import pytest


def _seed(path, ids, drafts=True):
    import importlib
    import os
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    import server.config as cfg
    import server.db as db
    importlib.reload(cfg)
    importlib.reload(db)
    db.init_db()
    s = db.SessionLocal()
    try:
        for i in ids:
            s.add(db.Review(id=f"r{i}", slack_ts=str(i), slack_channel="C",
                            rating=1, author="A",
                            received_at=datetime(2026, 7, 20)))
            if drafts:
                s.add(db.RcaDraft(id=f"d{i}", review_id=f"r{i}",
                                  generated_at=datetime(2026, 7, 20)))
        s.commit()
    finally:
        s.close()


def _run(src, dst, *extra):
    r = subprocess.run(
        [sys.executable, "tools/migrate_db.py", "--from", f"sqlite:///{src}",
         "--to", f"sqlite:///{dst}", *extra],
        capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def _count(path, tbl):
    import sqlite3
    return sqlite3.connect(path).execute(f"select count(*) from {tbl}").fetchone()[0]


@pytest.fixture()
def pair(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _seed(a, range(5))
    _seed(b, range(2))          # r0, r1 exist on both
    return str(a), str(b)


# ── the dry run is genuinely dry ────────────────────────────────────────────

def test_a_dry_run_writes_nothing(pair):
    src, dst = pair
    before = _count(dst, "reviews")
    code, out = _run(src, dst)
    assert code == 0, out
    assert "DRY RUN" in out
    assert _count(dst, "reviews") == before, "the dry run wrote to the target"


def test_the_dry_run_says_what_it_would_and_would_not_copy(pair):
    """The skipped count is the number that matters — it is the difference
    between "already there" and "silently not copied"."""
    _, out = _run(*pair)
    assert "3 new" in out and "2 already on target" in out, out
    assert "6 row(s) would be copied, 4 skipped" in out, out


# ── the copy ────────────────────────────────────────────────────────────────

def test_only_the_missing_rows_are_copied(pair):
    src, dst = pair
    code, out = _run(src, dst, "--apply")
    assert code == 0, out
    assert _count(dst, "reviews") == 5
    assert _count(dst, "rca_drafts") == 5


def test_running_it_twice_copies_nothing(pair):
    """A failure part-way leaves some tables done. Re-running has to finish
    the job, not double it."""
    src, dst = pair
    _run(src, dst, "--apply")
    code, out = _run(src, dst, "--apply")
    assert code == 0
    assert "Nothing to copy" in out
    assert _count(dst, "reviews") == 5


def test_the_source_is_never_written(pair):
    src, dst = pair
    before = _count(src, "reviews")
    _run(src, dst, "--apply")
    assert _count(src, "reviews") == before


def test_datetimes_survive_the_copy(pair):
    """A raw SELECT hands back datetimes as strings and the typed INSERT
    rejects them. Found by running it, not by reading it."""
    src, dst = pair
    code, out = _run(src, dst, "--apply")
    assert code == 0, out
    assert "TypeError" not in out and "StatementError" not in out, out


# ── the guards ──────────────────────────────────────────────────────────────

def test_the_same_database_on_both_sides_is_refused(pair):
    """Pointing both at one url is the likeliest mistake, and copying a table
    into itself would double every row."""
    src, _ = pair
    code, out = _run(src, src, "--apply")
    assert code == 0
    assert "SAME database" in out
    assert "Nothing to copy" not in out, "it got as far as planning a copy"
    assert _count(src, "reviews") == 5


def test_it_refuses_when_it_cannot_prove_the_two_are_different(pair, monkeypatch):
    """A Postgres that will not report its system identifier. Copying on a
    guess could double a live table.

    Driven in-process with _identity stubbed. The subprocess version of this
    passed for the wrong reason: with no psycopg2 installed the engine failed
    to open first, so the guard under test was never reached — a mutation that
    deleted it outright stayed green."""
    sys.path.insert(0, "tools")
    import migrate_db
    src, dst = pair
    monkeypatch.setattr(migrate_db, "_identity", lambda e: None)
    monkeypatch.setattr(sys, "argv", ["migrate_db.py", "--from", f"sqlite:///{src}",
                                      "--to", f"sqlite:///{dst}", "--apply"])
    before = _count(dst, "reviews")
    assert migrate_db.main() == 2
    assert _count(dst, "reviews") == before, "it copied without proving anything"


def test_one_side_identifying_is_not_enough(pair, monkeypatch):
    """Half an answer is not an answer. If only the source identifies, the two
    could still be the same database."""
    sys.path.insert(0, "tools")
    import migrate_db
    src, dst = pair
    monkeypatch.setattr(migrate_db, "_identity",
                        lambda e: "abc" if "a.db" in str(e.url) else None)
    monkeypatch.setattr(sys, "argv", ["migrate_db.py", "--from", f"sqlite:///{src}",
                                      "--to", f"sqlite:///{dst}", "--apply"])
    before = _count(dst, "reviews")
    assert migrate_db.main() == 2
    assert _count(dst, "reviews") == before


def test_an_unopenable_database_is_a_sentence_not_a_traceback(tmp_path):
    """A missing driver is a setup problem with a one-line fix, and it used to
    arrive as a forty-line traceback that reads like the migration broke."""
    import os
    r = subprocess.run(
        [sys.executable, "tools/migrate_db.py",
         "--from", "postgresql://u@127.0.0.1:1/x",
         "--to", "postgresql://u@127.0.0.1:2/y"],
        capture_output=True, text=True, env=dict(os.environ))
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "Traceback" not in out, out
    assert "cannot open the" in out
    assert "row(s) would be copied" not in out, "it planned a copy anyway"


def test_a_table_missing_on_the_target_stops_the_run(tmp_path):
    """Half a migration is worse than none. The target needs its schema
    first, and the tool says how to get it."""
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _seed(a, range(3))
    import sqlite3
    sqlite3.connect(b).execute("create table reviews (id text primary key)")
    code, out = _run(str(a), str(b), "--apply")
    assert code == 1, out
    assert "MISSING ON TARGET" in out
    assert "init_db()" in out, "it does not say how to fix it"


def test_reviews_are_copied_before_drafts(pair):
    """A draft whose review is missing breaks the join the whole dashboard is
    built on."""
    _, out = _run(*pair, "--apply")
    assert out.index("copied") < out.index("into rca_drafts")
    assert out.index("into reviews") < out.index("into rca_drafts")
