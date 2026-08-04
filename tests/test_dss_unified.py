"""The new DSS unified view is loaded, and it wins where the two disagree.

"add this to dss script, new chanes, if there are duplications in sceanario,
to follow the new one."

That last clause is the whole design. A scenario present in both the live
sheet and the new export has to resolve to ONE answer — appending the new rows
would leave two, let the selector scorer pick either, and make "the new one
wins" true about half the time while looking like it held always.

The other thing under test is the import itself. "Copy of <tab>" and "<tab>"
are snapshots of the same sheet and they DO differ: the copy of the
meeting-point tab is missing a sentence about explaining to the guest why
proof is being requested. Taking whichever the filesystem listed first would
silently ship a version nobody chose.
"""
import json
import pathlib

import pytest

from server.services.dss import _UNIFIED, _selector_key


# ── the export is loaded ───────────────────────────────────────────────────

def test_the_unified_export_is_checked_in():
    p = pathlib.Path("content/dss_unified.json")
    assert p.exists(), "the export is not checked in — nothing to merge"
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload.get("tabs"), "the export has no tabs"


def test_it_loads_into_the_types_dss_already_routes_to():
    """A row landing in a type nothing routes to is a row nobody can ever
    match — imported, counted, and unreachable."""
    from server.services.dss import TABS
    assert _UNIFIED, "the unified export loaded nothing"
    for t in _UNIFIED:
        assert t in TABS or t == "other", f"{t} is not a DSS type"


def test_every_loaded_row_carries_a_scenario_and_a_recommendation():
    for t, rows in _UNIFIED.items():
        for r in rows:
            assert r.get("scenarios"), f"{t}: a row with no scenario"
            assert r.get("dss"), f"{t}: {r.get('scenarios')!r} has no recommendation"


def test_the_rows_are_marked_as_coming_from_the_export():
    """The reader of a matched row has to be able to tell which sheet it came
    from, or "we followed the new one" is unverifiable after the fact."""
    for rows in _UNIFIED.values():
        assert all(r.get("_unified") for r in rows)


@pytest.mark.parametrize("dss_type", [
    "meetingPointIssue", "supplyPartnerIssue", "cancelation", "delay_fulfilment",
])
def test_each_tab_in_the_export_arrived(dss_type):
    assert _UNIFIED.get(dss_type), f"{dss_type} has no rows from the export"


# ── a missing or broken export costs the new rows, not the lookup ──────────

def test_a_missing_export_returns_nothing_rather_than_raising(monkeypatch):
    import server.services.dss as dss
    monkeypatch.setattr(dss, "_UNIFIED_PATH", "/nonexistent/dss_unified.json")
    assert dss._load_unified() == {}


def test_a_malformed_export_returns_nothing_rather_than_raising(tmp_path, monkeypatch):
    import server.services.dss as dss
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(dss, "_UNIFIED_PATH", str(bad))
    assert dss._load_unified() == {}


# ── the same scenario written differently is the same scenario ─────────────

def test_case_and_punctuation_do_not_make_two_scenarios():
    """The two exports differ by exactly this — "HO Error" against "Ho Error".
    Comparing raw text would carry both and let the older win half the time."""
    assert _selector_key("HO Error wrong meeting point") == \
           _selector_key("Ho Error  wrong  meeting point")
    assert _selector_key("Guide not present at the MP - OGC contact avaialible") == \
           _selector_key("guide not present at the mp  ogc contact avaialible")


def test_different_scenarios_stay_different():
    """A key that collapses everything would supersede the whole live sheet."""
    assert _selector_key("Guide not present at the MP") != \
           _selector_key("HO Error wrong meeting point")


def test_an_empty_selector_has_an_empty_key():
    assert _selector_key("") == ""
    assert _selector_key(None) == ""


# ── the importer ───────────────────────────────────────────────────────────

def test_the_importer_skips_a_copy_when_the_original_is_present(tmp_path):
    """They are snapshots of one tab and they differ. Picking by directory
    order ships a version nobody decided on."""
    import subprocess, sys
    html = ("<table><tr><td>A</td><td>B</td></tr>"
            "<tr><td>1</td><td>Title</td></tr>"
            "<tr><td>2</td><td>Scenarios</td><td>Advice</td></tr>"
            "<tr><td>3</td><td>Late guide</td><td>NEW text</td></tr></table>")
    (tmp_path / "DSS - Meeting point issues.html").write_text(html, encoding="utf-8")
    (tmp_path / "Copy of DSS - Meeting point issues.html").write_text(
        html.replace("NEW text", "OLD text"), encoding="utf-8")
    out = tmp_path / "out.json"
    r = subprocess.run([sys.executable, "tools/import_dss.py", str(tmp_path),
                        "-o", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "SKIPPED" in r.stdout, "the copy was taken silently"
    body = out.read_text(encoding="utf-8")
    assert "NEW text" in body
    assert "OLD text" not in body, "the superseded copy was imported"


def test_the_importer_reports_what_it_imported(tmp_path):
    """A count nobody prints is a run you cannot tell from a no-op."""
    import subprocess, sys
    (tmp_path / "DSS - Other issues.html").write_text(
        "<table><tr><td>A</td></tr><tr><td>1</td><td>Scenarios</td><td>Advice</td></tr>"
        "<tr><td>2</td><td>Lost ticket</td><td>Ask for proof</td></tr></table>",
        encoding="utf-8")
    out = tmp_path / "o.json"
    r = subprocess.run([sys.executable, "tools/import_dss.py", str(tmp_path),
                        "-o", str(out)], capture_output=True, text=True)
    assert "row(s)" in r.stdout and "tab(s)" in r.stdout, r.stdout


def test_the_importer_fails_loudly_on_an_empty_directory(tmp_path):
    import subprocess, sys
    r = subprocess.run([sys.executable, "tools/import_dss.py", str(tmp_path),
                        "-o", str(tmp_path / "o.json")],
                       capture_output=True, text=True)
    assert r.returncode != 0, "an empty import reported success"


# ── the merge: new replaces, never appends ─────────────────────────────────

def _drive(monkeypatch, live_rows, unified_rows, l2="Meeting Point Issues",
           review="the guide was not at the meeting point"):
    """get_recommendation against a stubbed live sheet and export."""
    import asyncio
    import server.services.dss as dss

    async def fake_tabs():
        return {"meetingPointIssue": live_rows, "cancelation": [],
                "supplyPartnerIssue": [], "delay_fulfilment": []}

    monkeypatch.setattr(dss, "_get_tabs", fake_tabs)
    monkeypatch.setattr(dss, "is_live", lambda *a, **k: True)
    monkeypatch.setattr(dss, "_UNIFIED", unified_rows)
    return asyncio.run(dss.get_recommendation(
        booking={"id": "1", "isPartnered": "Yes", "amountUSD": 50},
        review_id="tp_1", l1="Operations Issue", l2=l2, review_text=review))


SCEN = "Guide not present at the MP"


def test_the_new_row_replaces_the_live_one_for_the_same_scenario(monkeypatch):
    """Not appended. Two rows for one scenario let the scorer pick either, so
    "the new one wins" would hold about half the time and look like always."""
    got = _drive(monkeypatch,
                 live_rows=[{"scenarios": SCEN, "dss": "OLD guidance"}],
                 unified_rows={"meetingPointIssue": [
                     {"_unified": True, "scenarios": SCEN, "dss": "NEW guidance"}]})
    blob = str(got)
    assert "NEW guidance" in blob, got
    assert "OLD guidance" not in blob, "the superseded live row is still reachable"


def test_it_supersedes_across_a_spelling_difference(monkeypatch):
    """The real exports differ by exactly this. Raw-text comparison would keep
    both."""
    got = _drive(monkeypatch,
                 live_rows=[{"scenarios": "HO Error wrong meeting point",
                             "dss": "OLD guidance"}],
                 unified_rows={"meetingPointIssue": [
                     {"_unified": True, "scenarios": "Ho Error  wrong meeting point",
                      "dss": "NEW guidance"}]},
                 review="ho error wrong meeting point")
    assert "OLD guidance" not in str(got)


def test_a_live_scenario_the_export_does_not_cover_survives(monkeypatch):
    """The export is partial. Superseding a scenario it never mentions would
    delete guidance rather than update it."""
    got = _drive(monkeypatch,
                 live_rows=[{"scenarios": "Venue closed unexpectedly",
                             "dss": "LIVE ONLY guidance"}],
                 unified_rows={"meetingPointIssue": [
                     {"_unified": True, "scenarios": SCEN, "dss": "NEW guidance"}]},
                 review="the venue was closed unexpectedly when we arrived")
    assert "LIVE ONLY guidance" in str(got), got


def test_an_empty_export_leaves_the_live_sheet_alone(monkeypatch):
    got = _drive(monkeypatch,
                 live_rows=[{"scenarios": SCEN, "dss": "OLD guidance"}],
                 unified_rows={})
    assert "OLD guidance" in str(got)


# ── the export's one shape against four tabs' four selector columns ────────

def _drive_tab(monkeypatch, tab, live_rows, unified_rows, l1, l2, review):
    """As `_drive`, but the live rows land in the named tab.

    Cancellations and delays score on their own columns, not on `scenarios`,
    so a merge that only works for meeting-point rows passes every test above.
    """
    import asyncio
    import server.services.dss as dss

    async def fake_tabs():
        empty = {"meetingPointIssue": [], "cancelation": [],
                 "supplyPartnerIssue": [], "delay_fulfilment": []}
        return {**empty, tab: live_rows}

    monkeypatch.setattr(dss, "_get_tabs", fake_tabs)
    monkeypatch.setattr(dss, "is_live", lambda *a, **k: True)
    monkeypatch.setattr(dss, "_UNIFIED", unified_rows)
    return asyncio.run(dss.get_recommendation(
        booking={"id": "1", "isPartnered": "Yes", "amountUSD": 50},
        review_id="tp_1", l1=l1, l2=l2, review_text=review))


CASES = {
    "cancelation": ("Cancellations", "Cancellation Issues",
                    "Supplier cancelled last minute",
                    "the supplier cancelled my booking at the last minute"),
    "delay_fulfilment": ("Operations Issue", "Delay in Fulfilment",
                         "Tickets delivered after the experience date",
                         "the tickets were delivered after the experience date"),
}


@pytest.mark.parametrize("tab", sorted(CASES))
def test_an_export_row_is_reachable_in_the_tab_it_landed_in(monkeypatch, tab):
    """The export writes every scenario under `scenarios`. The scorer reads
    the column THAT TAB uses — `cancelation_reason`, `delay_fulfilment_reason`.

    So the export's cancellation rows superseded their live counterparts and
    then scored 0 against every review: the guidance was removed and nothing
    put back. The panel said "No DSS available", which is also what a tab with
    no coverage says — a broken merge and an out-of-scope L2 reading the same.
    63 cancellation rows and 24 delay rows were in that state.
    """
    l1, l2, scen, review = CASES[tab]
    import server.services.dss as dss
    got = _drive_tab(
        monkeypatch, tab,
        live_rows=[{dss.TABS[tab]: scen, "dss": "OLD guidance"}],
        unified_rows={tab: [{"_unified": True, "scenarios": scen,
                             dss.TABS[tab]: scen, "dss": "NEW guidance"}]},
        l1=l1, l2=l2, review=review)
    assert got.get("action") == "NEW guidance", got
    assert got.get("match_score"), "the export row matched nothing"


@pytest.mark.parametrize("tab", ["cancelation", "delay_fulfilment",
                                 "meetingPointIssue", "supplyPartnerIssue"])
def test_the_loaded_rows_carry_the_column_their_tab_scores_on(tab):
    """The same guarantee against the real export rather than a fixture. A
    loader that stores the selector under one name only leaves whole tabs
    unmatchable while every count and every "rows imported" line looks right.
    """
    from server.services.dss import TABS
    rows = _UNIFIED.get(tab) or []
    assert rows, f"{tab} has no rows from the export"
    missing = [r for r in rows if not r.get(TABS[tab])]
    assert not missing, (
        f"{len(missing)} of {len(rows)} {tab} rows have no {TABS[tab]} — "
        f"they supersede the live row and then score 0 against every review")


def test_the_export_row_wins_a_tie_with_a_live_row(monkeypatch):
    """Two rows for different-but-equivalent scenarios score the same, and the
    scorer keeps the FIRST at that score. Which one that is, is the whole
    instruction: put the live sheet in front and "follow the new one" becomes
    "follow whichever the sheet ordered", silently.

    Neither row supersedes the other here — the wording differs — so ordering
    is the only thing deciding it.
    """
    got = _drive_tab(
        monkeypatch, "meetingPointIssue",
        live_rows=[{"scenarios": "Guide absent from meeting point",
                    "dss": "LIVE guidance"}],
        unified_rows={"meetingPointIssue": [
            {"_unified": True, "scenarios": "Meeting point guide absent",
             "dss": "UNIFIED guidance"}]},
        l1="Operations Issue", l2="Meeting Point Issues",
        review="the guide was absent from the meeting point")
    assert got.get("action") == "UNIFIED guidance", got


def test_a_blank_scenario_in_the_export_supersedes_nothing(monkeypatch, caplog):
    """An export row with an empty scenario cell has an empty selector key,
    and so does every live row whose selector column is blank or absent. Left
    in the set, one stray spacer row drops all of them.

    They are unmatchable rows either way, so what this catches is the count:
    the trail would report a three-row supersede where one row was superseded.
    A number in the trail that is not the number is worse than no number.

    The real row in the same run is what makes this test able to fail — a
    merge that superseded nothing at all would otherwise pass it.
    """
    import logging
    caplog.set_level(logging.INFO, logger="server.services.dss")
    _drive_tab(
        monkeypatch, "meetingPointIssue",
        live_rows=[{"scenarios": SCEN, "dss": "OLD guidance"},
                   {"scenarios": "", "dss": "blank selector"},
                   {"dss": "no selector column at all"}],
        unified_rows={"meetingPointIssue": [
            {"_unified": True, "scenarios": "", "dss": "spacer row"},
            {"_unified": True, "scenarios": SCEN, "dss": "NEW guidance"}]},
        l1="Operations Issue", l2="Meeting Point Issues",
        review="the guide was not present at the meeting point")
    said = [r.message for r in caplog.records if "supersedes" in r.message]
    assert said, "the merge superseded nothing — this test proves nothing"
    assert "supersedes 1 live-sheet row(s)" in said[0], (
        f"{said[0]} — the blank-selector live rows were counted as superseded")
