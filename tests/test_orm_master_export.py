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
    assert len(O.MASTER_COLUMNS) == 27
    assert O.MASTER_COLUMNS[:21] == O.COLUMNS[:21]
    assert O.MASTER_COLUMNS[21:] == ["Why was review takedown not sent?",
                                     "TP link sent for Takedown",
                                     "Time When the review was reacted to",
                                     "Month", "Year", "Week"]
    assert O.COLUMNS[-1] == "Week" and len(O.COLUMNS) == 24   # old untouched


def test_master_row_maps_like_old_for_first_21_and_new_last_3():
    rev, d = _case()
    old, new = O.orm_row_for(rev, d), O.master_row_for(rev, d)
    for c in O.COLUMNS[:21]:
        assert new[c] == old[c]
    assert new["Why was review takedown not sent?"] == "not eligible"
    assert new["TP link sent for Takedown"] == "https://www.trustpilot.com/reviews/abc123"
    assert new["Time When the review was reacted to"] == "11 Dec 2025, 10:09:51"
    # Month/Year/Week come from the base row and are present on the master too.
    assert new["Month"] == old["Month"] and new["Year"] == old["Year"] and new["Week"] == old["Week"]


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

    def append_rows(self, rows, value_input_option="RAW"):
        _FakeIO.last_value_input_option = value_input_option
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
    assert ok["wrote"] and len(_FakeIO.appended[0]) == 27
    assert _FakeIO.appended[0][23] == "11 Dec 2025, 10:09:51"
    # written USER_ENTERED so no stray leading apostrophe lands in the cells
    assert _FakeIO.last_value_input_option == "USER_ENTERED"


def test_append_master_row_refuses_old_header(monkeypatch):
    class Old(_FakeIO):
        def read_column_a_and_header(self):
            return (list(O.COLUMNS), [])
    monkeypatch.setattr(O, "SheetIO", Old)
    _FakeIO.appended = []
    rev, d = _case()
    out = O.append_master_row(rev, d, apply=True, existing_links=set())
    assert not out["wrote"] and out["header_problem"] and not _FakeIO.appended


def test_tp_write_never_touches_old_sheet(monkeypatch):
    """TP logging moved off the old sheet entirely: on_review_finished must
    write ONLY to the master sheet and never call the old-sheet append."""
    calls = []
    monkeypatch.setattr(O, "append_row",
                        lambda *a, **k: calls.append("old") or {"wrote": True})
    monkeypatch.setattr(O, "append_master_row",
                        lambda *a, **k: calls.append("master") or {"wrote": True})
    import server.config as cfg
    monkeypatch.setattr(cfg, "ORM_RAW_EXPORT_ENABLED", True)
    monkeypatch.setattr(cfg, "ORM_MASTER_EXPORT_ENABLED", True)
    monkeypatch.setattr(cfg, "ORM_MASTER_SHEET_ID", "SHEET")
    rev, d = _case()
    res = O.on_review_finished(rev, d)
    assert res == {"wrote": True}
    assert calls == ["master"]          # old sheet untouched


def test_master_failure_is_swallowed_and_reported(monkeypatch):
    """A master-write failure is caught and returned as a non-write with an
    error, not raised into the solve path (CLAUDE rule 1 — named, not silent)."""
    def boom(*a, **k):
        raise RuntimeError("master down")
    monkeypatch.setattr(O, "append_master_row", boom)
    import server.config as cfg
    monkeypatch.setattr(cfg, "ORM_RAW_EXPORT_ENABLED", True)
    monkeypatch.setattr(cfg, "ORM_MASTER_EXPORT_ENABLED", True)
    monkeypatch.setattr(cfg, "ORM_MASTER_SHEET_ID", "SHEET")
    rev, d = _case()
    out = O.on_review_finished(rev, d)
    assert not out["wrote"] and "master down" in out["error"]


def test_tp_write_respects_flags(monkeypatch):
    import server.config as cfg
    monkeypatch.setattr(O, "append_master_row", lambda *a, **k: {"wrote": True})
    monkeypatch.setattr(cfg, "ORM_MASTER_SHEET_ID", "SHEET")
    rev, d = _case()
    monkeypatch.setattr(cfg, "ORM_RAW_EXPORT_ENABLED", False)
    assert O.on_review_finished(rev, d)["skipped"]           # master trigger off
    monkeypatch.setattr(cfg, "ORM_RAW_EXPORT_ENABLED", True)
    monkeypatch.setattr(cfg, "ORM_MASTER_EXPORT_ENABLED", False)
    assert O.on_review_finished(rev, d)["skipped"]           # master sheet off


def _mrow(date="October 24, 2026", source="Trustpilot", query="Op", booking="",
          tgid="", link="", owner="Avi"):
    cells = [""] * len(O.MASTER_COLUMNS)
    cells[0], cells[1], cells[2] = date, source, query
    cells[3], cells[4], cells[7], cells[8] = booking, tgid, link, owner
    return cells


def test_dedupe_key_link_then_booking_fallback():
    with_link = _mrow(link="https://trustpilot.com/reviews/abc", booking="111")
    assert O.master_dedupe_key(with_link) == "https://trustpilot.com/reviews/abc"
    # link-less rows fall back to booking facts — two identical no-link cases
    # collide (so dedup drops the copy); a different booking does not.
    a = _mrow(link="", booking="34603337", owner="Avi")
    b = _mrow(link="", booking="34603337", owner="Avi")
    c = _mrow(link="", booking="999", owner="Avi")
    assert O.master_dedupe_key(a) == O.master_dedupe_key(b)
    assert O.master_dedupe_key(a) != O.master_dedupe_key(c)
    assert O.master_dedupe_key(a)[0] == "nolink"


class _CleanIO:
    def __init__(self, rows):
        self.rows = rows
        self.written = None
    def resolve_tab(self): return "Sheet1"
    def read_column_a_and_header(self): return (list(O.MASTER_COLUMNS), [])
    def read_range(self, rng): return [list(r) for r in self.rows]
    def update_rows(self, updates): self.written = updates


def test_cleanup_dedupes_tp_only_keeps_social_and_sorts():
    # Oct 24 no-link TP case written 3×; two real TP links; AND two distinct
    # same-day/same-owner Instagram rows (blank link + blank booking) that must
    # NOT be collapsed. Days out of order.
    rows = [
        _mrow(date="October 24, 2026", source="Trustpilot", booking="34603337", link=""),  # keep
        _mrow(date="October 21, 2026", source="Trustpilot", link="L21"),
        _mrow(date="October 24, 2026", source="Trustpilot", booking="34603337", link=""),  # dup
        _mrow(date="October 22, 2026", source="Instagram", link="", booking="", owner="Avi"),  # SM keep
        _mrow(date="October 22, 2026", source="Instagram", link="", booking="", owner="Avi"),  # SM keep (distinct case)
        _mrow(date="October 24, 2026", source="Trustpilot", booking="34603337", link=""),  # dup
    ]
    io = _CleanIO(rows)
    dry = O.cleanup_master_sheet(apply=False, _io=io)
    # 6 rows, 2 TP dups removed, both Instagram rows kept → 4 kept
    assert dry["total"] == 6 and dry["kept"] == 4 and dry["removed"] == 2

    io2 = _CleanIO([list(r) for r in rows])
    done = O.cleanup_master_sheet(apply=True, _io=io2)
    assert done["applied"] and done["removed"] == 2
    body = [cells for (n, cells) in io2.written if n in (2, 3, 4, 5)]
    # date order; the two Instagram rows both survive on Oct 22
    assert [b[0] for b in body] == ["October 21, 2026", "October 22, 2026",
                                    "October 22, 2026", "October 24, 2026"]
    assert sum(1 for b in body if b[1] == "Instagram") == 2


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
