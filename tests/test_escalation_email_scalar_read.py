"""The vendor escalation email is read from a scalar column, not a nested STRUCT.

The REST query shim (bq_connector._convert) only deserializes scalars — a nested
RECORD came back as the raw wire shape {"f":[...]} and `.get("email")` on it was
always None, so the escalation email read blank in production. _get_booking_extra
now selects escalation_email / _type / _name as scalar columns. This drives the
real function with a stubbed warehouse row to prove the scalar values flow
through; if the extraction reverts to reading a `escalation` STRUCT, the row has
no such attribute and the email comes back blank, failing this test.
"""
from types import SimpleNamespace

import server.services.bigquery as bq


def _row():
    return SimpleNamespace(
        booking_status="Completed", tour_name="Colosseum", is_partnered=True,
        escalation_email="escalations@vendor.com", escalation_type="ESCALATIONS",
        escalation_name="Ops Desk",
        contact_types="ESCALATIONS, FINANCE", contact_count=5)


def test_the_escalation_email_flows_through_from_the_scalar_column(monkeypatch):
    monkeypatch.setattr(bq, "_bq", object())
    monkeypatch.setattr(bq, "_bqlib",
                        SimpleNamespace(ScalarQueryParameter=lambda *a, **k: None))
    monkeypatch.setattr(bq, "_run_query", lambda sql, params: [_row()])
    monkeypatch.setattr(bq, "_get_booking_amount", lambda bid_int: {})

    out = bq._get_booking_extra("32908218")
    assert out["escalationEmail"] == "escalations@vendor.com"
    assert out["escalationType"] == "ESCALATIONS"
    assert out["escalationName"] == "Ops Desk"
    # the rest of the enrichment still comes through
    assert out["isPartnered"] is True
    assert out["booking_status"] == "Completed"
    assert out["tid_name"] == "Colosseum"


def test_no_escalation_contact_reads_as_blank_not_an_error(monkeypatch):
    r = _row()
    r.escalation_email = None
    r.escalation_type = None
    r.escalation_name = None
    monkeypatch.setattr(bq, "_bq", object())
    monkeypatch.setattr(bq, "_bqlib",
                        SimpleNamespace(ScalarQueryParameter=lambda *a, **k: None))
    monkeypatch.setattr(bq, "_run_query", lambda sql, params: [r])
    monkeypatch.setattr(bq, "_get_booking_amount", lambda bid_int: {})

    out = bq._get_booking_extra("32908218")
    assert out["escalationEmail"] == ""          # blank, cleanly
    assert out["contactCount"] == 5              # but we still know the vendor has contacts
