"""The ORM RAW team-sheet row and the internal daily per-owner report.

Both are driven through their pure builders — `orm_row_for`/`to_cells`/
`check_header` and `count_rows`/`render`/`platform_of` — which take plain
objects and rows, so the 24-column mapping and the tallying are checked without
a live Google Sheet. Only the network append/read are left untested here (they
run against the real sheet from the deployment, where the service-account key
lives).
"""
from datetime import datetime, date

import server.services.orm_raw_export as O
import server.services.orm_daily_report as R


class _Review:
    def __init__(self, **kw):
        self.id = kw.get("id", "tp_x")
        self.picked_up_by = kw.get("picked_up_by", "")
        self.closed_at = kw.get("closed_at")
        self.received_at = kw.get("received_at")
        self.status = "sent"
        self.slack_ts = kw.get("slack_ts", "")
        self.channel = kw.get("channel", "")


class _Draft:
    def __init__(self, **kw):
        self.sent_at = kw.get("sent_at")
        self.l1 = kw.get("l1", "")
        self.l2 = kw.get("l2", "")
        self.resolution = kw.get("resolution", "")
        self.booking = kw.get("booking", {})
        self.ticket_facts = kw.get("ticket_facts", {})
        self.extracted_signals = kw.get("extracted_signals", {})
        self.rca_v3 = kw.get("rca_v3", {})


# ── the ORM RAW row ─────────────────────────────────────────────────────────

def test_the_header_is_exactly_24_columns():
    assert len(O.COLUMNS) == 24
    # the first and last are the ones most likely to drift
    assert O.COLUMNS[0] == "Date" and O.COLUMNS[-1] == "Week"
    assert "What was the issue?" in O.COLUMNS
    assert "Review Take down sent?" in O.COLUMNS


def test_the_mapping_fills_the_fields_we_own():
    rev = _Review(picked_up_by="Devshree")
    d = _Draft(sent_at=datetime(2026, 10, 21, 14, 30),
               l1="Supply Partner Issue", l2="Guide No Show",
               resolution="refund + 25% HOC",
               booking={"id": "33837258", "tgid": "7600",
                        "experienceName": "Accademia Gallery"},
               ticket_facts={"city": "Florence"},
               rca_v3={"outcome_category": "SP issues-[Tour/Guide/Cancellation/Quality/Comms]",
                       "takedown": {"verdict": "Yes"},
                       "actions_taken": {"sp": ["raised"], "guest": []}})
    row = O.orm_row_for(rev, d)
    assert row["Source"] == "Trustpilot"
    assert row["Date"] == "October 21, 2026"
    assert row["Query"] == "Supply Partner Issue / Guide No Show"
    assert row["Booking ID"] == "33837258"
    assert row["TGID"] == "7600"
    assert row["Experience Name"] == "Accademia Gallery"
    assert row["City"] == "Florence"
    assert row["Owner"] == "Devshree"
    assert row["Solution Offered"] == "refund + 25% HOC"
    assert row["What was the issue?"] == "SP issues-[Tour/Guide/Cancellation/Quality/Comms]"
    assert row["Did we raise with the team?"] == "Supply Partner"  # sp, not guest (empty)
    assert row["Review Take down sent?"] == "Yes"
    assert row["Month"] == "Oct" and row["Year"] == "2026" and row["Week"] == "43"


def test_city_prefers_zendesk_then_the_review_indicator():
    """City is the Zendesk booking-details city when present (the actual city),
    else the review's city_or_country indicator (what the card shows)."""
    d = _Draft(sent_at=datetime(2026, 10, 21, 9, 0),
               extracted_signals={"match_indicators": {"city_or_country": "Japan"}},
               ticket_facts={"city": "Osaka"})       # Zendesk wins
    assert O.orm_row_for(_Review(), d)["City"] == "Osaka"
    d2 = _Draft(sent_at=datetime(2026, 10, 21, 9, 0),
                extracted_signals={"match_indicators": {"city_or_country": "Japan"}})
    assert O.orm_row_for(_Review(), d2)["City"] == "Japan"   # indicator fallback


def test_solution_offered_is_the_resolution_dropdown_not_the_empty_freetext():
    """Solution Offered is the structured resolution's compensation_type (the
    card's dropdown), with the amount when present — the free-text column was
    blank on most cards."""
    d = _Draft(sent_at=datetime(2026, 10, 21, 9, 0), resolution="",  # free-text empty
               rca_v3={"resolution": {"compensation_type": "Refund to card",
                                      "amount": None, "text": ""}})
    assert O.orm_row_for(_Review(), d)["Solution Offered"] == "Refund to card"
    d2 = _Draft(sent_at=datetime(2026, 10, 21, 9, 0),
                rca_v3={"resolution": {"compensation_type": "HOC", "amount": "25%"}})
    assert O.orm_row_for(_Review(), d2)["Solution Offered"] == "HOC — 25%"


def test_human_filled_columns_are_left_blank():
    row = O.orm_row_for(_Review(), _Draft(sent_at=datetime(2026, 10, 21, 9, 0)))
    for c in O.HUMAN_FILLED:
        assert row[c] == "", f"{c} should be blank for a human to fill"


def test_date_uses_ist_so_a_late_utc_solve_is_the_next_day():
    # 2026-10-21 20:00 UTC is 2026-10-22 01:30 IST -> the row's day is the 22nd.
    row = O.orm_row_for(_Review(), _Draft(sent_at=datetime(2026, 10, 21, 20, 0)))
    assert row["Date"] == "October 22, 2026", row["Date"]


def test_to_cells_is_in_column_order_and_full_width():
    row = O.orm_row_for(_Review(picked_up_by="Paul"),
                        _Draft(sent_at=datetime(2026, 10, 21, 9, 0)))
    cells = O.to_cells(row)
    assert len(cells) == 24
    assert cells[O.COLUMNS.index("Owner")] == "Paul"
    assert cells[1] == "Trustpilot"


def test_check_header_accepts_the_exact_header_and_rejects_a_drifted_one():
    assert O.check_header(O.COLUMNS) == ""
    assert O.check_header([]) == ""                     # empty sheet is safe
    bad = O.check_header(O.COLUMNS[:-1] + ["Weekk"])
    assert bad and "does not match" in bad


def test_a_spaced_tab_name_is_single_quoted_for_a1():
    assert O.a1_sheet("ORM RAW") == "'ORM RAW'"
    assert O.a1_sheet("Sheet1") == "Sheet1"
    assert O.a1_sheet("O'Brien") == "'O''Brien'"        # internal quote doubled


def test_append_row_dedups_by_review_link(monkeypatch):
    """A case already on the sheet (same review Slack link) is skipped — so the
    write-on-send hook and the 8pm backfill cannot write it twice, and a
    re-sent RCA does not duplicate."""
    class FakeIO:
        def __init__(self, *a, **k):
            self.appended = []
        def read_column_a_and_header(self):
            return (list(O.COLUMNS), [])
        def read_range(self, rng):
            return []
        def append_rows(self, rows):
            self.appended.extend(rows)
    monkeypatch.setattr(O, "SheetIO", FakeIO)
    rev = _Review()
    rev.slack_channel = "CAB4TQ2AJ"
    rev.slack_ts = "1792427245.258299"
    d = _Draft(sent_at=datetime(2026, 10, 21, 9, 0))
    link = O.orm_row_for(rev, d)["Link to the review"]
    assert link, "the test review should produce a Slack link"
    already = O.append_row(rev, d, "s", "ORM RAW", apply=True, existing_links={link})
    assert already["wrote"] is False and "already on the sheet" in already["skipped"]
    fresh = O.append_row(rev, d, "s", "ORM RAW", apply=True, existing_links=set())
    assert fresh["wrote"] is True


def test_dry_run_plans_without_writing():
    out = O.append_row(_Review(picked_up_by="Shruti"),
                       _Draft(sent_at=datetime(2026, 10, 21, 9, 0)),
                       "sheet", "ORM RAW", apply=False)
    assert out["wrote"] is False
    assert out["planned"]["Owner"] == "Shruti"
    assert out["planned"]["Source"] == "Trustpilot"


# ── the daily report ────────────────────────────────────────────────────────

def test_platform_buckets_are_a_denylist_of_two():
    assert R.platform_of("Trustpilot") == R.TRUSTPILOT
    # app feedback is "APP Feedback" (and an older "App Feedback Form"); both
    # match the prefix, an app STORE does not.
    assert R.platform_of("APP Feedback") == R.APP_FEEDBACK
    assert R.platform_of("App Feedback Form") == R.APP_FEEDBACK
    assert R.platform_of("Escalations raised Internally") == R.ESCALATIONS
    for sm in ("Instagram", "Facebook", "PlayStore", "Playstore", "App Store",
               "Appstore", "Twitter", "Linkedin", "whatever"):
        assert R.platform_of(sm) == R.SOCIAL_MEDIA, sm


def _row(datestr, source, owner):
    # ORM RAW is A..I: Date, Source, Query, BID, TGID, Exp, City, Link, Owner
    return [datestr, source, "", "", "", "", "", "", owner]


def test_count_rows_tallies_by_owner_and_platform_for_the_day():
    day = date(2026, 10, 21)
    rows = [
        _row("October 21, 2026", "Trustpilot", "Devshree"),
        _row("October 21, 2026", "Trustpilot", "Devshree"),
        _row("October 21, 2026", "Facebook", "Devshree"),
        _row("October 21, 2026", "App Feedback Form", "Paul"),
        _row("October 20, 2026", "Trustpilot", "Avi"),     # different day
        _row("October 21, 2026", "Trustpilot", "Test"),    # non-owner
        _row("October 21, 2026", "Instagram", "@Shruti"),  # @ stripped
    ]
    counts, meta = R.count_rows(rows, day)
    assert counts["Devshree"][R.SOCIAL_MEDIA] == 1
    assert counts["Devshree"][R.TRUSTPILOT] == 2
    assert counts["Devshree"][R.APP_FEEDBACK] == 0
    assert counts["Paul"][R.APP_FEEDBACK] == 1
    assert counts["Shruti"][R.SOCIAL_MEDIA] == 1
    assert "Avi" not in counts and "Test" not in counts
    assert meta["skipped_non_owner"] == 1


def test_an_unreadable_date_is_counted_not_silently_dropped():
    counts, meta = R.count_rows([_row("not a date", "Trustpilot", "Avi")],
                                date(2026, 10, 21))
    assert counts == {}
    assert meta["unparsed_dates"] == 1


def test_a_spreadsheet_error_cell_is_not_flagged_as_unreadable():
    """A junk cell like #REF! (a broken formula) is skipped silently, not counted
    as an unreadable date — so it never triggers the footnote."""
    for junk in ("#REF!", "#N/A", "#VALUE!"):
        counts, meta = R.count_rows([_row(junk, "Trustpilot", "Avi")],
                                    date(2026, 10, 21))
        assert counts == {} and meta["unparsed_dates"] == 0, junk


def test_several_date_formats_all_parse_to_the_same_day():
    day = date(2025, 4, 1)
    for s in ("April 1, 2025", "1 April 2025", "4/1/2025", "2025-04-01"):
        counts, _ = R.count_rows([_row(s, "Trustpilot", "Devshree")], day)
        assert counts.get("Devshree", {}).get(R.TRUSTPILOT) == 1, s


def test_render_shows_a_table_with_a_total_and_reports_unparsed():
    counts = {"Devshree": {R.SOCIAL_MEDIA: 1, R.TRUSTPILOT: 2, R.APP_FEEDBACK: 0}}
    text = R.render(counts, date(2026, 10, 21), {"unparsed_dates": 1})
    assert "October 21, 2026" in text
    assert "Wednesday" in text          # the weekday now leads the date
    assert "Devshree" in text and "TOTAL" in text
    assert "unreadable date" in text


def test_a_day_with_nothing_says_so():
    text = R.render({}, date(2026, 10, 21), {})
    assert "No cases logged for today" in text


def test_build_report_day_param_targets_a_specific_day(monkeypatch):
    """build_report(day=...) builds THAT day's report, not today's — so a day the
    8pm scheduler missed can be re-posted. Drives the real build_report with the
    sheet read mocked."""
    import server.services.sheet_export as SE
    rows = [
        ["October 22, 2026", "Trustpilot", "", "", "", "", "", "", "Avi"],
        ["October 22, 2026", "Instagram",  "", "", "", "", "", "", "Avi"],
        ["October 23, 2026", "Trustpilot", "", "", "", "", "", "", "Paul"],
    ]
    class FakeIO:
        def __init__(self, *a, **k): self.tab = "ORM RAW"
        def resolve_tab(self): return self.tab
        def read_range(self, rng): return rows
    monkeypatch.setattr(SE, "SheetIO", FakeIO)
    out = R.build_report(day="2026-10-22")
    assert out["day"] == "2026-10-22" and not out["error"]
    assert out["counts"].get("Avi", {}).get(R.TRUSTPILOT) == 1
    assert out["counts"].get("Avi", {}).get(R.SOCIAL_MEDIA) == 1
    assert "Paul" not in out["counts"]          # the 23rd is excluded
    assert "October 22, 2026" in out["text"]


def test_build_report_rejects_a_bad_day():
    out = R.build_report(day="not-a-date")
    assert out["error"] and "YYYY-MM-DD" in out["error"]


def test_build_report_refuses_master_sheet_as_source(monkeypatch):
    """If the report's source is ever the master (TP-only) sheet, it must fail
    loud, not post a table with SM/App/Esc silently 0 (CLAUDE rule 1)."""
    import server.config as cfg
    monkeypatch.setattr(cfg, "ORM_MASTER_SHEET_ID", "SAME")
    monkeypatch.setattr(cfg, "ORM_REPORT_SHEET_ID", "SAME")
    out = R.build_report(day="2026-10-22")
    assert out["error"] and "MASTER" in out["error"]
    assert not out["counts"]


# ── the Sheets connector is matched by name, not an exact slug ───────────────

def test_sheet_connection_matched_by_name_not_exact_slug():
    from server.services.sheets_connector import _pick_sheet
    for slug in ("googlesheets", "google-sheet", "google-sheets", "Google Sheets"):
        items = [{"connector_name": "bigquery"}, {"connector_name": slug, "settings": {"x": 1}}]
        got = _pick_sheet(items)
        assert got and got.get("connector_name") == slug, slug


def test_sheet_connection_absent_returns_none():
    from server.services.sheets_connector import _pick_sheet
    # the exact case we hit: on the account but not bound to this Repl
    assert _pick_sheet([{"connector_name": "zendesk"}, {"connector_name": "bigquery"}]) is None
    assert _pick_sheet([]) is None
