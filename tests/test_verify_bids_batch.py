"""verify_bids_batch fetches multiple BIDs in one BQ query."""
import pytest

from server.services import bigquery_patch as bp
from server.services import bq_connector as bqc
from server.services import bigquery as bq


def _row(**over):
    base = {"booking_id": 32908218, "date_of_booking": "2026-07-01",
            "date_of_visit": "2026-07-10", "tid": "43605", "tgid": "22238",
            "experience_name": "Colosseum Tour", "vendor_id": "4040",
            "vendor_name": "Acme", "fulfilment_type": "Vendor Api",
            "primary_guest_name": "Pat", "booking_status": "Captured"}
    base.update(over)
    return base


def test_batch_returns_dict_per_bid(monkeypatch):
    rows = [_row(booking_id=100), _row(booking_id=200)]
    monkeypatch.setattr(bqc, "run_query", lambda *a, **k: rows)
    monkeypatch.setattr(bq, "_get_booking_extra", lambda bid: {})
    result = bp.verify_bids_batch(["100", "200"])
    assert result["100"] is not None and result["100"]["id"] == "100"
    assert result["200"] is not None and result["200"]["id"] == "200"


def test_batch_absent_bid_returns_none(monkeypatch):
    monkeypatch.setattr(bqc, "run_query", lambda *a, **k: [_row(booking_id=100)])
    monkeypatch.setattr(bq, "_get_booking_extra", lambda bid: {})
    result = bp.verify_bids_batch(["100", "999"])
    assert result["100"] is not None
    assert result["999"] is None


def test_batch_skips_dummy_uncaptured(monkeypatch):
    rows = [_row(booking_id=100, booking_status="Dummy"),
            _row(booking_id=200, booking_status="Captured")]
    monkeypatch.setattr(bqc, "run_query", lambda *a, **k: rows)
    monkeypatch.setattr(bq, "_get_booking_extra", lambda bid: {})
    result = bp.verify_bids_batch(["100", "200"])
    assert result["100"] is None
    assert result["200"] is not None


def test_batch_raises_on_query_failure(monkeypatch):
    monkeypatch.setattr(bqc, "run_query",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gone")))
    with pytest.raises(RuntimeError, match="gone"):
        bp.verify_bids_batch(["100"])


def test_batch_empty_input():
    assert bp.verify_bids_batch([]) == {}
