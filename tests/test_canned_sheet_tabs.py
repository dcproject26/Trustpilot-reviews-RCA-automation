"""The canned sheet has nine tabs. The reader saw one, and could pick the
wrong column inside it.

`tests/fixtures/canned_sheet_tabs.json` is the real sheet, exported to HTML and
reduced to its headers plus four rows a tab. Every claim below is checked
against those actual headers, not against a shape I imagined.

Three faults, in the order they bite:

  1. ONE TAB OF NINE. `/export?format=csv&gid=0` and `values/A:Z` both mean
     "the first tab". The sheet is split BY CHANNEL — "ORM main ( TP ) Macro"
     for Trustpilot, "Macros for SM", "Twitter Macro", "ORM Email Macro" — and
     this dashboard drafts Trustpilot replies. Whether the tone reference was
     even the right channel came down to tab order.

  2. THE WRONG COLUMN. "Twitter Macro" has "Mode of Response" ("Reply to cx
     post") to the LEFT of "Content Approved Template Response" (the actual
     reply). Both match the response keywords and first-match-wins took the
     label.

  3. A MAPPING TAB READ AS REPLIES. "Refer Macro Tags" is 75 rows of issue-type
     names across TP / SM / Twitter. Its header "TP MACRO Issue Type" matches
     BOTH keyword sets, so it parses as 75 flawless canned responses whose text
     is a category label. Handing those to the model as tone examples is worse
     than handing it none: it is confidently wrong and it looks like the sheet
     is working.
"""
import json
import pathlib

import pytest

from server.services.canned import (
    _detect_cols, _median_len, _pick_response_col, _parse_tab, _tab_is_a_mapping,
)

TABS = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "canned_sheet_tabs.json")
    .read_text(encoding="utf-8"))

TP = "ORM main ( TP ) Macro"
TWITTER = "Twitter Macro"
MAPPING = "Refer Macro Tags"


def _rows(tab):
    return _parse_tab(tab, TABS[tab])[0]


def _why(tab):
    return _parse_tab(tab, TABS[tab])[1]


# ── the fixture is the real thing ───────────────────────────────────────────

def test_the_fixture_is_the_sheet_that_was_exported():
    """If this drifts to something invented, every test below stops meaning
    anything about the live sheet."""
    assert len(TABS) == 9, sorted(TABS)
    assert TP in TABS and TWITTER in TABS and MAPPING in TABS
    assert TABS[TP][0][:2] == ["Use Case",
                               "TP / PlayStore / Other Social Media Response MACRO"]


# ── 1. the tab that matters ─────────────────────────────────────────────────

def test_the_trustpilot_tab_parses():
    """The one this dashboard actually needs — the reply goes out via
    Trustpilot on Send."""
    rows = _rows(TP)
    assert rows, _why(TP)
    assert rows[0]["situation"] == "Customer Unable to trace booking"
    assert rows[0]["response"].startswith("Hey <first name>")


def test_every_channel_tab_parses():
    """Each is a different voice for a different surface. Reading one and
    calling it the sheet is how the reply came back in the wrong register."""
    for tab in (TP, TWITTER, "Macros for SM", "ORM Email Macro"):
        assert _rows(tab), f"{tab} contributed nothing: {_why(tab)}"


def test_rows_say_which_tab_they_came_from():
    """Once four tabs are merged, "which voice is this" has to stay
    answerable."""
    assert _rows(TP)[0]["tab"] == TP


# ── 2. the wrong column ─────────────────────────────────────────────────────

def test_the_twitter_tab_reads_the_reply_not_the_channel_label():
    r = _rows(TWITTER)[0]
    assert r["response"] != "Reply to cx post", \
        "the tone example is the name of a channel, not a reply"
    assert len(r["response"]) > 120, r["response"]
    assert "Hey" in r["response"]


def test_the_response_column_is_chosen_by_content_not_position():
    hdr = ["Template Use Case", "Meaning/Context", "Mode of Response",
           "Content Approved Template Response"]
    rows = [["Unable to trace", "Applies when...", "Reply to cx post",
             "Hey <first name>, " + "x" * 300]]
    assert _detect_cols(hdr, rows)["response"] == 3


def test_without_rows_it_falls_back_rather_than_guessing_wrong():
    """The header-only signature is still used, and has to keep working — but
    it cannot resolve this, so it must not pretend to."""
    hdr = ["Use Case", "Response"]
    assert "response" in _detect_cols(hdr)


def test_an_approved_column_wins_over_a_longer_draft_one():
    """The tie-break has to be load-bearing, not decorative. Ordering the
    candidates so max() would pick the wrong one on length alone is the only
    way to tell — with equal lengths max() breaks the tie on the index and the
    assertion passes whatever the bonus does."""
    rows = [["a", "Hey " + "x" * 260, "Hey " + "y" * 200]]
    got = _pick_response_col(["Use Case", "Draft Response",
                              "Content Approved Response"], rows, [1, 2])
    assert got == 2, "the longer draft column beat the approved one"


def test_length_still_decides_when_neither_header_is_special():
    rows = [["a", "Hey " + "x" * 130, "Hey " + "y" * 400]]
    assert _pick_response_col(["Use Case", "Response", "Reply"], rows, [1, 2]) == 2


def test_a_column_of_labels_is_never_chosen_however_it_is_headed():
    rows = [["a", "Reply to cx post", "Approved reply"]]
    assert _pick_response_col(["Use Case", "Mode of Response",
                               "Content Approved Response"], rows, [1, 2]) is None


# ── 3. the mapping tab ──────────────────────────────────────────────────────

def test_the_tag_mapping_tab_is_not_read_as_canned_replies():
    assert _rows(MAPPING) == [], \
        "issue-type labels are being fed to the model as tone examples"


def test_it_says_why_the_mapping_tab_was_dropped():
    """A tab that contributed nothing must be countable, or a sheet where most
    tabs were dropped looks exactly like a sheet with one tab."""
    why = _why(MAPPING)
    assert why and MAPPING in why
    assert "labels, not replies" in why, why


def test_the_mapping_tab_would_otherwise_have_passed():
    """Proof the guard is load-bearing rather than decorative: without the
    length test this tab has both required columns and non-empty cells."""
    hdr = TABS[MAPPING][0]
    col = _detect_cols(hdr, TABS[MAPPING][1:])
    assert "situation" in col and "response" in col, \
        "the header check alone already rejected it, so the guard proves nothing"
    assert _tab_is_a_mapping(hdr, TABS[MAPPING][1:], col)


def test_a_real_tab_is_not_mistaken_for_a_mapping():
    """The inverse. Over-eager rejection empties the sheet and looks identical
    to a sheet nobody shared."""
    hdr = TABS[TP][0]
    assert not _tab_is_a_mapping(hdr, TABS[TP][1:], _detect_cols(hdr, TABS[TP][1:]))


# ── the empty and near-empty tabs ───────────────────────────────────────────

@pytest.mark.parametrize("tab", ["Sheet9", "Content Check", "Takedown Macro"])
def test_a_tab_with_nothing_usable_is_skipped_with_a_reason(tab):
    rows, why = _parse_tab(tab, TABS[tab])
    assert rows == []
    assert why, f"{tab} was dropped silently"
    assert tab in why


def test_median_length_ignores_blank_cells():
    """A column of mostly-empty cells with one long reply must not read as a
    reply column on the strength of the one."""
    assert _median_len([["", ""], ["", ""], ["", "x" * 400]], 1) < 120


def test_a_sheet_of_only_mapping_tabs_reports_that_it_read_fine():
    """"No usable replies" and "could not reach the sheet" are different
    problems with different fixes, and both come back as zero rows."""
    rows, why = _parse_tab(MAPPING, TABS[MAPPING])
    assert not rows
    assert "read" not in why.lower() or "labels" in why, why


# ── _fetch_rows itself ──────────────────────────────────────────────────────
#
# Everything above drives _parse_tab, which is where the parsing lives — and
# every one of those tests stayed green when _fetch_rows was reverted to
# reading only the first tab. A perfect parser called once against tab one is
# the exact shape of "the validator that was wired to nothing".

import asyncio                                                    # noqa: E402

import server.services.canned as C                                # noqa: E402


@pytest.fixture()
def sheet(monkeypatch):
    """A stub standing in for the whole sheet, tab by tab."""
    monkeypatch.setattr(C, "_cache_rows", [], raising=False)
    monkeypatch.setattr(C, "_cache_at", 0, raising=False)
    monkeypatch.setattr(C, "_last_reason", "", raising=False)

    def _serve(tabs):
        monkeypatch.setattr(C, "_tabs_via_service_account",
                            lambda: [(n, TABS[n]) for n in tabs])
        return asyncio.run(C._fetch_rows())
    return _serve


def test_every_tab_is_read_not_just_the_first(sheet):
    """The survivor. Reading tab one only leaves the Trustpilot voice out
    entirely whenever Trustpilot is not tab one — and nothing said so."""
    rows = sheet([MAPPING, TP, TWITTER, "ORM Email Macro"])
    tabs = {r["tab"] for r in rows}
    assert TP in tabs, f"the Trustpilot tab was never read: {tabs}"
    assert TWITTER in tabs and "ORM Email Macro" in tabs, tabs
    assert len(tabs) == 3, f"expected the three reply tabs, got {tabs}"


def test_the_mapping_tab_contributes_nothing_through_the_real_entry_point(sheet):
    rows = sheet([MAPPING, TP])
    assert all(r["tab"] != MAPPING for r in rows)
    assert rows, "the good tab was dropped along with the mapping one"


def test_a_sheet_of_only_mapping_tabs_says_why_it_produced_nothing(sheet):
    """The other survivor. Zero rows here is NOT "the sheet is unreachable" —
    it read fine and held no replies, and those are fixed by different people.
    Without a reason the two are one empty list again."""
    rows = sheet([MAPPING, "Sheet9", "Content Check"])
    assert rows == []
    why = C.last_failure_reason()
    assert why, "the sheet read fine, produced nothing, and said nothing"
    assert "no tab held usable replies" in why, why
    assert MAPPING in why, "it does not name which tabs it rejected"


def test_a_good_read_clears_the_reason(sheet):
    """A stale reason attached to a healthy read is the inverse bug."""
    C._last_reason = "something from last time"
    assert sheet([TP])
    assert C.last_failure_reason() == ""


def test_the_csv_fallback_says_it_can_only_see_one_tab(monkeypatch):
    """When the service account is unavailable the public CSV export can reach
    tab one and no further. A partial read that does not announce itself looks
    exactly like a complete one."""
    import csv as _csv
    import io as _io

    monkeypatch.setattr(C, "_cache_rows", [], raising=False)
    monkeypatch.setattr(C, "_cache_at", 0, raising=False)

    def _no_sa():
        raise RuntimeError("no GCP_SERVICE_ACCOUNT_JSON to authenticate with")
    monkeypatch.setattr(C, "_tabs_via_service_account", _no_sa)

    buf = _io.StringIO()
    _csv.writer(buf).writerows(TABS[TP])

    class _R:
        status_code = 200
        headers = {"content-type": "text/csv"}
        text = buf.getvalue()

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return _R()
    monkeypatch.setattr(C.httpx, "AsyncClient", _Client)

    rows = asyncio.run(C._fetch_rows())
    assert rows, "the CSV fallback produced nothing"
    assert all("first tab only" in r["tab"] for r in rows), \
        "a one-tab read is not labelled as one"
