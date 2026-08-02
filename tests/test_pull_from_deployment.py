"""Copying the deployment's reviews across without its database password.

The deployment is on a Neon instance whose connection string lives in the
deployment's own secret store — visible to the deployment and to nobody else,
including the shell you would run a migration from. But it already serves its
reviews over HTTP, so the data can come across without anyone hunting for a
credential.

The cost is that the API serves what the dashboard renders, not every column.
Whatever is missing has to be COUNTED and NAMED, or a lossy copy looks exactly
like a complete one until somebody opens a card and finds a blank section.
"""
import sys

import pytest

sys.path.insert(0, "tools")
import pull_from_deployment as P                                  # noqa: E402
import server.db as db                                            # noqa: E402


# ── which fields travel is derived, never hand-listed ───────────────────────

def test_the_field_map_is_derived_from_the_model():
    """The hand-written map was wrong on its first outing: it named 33 columns
    as absent from the payload when 30 of them were present, because I had
    simply not typed them out. A map maintained in step with two other files
    rots silently and then reports the rot as a property of the data."""
    keys = {"id", "l1", "l2", "not_a_column", "rca_v3"}
    got = P._copyable(keys, db.RcaDraft)
    assert "not_a_column" not in got
    assert {"id", "l1", "l2", "rca_v3"} <= set(got)


def test_what_is_missing_is_derived_too():
    absent = P._absent({"l1", "l2"}, db.RcaDraft, ("id", "review_id"))
    assert "rca_v3" in absent and "l1" not in absent
    assert "id" not in absent


def test_a_payload_key_that_is_not_a_column_is_never_written():
    """_draft_dict sends rendering helpers the model has no column for.
    Passing one to the constructor is a TypeError mid-copy."""
    assert P._copyable({"guest_name", "guest_name_note", "match_title"},
                       db.RcaDraft) == []


# ── datetimes ───────────────────────────────────────────────────────────────

def test_an_iso_string_becomes_a_datetime():
    """JSON has no datetime, so every timestamp arrives as a string and the
    typed column rejects it. Found by running the copy, not by reading it."""
    from datetime import datetime
    got = P._coerce(db.RcaDraft, "generated_at", "2026-07-20T09:21:25.954409")
    assert isinstance(got, datetime)
    assert got.year == 2026 and got.minute == 21


def test_a_tz_aware_string_loses_the_tz_for_a_naive_column():
    from datetime import datetime
    got = P._coerce(db.RcaDraft, "generated_at", "2026-07-20T09:21:25+05:30")
    assert isinstance(got, datetime) and got.tzinfo is None


def test_an_unparseable_timestamp_becomes_null_not_a_guess():
    """A date nobody can read is not a date. Inventing one puts a false
    timestamp on a real row."""
    assert P._coerce(db.RcaDraft, "generated_at", "sometime last tuesday") is None


def test_a_non_datetime_column_is_left_alone():
    assert P._coerce(db.RcaDraft, "l1", "Operations Issue") == "Operations Issue"
    assert P._coerce(db.RcaDraft, "rca_v3", {"a": 1}) == {"a": 1}


def test_null_stays_null():
    assert P._coerce(db.RcaDraft, "generated_at", None) is None


def test_a_column_the_model_does_not_have_passes_through():
    assert P._coerce(db.RcaDraft, "nope", "x") == "x"


# ── the stamp has to travel ─────────────────────────────────────────────────

def test_the_prompt_stamp_is_in_the_payload():
    """Without it a copied draft reads as the legacy v3 shape — a migration
    that silently ages every row it moves, and show_draft would tell you to
    re-run rows that are already current."""
    api_src = open("server/api.py", encoding="utf-8").read()
    i = api_src.find("def _draft_dict(")
    assert '"rca_prompt_version": d.rca_prompt_version or ""' in api_src[i:i + 9000]


def test_the_stamp_survives_a_copy():
    from datetime import datetime
    row = {"rca_prompt_version": "rca_v4+375e160b",
           "generated_at": "2026-07-20T09:21:25"}
    out = {k: P._coerce(db.RcaDraft, k, row[k])
           for k in P._copyable(set(row), db.RcaDraft)}
    assert out["rca_prompt_version"] == "rca_v4+375e160b"
    assert isinstance(out["generated_at"], datetime)
