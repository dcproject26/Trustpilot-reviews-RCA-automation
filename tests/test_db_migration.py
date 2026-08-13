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
    arrive as a forty-line traceback that reads like the migration broke.

    THE URL HERE NAMES A DRIVER THAT IS NOT INSTALLED, and that is the whole
    point of the test. It used to say `postgresql://u@127.0.0.1:1/x` — an
    unreachable HOST — and asserted "cannot open the". That string comes from
    `_engine()`, which only fails when `create_engine()` itself raises, and
    `create_engine()` does not connect: it builds lazily and succeeds for any
    unreachable host as long as the driver is importable. psycopg2-binary is
    in requirements.txt, so it always was.

    The run therefore went past `_engine()` to the identity probe, printed
    "Refusing to copy on a guess", and the assertion could not pass on any
    machine WITH psycopg2 — which is every machine that installed this
    project. It was called an environmental failure for a whole session; it
    was a test asserting an outcome its own input cannot produce.

    `psycopg2cffi` is a real postgres driver that this project does not
    install, so `create_engine` raises ModuleNotFoundError and the
    missing-driver path — the one the docstring is about — actually runs."""
    import os
    r = subprocess.run(
        [sys.executable, "tools/migrate_db.py",
         "--from", "postgresql+psycopg2cffi://u@127.0.0.1:1/x",
         "--to", "postgresql+psycopg2cffi://u@127.0.0.1:2/y"],
        capture_output=True, text=True, env=dict(os.environ))
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "Traceback" not in out, out
    assert "cannot open the" in out, out
    assert "row(s) would be copied" not in out, "it planned a copy anyway"


def test_an_unreachable_host_refuses_rather_than_guessing(tmp_path):
    """The path the old version of the test above was ACTUALLY exercising,
    kept because it is a real guarantee and nothing else covered it.

    A reachable driver and a dead host gets past `_engine()`. The identity
    probe then cannot name either side, and copying between two databases you
    cannot tell apart is how a migration overwrites the wrong one."""
    import os
    r = subprocess.run(
        [sys.executable, "tools/migrate_db.py",
         "--from", "postgresql://u@127.0.0.1:1/x",
         "--to", "postgresql://u@127.0.0.1:2/y"],
        capture_output=True, text=True, env=dict(os.environ))
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "Traceback" not in out, out
    assert "Refusing to copy on a guess" in out, out
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


# ── collisions on a NON-primary unique key ──────────────────────────────────
#
# The skip rule matched on the primary key alone. reviews.slack_ts is also
# unique, so a review the target already held under a DIFFERENT id got past
# the skip, the insert raised IntegrityError, and the run died mid-way with
# the earlier tables already committed. "Safe to run twice" was true only for
# the collisions I had thought of, which is not a property, it is a
# coincidence.

def _seed_one(path, rid, ts):
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
        s.add(db.Review(id=rid, slack_ts=ts, slack_channel="C", rating=1,
                        author="A", received_at=datetime(2026, 7, 20)))
        s.commit()
    finally:
        s.close()


@pytest.fixture()
def clashing(tmp_path):
    """The target holds the same Slack message under a different review id."""
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _seed(a, range(3))                       # r0/ts 0, r1/ts 1, r2/ts 2
    _seed_one(b, "tp_other", "0")            # same ts as r0, different id
    return str(a), str(b)


def test_a_unique_key_collision_does_not_abort_the_run(clashing):
    src, dst = clashing
    code, out = _run(src, dst, "--apply")
    assert "IntegrityError" not in out, out
    assert "Traceback" not in out, out
    assert _count(dst, "reviews") == 3, out


def test_the_collision_is_counted_as_already_present(clashing):
    _, out = _run(*clashing)
    assert "2 new" in out and "1 already on target" in out, out
    assert "slack_ts" in out, \
        "the run does not say it matched on anything but the primary key"


def test_it_is_still_safe_to_run_twice(clashing):
    """Nothing doubles, and the row that could not be copied is STILL named.

    Falling silent on the second run would be the worse bug: a rerun reporting
    a clean finish while the same draft is still missing reads as "it worked
    this time" — the whole point of counting what could not be done is that
    the count does not disappear once it stops being news.
    """
    src, dst = clashing
    _run(src, dst, "--apply")
    n_r, n_d = _count(dst, "reviews"), _count(dst, "rca_drafts")
    code, out = _run(src, dst, "--apply")
    assert (_count(dst, "reviews"), _count(dst, "rca_drafts")) == (n_r, n_d), \
        "the second run doubled rows"
    assert "0 row(s) copied" in out or "Nothing to copy" in out, out
    assert "held back" in out, \
        "the second run stopped mentioning the row it still cannot copy"


# ── a child whose parent was skipped ────────────────────────────────────────

def test_a_draft_is_not_copied_without_its_review(clashing):
    """SQLite does not enforce foreign keys, so this does not even raise: it
    writes a draft the dashboard's join can never find, which renders as a
    blank card with no reason given."""
    import sqlite3
    src, dst = clashing
    _run(src, dst, "--apply")
    orphans = sqlite3.connect(dst).execute(
        "select count(*) from rca_drafts d "
        "left join reviews r on d.review_id = r.id where r.id is null"
    ).fetchone()[0]
    assert orphans == 0, f"{orphans} draft(s) point at a review that is not there"


def test_the_held_back_child_is_named_not_dropped_silently(clashing):
    """A migration that quietly loses rows looks exactly like a complete one
    until somebody goes looking for a review that is not there."""
    src, dst = clashing
    code, out = _run(src, dst, "--apply")
    assert "held back because their parent row is not on the target" in out, out
    assert "d0" in out, out
    assert code == 1, "an incomplete copy exited as though it were complete"


def test_a_clean_run_still_exits_zero(pair):
    src, dst = pair
    code, out = _run(src, dst, "--apply")
    assert code == 0, out
    assert "held back" not in out


# ── NULL is not a duplicate ─────────────────────────────────────────────────
#
# reviews.slack_ts is unique AND nullable — a review added by hand has no
# Slack message behind it. SQL permits any number of NULLs in a unique column,
# so two rows with slack_ts NULL are not duplicates. Matching on the tuple
# alone made them look like one, so the first manual review copied and every
# one after it was silently dropped and counted as "already on target".

def _seed_manual(path, ids):
    """Reviews with no Slack message, as the add-by-hand form creates them."""
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
            s.add(db.Review(id=f"m{i}", slack_ts=None, slack_channel="C_MANUAL",
                            rating=1, author="A",
                            received_at=datetime(2026, 7, 20)))
        s.commit()
    finally:
        s.close()


def test_null_unique_values_are_not_treated_as_duplicates(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _seed_manual(a, range(3))          # three hand-added reviews, all ts NULL
    _seed_manual(b, [9])               # one already on the target, also NULL
    code, out = _run(str(a), str(b), "--apply")
    assert _count(str(b), "reviews") == 4, (
        "hand-added reviews were dropped as duplicates of each other — SQL "
        "allows many NULLs in a unique column:\n" + out)


def test_they_are_not_reported_as_already_present(tmp_path):
    """The count is what makes the loss invisible: 'already on target' reads
    as nothing to do."""
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _seed_manual(a, range(3))
    _seed_manual(b, [9])
    _, out = _run(str(a), str(b))
    assert "3 new" in out and "0 already on target" in out, out


# ── a row the TARGET refuses ────────────────────────────────────────────────
#
# The target's schema can be ahead of the source's. A constraint that exists
# only there is not in the source's key list, so the skip cannot see it and
# the insert is refused. That used to abort the table and everything after it,
# with the tables before it already committed.

def test_one_refused_row_does_not_abort_the_rest(tmp_path):
    import sqlite3
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _seed(a, range(4), drafts=False)
    _seed(b, [], drafts=False)
    # A uniqueness rule the source does not have, so the skip cannot see it.
    sqlite3.connect(b).execute(
        "create unique index ix_author_only on reviews (author)")
    code, out = _run(str(a), str(b), "--apply")
    assert "Traceback" not in out, out
    assert _count(str(b), "reviews") == 1, (
        "the first row went in and the rest were abandoned:\n" + out)
    assert "REFUSED by the target" in out, out
    assert "still only on the source" in out, out
    assert code == 1, "a partial copy exited as though it were complete"


def test_a_unique_index_on_the_source_is_honoured(tmp_path):
    """A unique INDEX, not a unique CONSTRAINT. SQLAlchemy reports the two
    through different inspector calls, and a bare CREATE UNIQUE INDEX — which
    is how an index added by hand to the live database shows up — appears only
    in get_indexes()."""
    import sqlite3
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _seed(a, [], drafts=False)
    _seed(b, [], drafts=False)
    for p in (a, b):
        sqlite3.connect(p).execute(
            "create unique index ix_by_author on reviews (author)")
    src = sqlite3.connect(a)
    for i in range(3):
        src.execute("insert into reviews (id, slack_ts, slack_channel, rating, "
                    "author, received_at) values (?, ?, 'C', 1, ?, "
                    "'2026-07-20 00:00:00')", (f"r{i}", str(i), f"Guest {i}"))
    src.commit()
    # The target already holds Guest 0, under an id the source does not use —
    # so only the unique index can tell it is the same row.
    dst = sqlite3.connect(b)
    dst.execute("insert into reviews (id, slack_ts, slack_channel, rating, "
                "author, received_at) values ('other', '99', 'C', 1, "
                "'Guest 0', '2026-07-20 00:00:00')")
    dst.commit()

    _, out = _run(str(a), str(b))
    assert "2 new" in out and "1 already on target" in out, (
        "the unique index was not consulted, so a row the target already holds "
        "under it is counted as new and will be refused:\n" + out)
    assert "author" in out, "the run does not say it matched on the index"
