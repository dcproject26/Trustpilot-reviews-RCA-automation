"""The Reporting page's endpoints: the field registry and the ad-hoc query.
Driven through the real app + a real-schema DB (the `client` fixture)."""
from datetime import datetime


# A window no other test writes into. These tests pass date_from/date_to on every
# query so they assert on their OWN rows: without that they pass alone and fail in
# the full suite, which is the worst kind of green.
W = {"date_from": "2027-03-01", "date_to": "2027-03-03"}


def _seed(client):
    """Three reviews across two days, with enough shape to group and filter."""
    from server.db import Review, RcaDraft, SessionLocal
    s = SessionLocal()
    try:
        s.add(Review(id="a", received_at=datetime(2027, 3, 1, 9), status="sent",
                     rating=1, language="English", picked_up_by="Avi"))
        s.add(RcaDraft(id="a-d", review_id="a", match_tier=1, l1="Operations Issue",
                       scenarios=["Refund issues", "Content issues"],
                       booking={"id": "B1", "vendorName": "Acme"},
                       sent_at=datetime(2027, 3, 1, 15)))
        s.add(Review(id="b", received_at=datetime(2027, 3, 1, 10), status="sent",
                     rating=2, language="French", picked_up_by="Test"))
        s.add(RcaDraft(id="b-d", review_id="b", match_tier=2, l1="Product Issue",
                       scenarios=["Refund issues"], booking={"id": "B2"},
                       sent_at=datetime(2027, 3, 1, 12)))
        s.add(Review(id="c", received_at=datetime(2027, 3, 3, 9), status="new",
                     rating=1, language="English"))
        s.commit()
    finally:
        s.close()


def test_fields_lists_every_dimension_and_measure(client):
    r = client.get("/api/reporting/fields")
    assert r.status_code == 200
    d = r.json()
    keys = {x["key"] for x in d["dimensions"]}
    assert {"vendor", "tier", "owner", "scenarios", "tgid"} <= keys
    assert {"count", "median_tts", "solved_pct"} <= {m["key"] for m in d["measures"]}
    assert "Booking details" in d["views"]


def test_query_groups_and_totals(client):
    _seed(client)
    r = client.post("/api/reporting/query", json={
        **W, "dimensions": ["tier"], "measures": ["count", "solved_pct"]})
    assert r.status_code == 200
    d = r.json()
    rows = {tuple(x["key"]): x["values"] for x in d["rows"]}
    assert rows[("Tier 1",)]["count"] == 1
    assert rows[("Tier 2",)]["count"] == 1
    assert rows[("Untraceable",)]["count"] == 1        # the unmatched 'new' one
    assert d["totals"]["count"] == 3
    # totals recomputed over all rows, not summed from the groups
    assert d["totals"]["solved_pct"] == 2 / 3 * 100


def test_query_date_window_is_inclusive_of_date_to(client):
    _seed(client)
    only_first = client.post("/api/reporting/query", json={
        "date_from": "2027-03-01", "date_to": "2027-03-01", "measures": ["count"]}).json()
    assert only_first["totals"]["count"] == 2
    both = client.post("/api/reporting/query", json={
        "date_from": "2027-03-01", "date_to": "2027-03-03", "measures": ["count"]}).json()
    assert both["totals"]["count"] == 3


def test_query_filters_and_unnests(client):
    _seed(client)
    d = client.post("/api/reporting/query", json={
        **W, "dimensions": ["scenarios"], "measures": ["count"]}).json()
    counts = {tuple(x["key"])[0]: x["values"]["count"] for x in d["rows"]}
    assert counts["Refund issues"] == 2 and counts["Content issues"] == 1
    assert d["unnested"] == ["scenarios"]              # told, not hidden
    only = client.post("/api/reporting/query", json={
        **W, "measures": ["count"], "filters": {"scenarios": "Content issues"}}).json()
    assert only["totals"]["count"] == 1


def test_query_reports_unset_rather_than_a_silent_zero(client):
    _seed(client)
    d = client.post("/api/reporting/query", json={
        **W, "dimensions": ["vendor"], "measures": ["count"]}).json()
    # one review has no booking at all, one booking carries no vendor name
    assert d["unset"]["vendor"] >= 1
    assert sum(x["values"]["count"] for x in d["rows"]) == d["matched"]


def test_test_owner_is_not_a_person_but_the_review_still_counts(client):
    _seed(client)
    d = client.post("/api/reporting/query", json={
        **W, "dimensions": ["owner"], "measures": ["count"]}).json()
    owners = {tuple(x["key"])[0] for x in d["rows"]}
    assert "Test" not in owners
    assert "Unassigned" in owners and "Avi" in owners
    assert d["totals"]["count"] == 3                   # nothing dropped


def test_query_rejects_bad_input_with_a_reason(client):
    bad_dim = client.post("/api/reporting/query", json={"dimensions": ["nope"]})
    assert bad_dim.status_code == 422 and "unknown dimension" in bad_dim.json()["detail"]
    bad_meas = client.post("/api/reporting/query", json={"measures": ["nope"]})
    assert bad_meas.status_code == 422 and "unknown measure" in bad_meas.json()["detail"]
    bad_date = client.post("/api/reporting/query", json={"date_from": "01-09-2026"})
    assert bad_date.status_code == 422 and "YYYY-MM-DD" in bad_date.json()["detail"]
    backwards = client.post("/api/reporting/query", json={
        "date_from": "2027-03-05", "date_to": "2027-03-01"})
    assert backwards.status_code == 422
    assert client.post("/api/reporting/query", json={"limit": 0}).status_code == 422


def test_limit_truncates_and_says_so(client):
    _seed(client)
    d = client.post("/api/reporting/query", json={
        **W, "dimensions": ["tier"], "measures": ["count"], "limit": 1}).json()
    assert len(d["rows"]) == 1 and d["row_count"] == 3 and d["truncated"] is True
