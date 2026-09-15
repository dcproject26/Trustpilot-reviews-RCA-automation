"""Bulk-solving reviews from the command line.

It WRITES, so the tests care most about what it refuses to do: never touching an
already-solved review, never guessing a selection, and never reporting a silent
zero when it actually did nothing.
"""
from datetime import datetime

import pytest

from tools.bulk_solve import select, plan, apply, main, CLOSE_REASON


def _seed(live_db):
    from server.db import Review
    s = live_db.SessionLocal()
    s.add(Review(id="bs_draft", received_at=datetime(2026, 9, 5), status="draft",
                 rating=1, picked_up_by=""))
    s.add(Review(id="bs_new", received_at=datetime(2026, 9, 6), status="new", rating=1))
    s.add(Review(id="bs_done", received_at=datetime(2026, 9, 7), status="sent",
                 rating=1, picked_up_by="Paul", closed_at=datetime(2026, 9, 7, 10),
                 close_reason="finished properly"))
    s.add(Review(id="bs_old", received_at=datetime(2026, 8, 1), status="draft", rating=1))
    s.commit()
    return s


def test_selects_by_id_and_names_the_ids_that_do_not_exist(live_db):
    s = _seed(live_db)
    try:
        found, missing = select(s, ids=["bs_draft", "bs_nope"])
        assert {r.id for r in found} == {"bs_draft"}
        # NOT merged into the result: a bad id is a different problem from an id
        # that matched an already-solved review.
        assert missing == ["bs_nope"]
    finally:
        s.close()


def test_selects_by_status_and_date_window(live_db):
    s = _seed(live_db)
    try:
        found, _ = select(s, status="draft",
                          date_from=datetime(2026, 9, 1), date_to=datetime(2026, 9, 30))
        assert {r.id for r in found} == {"bs_draft"}      # bs_old is out of range
    finally:
        s.close()


def test_an_already_solved_review_is_left_alone(live_db):
    """Re-stamping it would rewrite the closed_at of work someone really did and
    silently reassign its owner."""
    s = _seed(live_db)
    try:
        found, _ = select(s, ids=["bs_draft", "bs_done"])
        to_change, already = plan(found, "Devshree")
        assert {r.id for r in to_change} == {"bs_draft"}
        assert {r.id for r in already} == {"bs_done"}
    finally:
        s.close()


def test_apply_writes_owner_status_and_a_reason(live_db):
    from server.db import Review
    s = _seed(live_db)
    try:
        found, _ = select(s, ids=["bs_draft", "bs_new"])
        to_change, _ = plan(found, "Devshree")
        n = apply(s, to_change, "Devshree", now=datetime(2026, 9, 9, 12))
        assert n == 2
        r = s.query(Review).filter_by(id="bs_draft").one()
        assert r.status == "sent"
        assert r.picked_up_by == "Devshree"
        assert r.closed_at == datetime(2026, 9, 9, 12)
        assert r.close_reason == CLOSE_REASON
        # the one that was already done keeps ITS owner and reason
        done = s.query(Review).filter_by(id="bs_done").one()
        assert done.picked_up_by == "Paul" and done.close_reason == "finished properly"
    finally:
        s.close()


def test_a_dry_run_writes_nothing(live_db, capsys):
    from server.db import Review
    s = _seed(live_db)
    s.close()
    main(["--owner", "Devshree", "--ids", "bs_draft"])
    out = capsys.readouterr().out
    assert "Would solve 1" in out
    assert "DRY RUN" in out
    s2 = live_db.SessionLocal()
    try:
        assert s2.query(Review).filter_by(id="bs_draft").one().status == "draft"
    finally:
        s2.close()


def test_apply_reports_what_it_did_and_what_it_could_not(live_db, capsys):
    _seed(live_db).close()
    main(["--owner", "Devshree", "--ids", "bs_draft,bs_done,bs_ghost", "--apply"])
    out = capsys.readouterr().out
    assert "Solved 1" in out
    assert "already solved: 1" in out
    assert "NOT FOUND" in out and "bs_ghost" in out


def test_matching_nothing_says_so_rather_than_looking_like_success(live_db, capsys):
    """A run that changed nothing must not read the same as one that changed
    everything — that is this project's first rule."""
    _seed(live_db).close()
    main(["--owner", "Devshree", "--ids", "bs_done"])       # already solved
    out = capsys.readouterr().out
    assert "Nothing to do" in out
    assert "real answer, not a failure" in out


def test_it_refuses_to_run_with_no_selection():
    """"Solve everything" must not be reachable by leaving the arguments off."""
    with pytest.raises(SystemExit):
        main(["--owner", "Devshree"])


def test_a_bad_date_is_rejected():
    with pytest.raises(SystemExit):
        main(["--owner", "Devshree", "--status", "draft", "--from", "05-09-2026"])
