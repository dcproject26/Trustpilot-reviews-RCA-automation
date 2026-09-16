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
    # 'b' was Tier 2 and picked_up_by="Test" — excluded as non-production data.
    assert rows[("Tier 1",)]["count"] == 1
    assert ("Tier 2",) not in rows
    assert rows[("Untraceable",)]["count"] == 1        # the unmatched 'new' one
    assert d["totals"]["count"] == 2                   # 'b' excluded
    # totals recomputed over all rows, not summed from the groups
    assert d["totals"]["solved_pct"] == 1 / 2 * 100    # only 'a' remains solved


def test_query_date_window_is_inclusive_of_date_to(client):
    _seed(client)
    only_first = client.post("/api/reporting/query", json={
        "date_from": "2027-03-01", "date_to": "2027-03-01", "measures": ["count"]}).json()
    assert only_first["totals"]["count"] == 1          # 'b' excluded as test-owner
    both = client.post("/api/reporting/query", json={
        "date_from": "2027-03-01", "date_to": "2027-03-03", "measures": ["count"]}).json()
    assert both["totals"]["count"] == 2


def test_query_filters_and_unnests(client):
    _seed(client)
    d = client.post("/api/reporting/query", json={
        **W, "dimensions": ["scenarios"], "measures": ["count"]}).json()
    counts = {tuple(x["key"])[0]: x["values"]["count"] for x in d["rows"]}
    # 'b' (Refund issues, test-owner) is excluded — Refund issues drops to 1.
    assert counts["Refund issues"] == 1 and counts["Content issues"] == 1
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


def test_test_owner_reviews_are_excluded_from_reporting_entirely(client):
    """Reporting only computes PRODUCTION data — a review worked from a test
    account is dropped everywhere: from totals, from groups, from filters. The
    dashboard is still allowed to show these rows for auditing, but a manager
    reading numbers must never see 5 pending that is really 3 real + 2 test."""
    _seed(client)
    d = client.post("/api/reporting/query", json={
        **W, "dimensions": ["owner"], "measures": ["count"]}).json()
    owners = {tuple(x["key"])[0] for x in d["rows"]}
    # The test-owner row is not surfaced under Test AND not folded into
    # Unassigned — it is dropped from the analytics entirely.
    assert "Test" not in owners
    assert "Avi" in owners
    assert d["totals"]["count"] == 2                   # 'b' (Test) excluded


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
    # Two tiers remain after excluding the test-owner review ('b' was Tier 2).
    assert len(d["rows"]) == 1 and d["row_count"] == 2 and d["truncated"] is True


def test_overview_is_one_call_with_kpis_and_every_card(client):
    """The Overview used to fire ~29 queries (a head + one per card), each
    re-scanning every review — seconds of loading, and 500s under the concurrent
    storm. /overview returns the KPI totals AND every dimension's top-N from a
    SINGLE scan, so the whole page is one request. Its numbers must match what
    the per-field /query returns for the same scope."""
    _seed(client)
    ov = client.post("/api/reporting/overview", json=W).json()
    # KPI totals present and correct (test-owner 'b' excluded -> 2 production).
    assert ov["matched"] == 2
    assert ov["totals"]["count"] == 2
    assert ov["totals"]["solved"] == 1                 # only 'a' is sent & real
    # Every non-date dimension has a card.
    from server.services.reporting_query import DIMENSIONS
    expected = {d.key for d in DIMENSIONS if d.key != "date"}
    assert set(ov["cards"].keys()) == expected
    # A card's rows match the standalone /query for the same field and scope —
    # the Overview and Explore cannot disagree.
    q = client.post("/api/reporting/query", json={
        **W, "dimensions": ["tier"], "measures": ["count"]}).json()
    ov_tier = {tuple(r["key"])[0]: r["values"]["count"] for r in ov["cards"]["tier"]["rows"]}
    q_tier = {tuple(r["key"])[0]: r["values"]["count"] for r in q["rows"]}
    assert ov_tier == q_tier


def test_overview_rejects_a_backwards_window(client):
    r = client.post("/api/reporting/overview",
                    json={"date_from": "2027-03-05", "date_to": "2027-03-01"})
    assert r.status_code == 422
