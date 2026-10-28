"""The experience NAME comes from the ORDER, never from a tour_id join.

After the realtime migration the bookings table carries only tour_id / tour_name
— experience_id and experience_name live on fct_orders_lambda. A prior fix
joined dim_experiences ON tour_id = experience_id, which compares two different
id spaces and returns a DIFFERENT experience's name: booking 34711365 showed
"Lunch and the Bow of Ulysses" (experience 4598, = its tour_id) instead of its
real "Thames River Lunch Cruise" (experience_id 2961).

verify_bid now reads the real name through the order (b.order_id = o.order_id)
in an ISOLATED query, so a failure there leaves the tour name and never breaks
the booking fetch.
"""
import pathlib
import re

import pytest

from server.services import bigquery_patch as bp
from server.services import bq_connector as bqc
from server.services import bigquery as bq


def _row(**over):
    base = {"booking_id": 34711365, "date_of_booking": "2026-10-13",
            "date_of_visit": "2026-10-24", "tid": "4598", "tgid": "4598",
            "experience_name": "General",            # b.tour_name — the safe base
            "vendor_id": "4748", "vendor_name": "Musement",
            "fulfilment_type": "Vendor Api", "primary_guest_name": "Martin",
            "booking_status": "Completed"}
    base.update(over)
    return base


# ── the behaviour: the order's name overrides the tour-name base ─────────────

def test_verify_bid_uses_the_experience_name_from_the_order(monkeypatch):
    monkeypatch.setattr(bqc, "run_query", lambda *a, **k: [_row()])
    monkeypatch.setattr(bq, "_get_booking_extra", lambda bid: {})
    monkeypatch.setattr(
        bq, "_experience_from_order",
        lambda bid_int: {"experienceName": "Thames River Lunch Cruise with 2-Course Meal, Coffee & Tea"})
    out = bp.verify_bid("34711365")
    assert out is not None
    assert out["experienceName"] == \
        "Thames River Lunch Cruise with 2-Course Meal, Coffee & Tea", \
        "the real experience name from the order must win over the tour name"


def test_verify_bid_sets_tgid_to_the_experience_id(monkeypatch):
    """TGID IS the experience_id (Tour Group ID = experience_id), NOT the
    tour_id. After the order enrichment, tgid must carry the real experience_id
    so every downstream consumer — insights, Slack, prompts — scopes correctly."""
    monkeypatch.setattr(bqc, "run_query", lambda *a, **k: [_row()])
    monkeypatch.setattr(bq, "_get_booking_extra", lambda bid: {})
    monkeypatch.setattr(
        bq, "_experience_from_order",
        lambda bid_int: {"experienceName": "Wicked", "experienceId": "3037"})
    out = bp.verify_bid("34600921")
    assert out["experienceId"] == "3037", "the real TGID is the order's experience_id"
    assert out["tgid"] == "3037", "tgid must be the experience_id, not the tour_id"
    assert "3037" in (out.get("tgid_link") or ""), \
        "the TGID link must point to the experience page (experience_id)"
    assert "3037" in (out.get("experienceLink") or ""), \
        "the experience link must also use experience_id"


def test_experience_from_order_returns_the_experience_id(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(
        bq, "_run_query",
        lambda sql, params: [SimpleNamespace(experience_name="Wicked", experience_id=3037)])
    if getattr(bq, "_bqlib", None) is None:
        import types as _t
        monkeypatch.setattr(bq, "_bqlib",
                            _t.SimpleNamespace(ScalarQueryParameter=lambda *a, **k: None))
    out = bq._experience_from_order(34600921)
    assert out == {"experienceName": "Wicked", "experienceId": "3037"}


def test_verify_bid_falls_back_to_tour_name_when_the_order_cannot_be_read(monkeypatch):
    """The order enrichment is isolated: if it returns nothing, the booking
    still resolves with the tour name — never the WRONG experience, never a
    broken fetch (CLAUDE.md rule 1)."""
    monkeypatch.setattr(bqc, "run_query", lambda *a, **k: [_row()])
    monkeypatch.setattr(bq, "_get_booking_extra", lambda bid: {})
    monkeypatch.setattr(bq, "_experience_from_order", lambda bid_int: {})
    out = bp.verify_bid("34711365")
    assert out is not None and out["experienceName"] == "General"


def test_experience_from_order_reads_the_orders_table_by_order_id(monkeypatch):
    """The isolated query joins bookings to orders on order_id and reads the
    experience name from the ORDER."""
    captured = {}

    def _fake_run_query(sql, params):
        captured["sql"] = sql
        from types import SimpleNamespace
        return [SimpleNamespace(experience_name="Thames River Lunch Cruise")]

    monkeypatch.setattr(bq, "_run_query", _fake_run_query)
    # _bqlib must be present for ScalarQueryParameter — stub it if absent.
    if getattr(bq, "_bqlib", None) is None:
        import types as _t
        monkeypatch.setattr(bq, "_bqlib",
                            _t.SimpleNamespace(ScalarQueryParameter=lambda *a, **k: None))
    out = bq._experience_from_order(34711365)
    assert out == {"experienceName": "Thames River Lunch Cruise"}
    assert "o.experience_name" in captured["sql"]
    assert "b.order_id = o.order_id" in captured["sql"]


def test_experience_from_order_is_silent_on_failure(monkeypatch):
    """A bad column or an unreachable table costs only this field, returns {}."""
    monkeypatch.setattr(
        bq, "_run_query",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no such column: order_id")))
    assert bq._experience_from_order(34711365) == {}


# ── NEGATIVE source assertions: the wrong join is gone (CLAUDE.md §2) ─────────

_PATCH = pathlib.Path("server/services/bigquery_patch.py").read_text(encoding="utf-8")
_BQ = pathlib.Path("server/services/bigquery.py").read_text(encoding="utf-8")


_CLIENT = pathlib.Path("client/index.html").read_text(encoding="utf-8")


def test_the_card_shows_the_experience_id_as_the_tgid():
    """Client-side JS (no harness) — a source assertion, allowed by CLAUDE.md §2.
    The TGID field and link must prefer experienceId/experienceLink (the real
    experience id on the order) over tgid/tgidLink (the tour id)."""
    assert "b.experienceId || b.tgid" in _CLIENT, \
        "the TGID field no longer prefers the experience_id"
    assert "b.experienceLink || b.tgidLink" in _CLIENT, \
        "the TGID link no longer prefers the experience page link"
    assert "db.experienceId" in _CLIENT, "experienceId is not read from the API payload"


def test_the_tour_id_to_experience_id_join_appears_nowhere():
    """The defect was `ON CAST(b.tour_id ...) = CAST(e.experience_id ...)`. A
    negative source assertion is valid here — unreachability cannot defeat
    'this string appears nowhere' (CLAUDE.md §2)."""
    bug = "CAST(e.experience_id AS INT64)"
    assert bug not in _PATCH, "the tour_id=experience_id join is back in bigquery_patch"
    assert bug not in _BQ, "the tour_id=experience_id join is back in bigquery"
