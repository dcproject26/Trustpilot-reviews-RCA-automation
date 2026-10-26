"""A Dummy/Uncaptured booking is not a real booking — verify_bid rejects it.

The realtime `fct_bookings_lambda` now carries Dummy and Uncaptured rows that
the old `fct_bookings` did not. Without a guard, a BID the guest quoted that
happens to resolve to one of those would match as a real booking. verify_bid
selects booking_status and refuses those rows, saying so distinctly (rule 1 —
"found but not a real booking" is not the same as "no such BID") rather than
silently returning a phantom.
"""
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


@pytest.mark.parametrize("status", ["Uncaptured", "Dummy"])
def test_a_non_real_booking_status_is_not_matched(monkeypatch, status):
    monkeypatch.setattr(bqc, "run_query", lambda *a, **k: [_row(booking_status=status)])
    assert bp.verify_bid("32908218") is None, (
        f"a {status} booking must not match as a real one")


def test_a_real_booking_is_still_matched(monkeypatch):
    """The inverse guard: the status check must not reject genuine bookings.
    Without this, deleting the match path entirely would also pass the test
    above."""
    monkeypatch.setattr(bqc, "run_query", lambda *a, **k: [_row(booking_status="Captured")])
    monkeypatch.setattr(bq, "_get_booking_extra", lambda bid: {})
    out = bp.verify_bid("32908218")
    assert out is not None and out["id"] == "32908218"


def test_verify_bid_raises_on_query_failure(monkeypatch):
    """verify_bid must propagate query failures so callers can distinguish
    'not found' from 'could not look.'"""
    monkeypatch.setattr(bqc, "run_query",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("table gone")))
    with pytest.raises(RuntimeError, match="table gone"):
        bp.verify_bid("34711365")
