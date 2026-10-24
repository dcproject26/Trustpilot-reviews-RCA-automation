"""Additive dual-write to the ORM master sheet: behaviour tests."""
from datetime import datetime

import server.services.orm_raw_export as O
from tests.test_orm_raw_and_report import _Review, _Draft


def _case():
    rev = _Review(picked_up_by="Devshree")
    rev.slack_channel, rev.slack_ts = "CAB4TQ2AJ", "1792427245.258299"
    rev.review_url = "https://www.trustpilot.com/reviews/abc123"
    d = _Draft(sent_at=datetime(2025, 12, 11, 4, 39, 51),   # 10:09:51 IST
               l1="A", l2="B", booking={"id": "1"},
               rca_v3={"takedown": {"verdict": "No", "reason": "not eligible"}})
    return rev, d


def test_master_columns_shape():
    assert len(O.MASTER_COLUMNS) == 24
    assert O.MASTER_COLUMNS[:21] == O.COLUMNS[:21]
    assert O.MASTER_COLUMNS[21:] == ["Why was review takedown not sent?",
                                     "TP link sent for Takedown",
                                     "Time When the review was reacted to"]
    for c in ("Month", "Year", "Week"):
        assert c not in O.MASTER_COLUMNS
    assert O.COLUMNS[-1] == "Week" and len(O.COLUMNS) == 24   # old untouched


def test_master_row_maps_like_old_for_first_21_and_new_last_3():
    rev, d = _case()
    old, new = O.orm_row_for(rev, d), O.master_row_for(rev, d)
    for c in O.COLUMNS[:21]:
        assert new[c] == old[c]
    assert new["Why was review takedown not sent?"] == "not eligible"
    assert new["TP link sent for Takedown"] == "https://www.trustpilot.com/reviews/abc123"
    assert new["Time When the review was reacted to"] == "11 Dec 2025, 10:09:51"
    assert "Month" not in new


def test_master_row_defaults_blank():
    new = O.master_row_for(_Review(), None)
    assert new["Why was review takedown not sent?"] == ""
    assert new["TP link sent for Takedown"] == ""
    assert new["Time When the review was reacted to"] == ""


class _FakeIO:
    appended = []

    def __init__(self, *a, **k):
        pass

    def read_column_a_and_header(self):
        return (list(O.MASTER_COLUMNS), [])

    def read_range(self, rng):
        return []

    def append_rows(self, rows):
        _FakeIO.appended.extend(rows)


def test_append_master_row_dedups_and_writes_master_cells(monkeypatch):
    monkeypatch.setattr(O, "SheetIO", _FakeIO)
    _FakeIO.appended = []
    rev, d = _case()
    link = O.master_row_for(rev, d)["Link to the review"]
    assert link
    skip = O.append_master_row(rev, d, apply=True, existing_links={link})
    assert not skip["wrote"] and "already on the sheet" in skip["skipped"]
    ok = O.append_master_row(rev, d, apply=True, existing_links=set())
    assert ok["wrote"] and len(_FakeIO.appended[0]) == 24
    assert _FakeIO.appended[0][23] == "11 Dec 2025, 10:09:51"


def test_append_master_row_refuses_old_header(monkeypatch):
    class Old(_FakeIO):
        def read_column_a_and_header(self):
            return (list(O.COLUMNS), [])
    monkeypatch.setattr(O, "SheetIO", Old)
    _FakeIO.appended = []
    rev, d = _case()
    out = O.append_master_row(rev, d, apply=True, existing_links=set())
    assert not out["wrote"] and out["header_problem"] and not _FakeIO.appended


def test_master_failure_never_breaks_old_write(monkeypatch):
    calls = []
    monkeypatch.setattr(O, "append_row",
                        lambda *a, **k: calls.append("old") or {"wrote": True})

    def boom(*a, **k):
        raise RuntimeError("master down")
    monkeypatch.setattr(O, "append_master_row", boom)
    rev, d = _case()
    assert O.on_review_finished(rev, d) == {"wrote": True}
    assert calls == ["old"]


def test_master_written_after_old_and_respects_flag(monkeypatch):
    order = []
    monkeypatch.setattr(O, "append_row", lambda *a, **k: order.append("old") or {})
    monkeypatch.setattr(O, "append_master_row",
                        lambda *a, **k: order.append("master") or {})
    rev, d = _case()
    O.on_review_finished(rev, d)
    assert order == ["old", "master"]
    import server.config as cfg
    monkeypatch.setattr(cfg, "ORM_MASTER_EXPORT_ENABLED", False)
    order.clear()
    O.on_review_finished(rev, d)
    assert order == ["old"]


def test_write_solved_day_master_dedups(live_db, monkeypatch):
    from server.db import Review
    monkeypatch.setattr(O, "SheetIO", _FakeIO)
    _FakeIO.appended = []
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="tp_m1", slack_ts="1.1", slack_channel="C1", rating=1,
                     language="en", body_original="b", status="sent",
                     received_at=datetime(2025, 12, 11, 1, 0),
                     closed_at=datetime(2025, 12, 11, 4, 39, 51)))
        s.commit()
        dry = O.write_solved_day_master(s, day="2025-12-11", apply=False)
        assert dry["solved"] == 1 and dry["wrote"] == 0
        out = O.write_solved_day_master(s, day="2025-12-11", apply=True)
        assert out["solved"] == 1 and out["wrote"] == 1
        other = O.write_solved_day_master(s, day="2025-12-12", apply=True)
        assert other["solved"] == 0
    finally:
        s.close()
