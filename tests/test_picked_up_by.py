"""The workbench restructure's one backend addition: "Picked up by".

There is no signed-in user, so this is not a claim button — the associate
TYPES their name in the case header and it renders as an inbox column. It
must survive being handed over (editable forever), and an unfilled owner and
a cleared one must remain distinguishable (CLAUDE.md rule 1 applied to a
text field).

Driven end to end: the model column, the read on both /api/reviews and
/api/reviews/{id}, the PATCH write, the trim rule, the length cap, and the
never-set-vs-cleared distinction. The list serializer carries it too, so
the inbox does not need a second round-trip per row.
"""
from datetime import datetime

import pytest

from server.db import Review


def _seed(live_db, **over):
    """One review, keyword-overridable."""
    s = live_db.SessionLocal()
    r = Review(id="tp_p1", slack_ts="tp_p1", slack_channel="C1",
               rating=1, author="Alice", body_original="x",
               language="en", status="new",
               received_at=datetime(2026, 1, 1, 12, 0))
    for k, v in over.items():
        setattr(r, k, v)
    s.add(r)
    s.commit()
    s.close()


# ── the read ────────────────────────────────────────────────────────────────

def test_the_field_defaults_to_null_never_to_an_empty_string(client, live_db):
    """THE POINT. A never-set owner is null, not "" — the two mean different
    things and the client renders "unassigned" only for the first."""
    _seed(live_db)
    got = client.get("/api/reviews/tp_p1").json()["review"]
    assert got["picked_up_by"] is None
    got_list = [r for r in client.get("/api/reviews").json() if r["id"] == "tp_p1"]
    assert got_list and got_list[0]["picked_up_by"] is None


def test_a_seeded_value_reads_back_on_both_endpoints(client, live_db):
    """The inbox column must not need a second round-trip per row."""
    _seed(live_db, picked_up_by="Rhea")
    assert client.get("/api/reviews/tp_p1").json()["review"]["picked_up_by"] == "Rhea"
    row = [r for r in client.get("/api/reviews").json() if r["id"] == "tp_p1"][0]
    assert row["picked_up_by"] == "Rhea"


# ── the write ───────────────────────────────────────────────────────────────

def test_a_typed_name_is_stored_and_returned(client, live_db):
    _seed(live_db)
    r = client.patch("/api/reviews/tp_p1/picked-up-by", json={"name": "Rhea"})
    assert r.status_code == 200
    assert r.json()["picked_up_by"] == "Rhea"
    assert client.get("/api/reviews/tp_p1").json()["review"]["picked_up_by"] == "Rhea"


def test_the_field_stays_editable_after_being_set(client, live_db):
    """Reviews get handed over. Whatever mechanism arrives, an owner must be
    replaceable — the field is free text, not a claim."""
    _seed(live_db, picked_up_by="Alice")
    r = client.patch("/api/reviews/tp_p1/picked-up-by", json={"name": "Bob"})
    assert r.status_code == 200 and r.json()["picked_up_by"] == "Bob"


def test_trailing_whitespace_does_not_split_the_owner(client, live_db):
    """A name typed with a trailing newline is not a second owner."""
    _seed(live_db)
    r = client.patch("/api/reviews/tp_p1/picked-up-by", json={"name": "  Rhea\n"})
    assert r.json()["picked_up_by"] == "Rhea"


def test_null_clears_the_owner(client, live_db):
    """Explicit unclaim. Sent as null; stored as null; renders as unassigned."""
    _seed(live_db, picked_up_by="Alice")
    r = client.patch("/api/reviews/tp_p1/picked-up-by", json={"name": None})
    assert r.status_code == 200 and r.json()["picked_up_by"] is None


def test_empty_string_is_kept_apart_from_never_set(client, live_db):
    """Rule 1, in a text field. Never claimed (None) and typed-then-cleared
    (empty string) are different facts; a UI can render them differently. So
    this endpoint must not silently coerce "" to None."""
    _seed(live_db)                       # None
    r = client.patch("/api/reviews/tp_p1/picked-up-by", json={"name": ""})
    assert r.status_code == 200
    assert r.json()["picked_up_by"] == ""
    got = client.get("/api/reviews/tp_p1").json()["review"]
    assert got["picked_up_by"] == ""     # not None


def test_a_ridiculous_paste_is_capped_rather_than_silently_stored(client, live_db):
    """TEXT would silently take a paragraph — the inbox column would then
    render half a review as the owner name. Capped at 120 so a real name
    always fits and a paste is visibly truncated."""
    _seed(live_db)
    long = "R" * 5000
    r = client.patch("/api/reviews/tp_p1/picked-up-by", json={"name": long})
    assert r.status_code == 200
    assert len(r.json()["picked_up_by"]) == 120


def test_a_missing_review_returns_404_rather_than_saving_a_ghost(client, live_db):
    r = client.patch("/api/reviews/tp_missing/picked-up-by", json={"name": "Rhea"})
    assert r.status_code == 404


def test_the_field_survives_the_migration_on_a_pre_existing_database(live_db):
    """`ensure_columns()` on init_db() must have added the column — the fixture
    calls init_db(), and inspection here confirms it."""
    from sqlalchemy import inspect
    cols = {c["name"] for c in inspect(live_db.engine).get_columns("reviews")}
    assert "picked_up_by" in cols
