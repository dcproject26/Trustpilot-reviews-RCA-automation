"""The inbox lists EVERY review, not a capped window.

`/api/reviews` used to `.limit(200)`, so the inbox showed 200 while Reporting
counted them all — the two disagreed with nothing on screen to say why. This
seeds more than the old cap and asserts every row comes back, so the cap can
never quietly return.
"""
from datetime import datetime, timedelta


def _seed(live_db, n):
    from server.db import Review
    s = live_db.SessionLocal()
    try:
        base = datetime(2026, 1, 1, 9, 0)
        for i in range(n):
            s.add(Review(id=f"tp_bulk_{i}", author=f"G{i}", rating=1,
                         status="draft", body_original="x",
                         received_at=base + timedelta(minutes=i)))
        s.commit()
    finally:
        s.close()


def test_the_inbox_returns_more_than_the_old_200_cap(client, live_db):
    _seed(live_db, 205)
    rows = client.get("/api/reviews").json()
    ids = {r["id"] for r in rows if r["id"].startswith("tp_bulk_")}
    # All 205 present — a reinstated .limit(200) would drop at least 5.
    assert len(ids) == 205, f"the inbox capped the list at {len(ids)} rows"
