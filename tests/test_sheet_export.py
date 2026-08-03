"""The sheet export, driven end to end against a fake Sheets API.

docs.google.com is not reachable from every environment this runs in, so the
HTTP layer is injected and everything else — row building, the header check,
the upsert planning — is exercised for real. What is NOT covered here is the
live write itself, and that has to be said rather than implied: these tests
prove the rows are right and the plan is right, not that the credential works.

The failure this file mostly exists for: writing COLUMNS-ordered rows into a
sheet whose header is different. Every value lands one column left and the
result reads as plausible data — dates under "author", ids under "rating" —
which no amount of looking at the sheet will reveal.
"""
from datetime import datetime

import pytest

from server.services import sheet_export as SX


class FakeSheet:
    """Records what it was asked to do, and answers like the real thing."""

    def __init__(self, header=None, ids=()):
        self.header = list(header) if header is not None else []
        self.ids = list(ids)
        self.updated = []
        self.appended = []
        self.header_written = False

    def read_column_a_and_header(self):
        return list(self.header), list(self.ids)

    def write_header(self):
        self.header_written = True
        self.header = list(SX.COLUMNS)

    def update_rows(self, updates):
        self.updated.extend(updates)

    def append_rows(self, rows):
        self.appended.extend(rows)


class R:
    """A review, with only the attributes the exporter reads."""
    def __init__(self, rid="tp_1", **kw):
        self.id = rid
        self.received_at = datetime(2026, 8, 1, 9, 30)
        self.author = "Lewis MacAndrew"
        self.rating = 1
        self.language = "en"
        self.body_original = "They cancelled and refused a refund."
        self.body_english = None
        self.status = "draft"
        for k, v in kw.items():
            setattr(self, k, v)


class D:
    """A draft, ditto."""
    def __init__(self, **kw):
        self.booking = {"id": "32994590", "tid": "43605", "vid": "4040",
                        "tgid": "22238", "experienceName": "Colosseum",
                        "vendorName": "Italy Pass", "date_of_visit": "2026-08-04"}
        self.match_tier = 1
        self.match_method = "BID in review text"
        self.l1 = "Operations Issue"
        self.l2 = "Ticket Issues"
        self.sub_themes = ["C. Ticket Delayed"]
        self.scenarios = ["Tickets sent late"]
        self.resolution = "Refund + 25% HOC"
        self.zendesk_ticket_ids = ["34125496", "34256902"]
        self.rca_posted_at = None
        self.sent_at = None
        self.final_response = ""
        self.suggested_response = "Hey Lewis,"
        self.rca_prompt_version = "v4+abc123"
        self.rca_v3 = {
            "tldr": {"our_mistake": "We refused a refund the vendor caused.",
                     "our_fix": "Refund in full and tell RO."},
            "what_went_wrong": {"guest_issues": [
                {"issue": "Refund denied after vendor cancelled",
                 "owner": "CE", "claim_accuracy": "Partly accurate"},
                {"issue": "No escalation to the SP",
                 "owner": "RO", "claim_accuracy": "Accurate"},
            ]},
            "sop_compliance": {"verdict": "deviated"},
            "takedown": {"verdict": "No"},
            "flags": [{"flag": "Vendor cancels at a meaningful rate"}],
        }
        for k, v in kw.items():
            setattr(self, k, v)


# ── the row ─────────────────────────────────────────────────────────────────

def test_a_row_has_a_cell_for_every_column():
    cells = SX.to_cells(SX.row_for(R(), D()))
    assert len(cells) == len(SX.COLUMNS), (
        "a row with the wrong number of cells shifts every value after the "
        "gap into the wrong column")


def test_a_missing_field_is_an_empty_cell_not_a_dropped_one():
    """Dropping it would shift everything after it left."""
    cells = SX.to_cells({"review_id": "tp_1"})
    assert len(cells) == len(SX.COLUMNS)
    assert cells[0] == "tp_1"
    assert all(c == "" for c in cells[1:])


def test_none_never_renders_as_the_word_none():
    """"None" in a cell reads as something somebody typed, and a spreadsheet
    has no other way to say "we did not have this"."""
    cells = SX.to_cells(SX.row_for(R(), None))
    assert "None" not in cells


def test_a_review_with_no_draft_still_exports():
    row = SX.row_for(R(), None)
    assert row["review_id"] == "tp_1"
    assert row["author"] == "Lewis MacAndrew"
    assert row["l1"] == "" and row["issue_count"] == 0


def test_the_rca_fields_are_pulled_out_of_rca_v3():
    row = SX.row_for(R(), D())
    assert row["issue_count"] == 2
    assert row["takedown"] == "No"
    assert row["flags"] == ["Vendor cancels at a meaningful rate"]


def test_the_removed_sections_have_no_columns():
    """TL;DR and SOP compliance were removed from the RCA. Leaving their
    columns would put a permanently empty column in every export, which reads
    as data the pipeline stopped producing rather than a section that is
    gone."""
    assert not [c for c in SX.COLUMNS if "tldr" in c or c.startswith("sop")]


def test_lists_become_one_cell_each():
    cells = dict(zip(SX.COLUMNS, SX.to_cells(SX.row_for(R(), D()))))
    assert cells["issues"] == ("Refund denied after vendor cancelled; "
                               "No escalation to the SP")
    assert cells["owners"] == "CE; RO"
    assert cells["zendesk_tickets"] == "34125496; 34256902"


def test_the_english_body_wins_when_there_is_one():
    row = SX.row_for(R(body_english="They cancelled it."), D())
    assert row["review_text"] == "They cancelled it."


# ── the header check ────────────────────────────────────────────────────────

def test_an_empty_sheet_is_safe():
    assert SX.check_header([]) == ""


def test_a_matching_header_is_safe():
    assert SX.check_header(list(SX.COLUMNS)) == ""


def test_a_header_missing_a_column_is_refused():
    bad = [c for c in SX.COLUMNS if c != "l2"]
    why = SX.check_header(bad)
    assert why, "a mismatched header was accepted"
    assert "l2" in why
    assert "wrong column" in why


def test_a_reordered_header_is_refused():
    """Same columns, different order — every value still lands in the wrong
    place, and the sheet looks entirely reasonable afterwards."""
    swapped = list(SX.COLUMNS)
    swapped[1], swapped[2] = swapped[2], swapped[1]
    why = SX.check_header(swapped)
    assert why, "a reordered header was accepted"


def test_a_refusal_says_what_to_do_about_it():
    why = SX.check_header(["something", "else"])
    assert "nothing was written" in why
    assert "Fix the header row" in why


def test_a_refused_export_writes_nothing():
    io = FakeSheet(header=["wrong", "header"])
    out = SX.export(io, [SX.row_for(R(), D())], apply=True)
    assert out["refused"]
    assert io.updated == [] and io.appended == [], \
        "it refused and wrote anyway"


# ── the upsert ──────────────────────────────────────────────────────────────

def test_a_new_review_is_appended():
    io = FakeSheet(header=list(SX.COLUMNS), ids=["tp_other"])
    out = SX.export(io, [SX.row_for(R("tp_new"), D())], apply=True)
    assert (out["appended"], out["updated"]) == (1, 0)
    assert len(io.appended) == 1


def test_a_review_already_there_is_updated_in_place():
    """Re-running an RCA must replace its row. Appending would fill the sheet
    with stale copies of the same review, all equally plausible."""
    io = FakeSheet(header=list(SX.COLUMNS), ids=["tp_a", "tp_1", "tp_b"])
    out = SX.export(io, [SX.row_for(R("tp_1"), D())], apply=True)
    assert (out["updated"], out["appended"]) == (1, 0)
    assert io.updated[0][0] == 3, \
        "wrong row number — off by the header row, which overwrites a neighbour"


def test_the_row_number_accounts_for_the_header():
    """Row 1 is the header, so the first data row is 2. Off by one here
    overwrites the wrong review, and the sheet still looks fine."""
    io = FakeSheet(header=list(SX.COLUMNS), ids=["tp_first"])
    SX.export(io, [SX.row_for(R("tp_first"), D())], apply=True)
    assert io.updated[0][0] == 2


def test_running_it_twice_appends_nothing_the_second_time():
    io = FakeSheet(header=list(SX.COLUMNS), ids=[])
    rows = [SX.row_for(R("tp_1"), D()), SX.row_for(R("tp_2"), D())]
    SX.export(io, rows, apply=True)
    assert len(io.appended) == 2
    io.ids = ["tp_1", "tp_2"]                  # as the sheet now stands
    out = SX.export(io, rows, apply=True)
    assert out["appended"] == 0 and out["updated"] == 2


def test_a_dry_run_writes_nothing_but_still_reports_the_plan():
    io = FakeSheet(header=list(SX.COLUMNS), ids=["tp_1"])
    out = SX.export(io, [SX.row_for(R("tp_1"), D()),
                         SX.row_for(R("tp_2"), D())], apply=False)
    assert (out["updated"], out["appended"]) == (1, 1)
    assert io.updated == [] and io.appended == [] and not io.header_written


def test_duplicates_already_in_the_sheet_are_named():
    """An upsert updates the first and leaves the rest looking current."""
    io = FakeSheet(header=list(SX.COLUMNS), ids=["tp_1", "tp_2", "tp_1"])
    out = SX.export(io, [SX.row_for(R("tp_1"), D())], apply=True)
    assert out["duplicates"] == ["tp_1"]


def test_no_duplicates_reports_an_empty_list_not_a_missing_key():
    io = FakeSheet(header=list(SX.COLUMNS), ids=["tp_1"])
    out = SX.export(io, [], apply=True)
    assert out["duplicates"] == []


def test_a_blank_id_in_the_sheet_is_not_treated_as_a_review():
    """A trailing empty row is normal in a spreadsheet, and matching a review
    with an empty id against it would overwrite it."""
    io = FakeSheet(header=list(SX.COLUMNS), ids=["tp_1", "", "  "])
    out = SX.export(io, [SX.row_for(R("tp_9"), D())], apply=True)
    assert out["appended"] == 1 and out["updated"] == 0


def test_a_row_with_no_id_does_not_overwrite_a_blank_row():
    """The case the test above cannot reach.

    Every review HAS an id, so a review never looks up "" — which meant
    indexing the sheet's blank rows by "" changed no outcome, and mutation
    testing proved it by removing the guard and killing nothing. A row with no
    review_id does look up "", and without the guard it would silently
    overwrite whatever blank row came first, destroying a spreadsheet row
    nobody asked it to touch.
    """
    io = FakeSheet(header=list(SX.COLUMNS), ids=["tp_1", "", "tp_2"])
    out = SX.export(io, [{"author": "no id at all"}], apply=True)
    assert out["updated"] == 0, "it matched a row on an empty id"
    assert out["appended"] == 1
    assert io.updated == [], f"it wrote over row {io.updated}"


def test_a_whitespace_id_is_treated_the_same_as_a_blank_one():
    io = FakeSheet(header=list(SX.COLUMNS), ids=["  ", "tp_1"])
    out = SX.export(io, [{"review_id": "   ", "author": "x"}], apply=True)
    assert out["updated"] == 0 and io.updated == []


def test_the_header_is_written_into_an_empty_sheet():
    io = FakeSheet(header=[], ids=[])
    out = SX.export(io, [SX.row_for(R(), D())], apply=True)
    assert io.header_written and out["header_written"]
    assert out["appended"] == 1


def test_the_header_is_not_written_on_a_dry_run():
    io = FakeSheet(header=[], ids=[])
    SX.export(io, [SX.row_for(R(), D())], apply=False)
    assert not io.header_written


# ── the write scope ─────────────────────────────────────────────────────────

def test_the_write_asks_for_the_read_write_scope():
    """Negative-ish, and deliberately a constant check: everything else in
    this codebase asks for spreadsheets.readonly, and a readonly token fails
    the write with a 403 that reads like a permission problem on the sheet
    rather than on the token."""
    assert SX.SCOPE_RW == "https://www.googleapis.com/auth/spreadsheets"
    assert not SX.SCOPE_RW.endswith(".readonly")
