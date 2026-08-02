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


# ── the checked-in macros ───────────────────────────────────────────────────
#
# The live sheet failed four ways — an unshared service account, an ambiguous
# id between config.py and .env.example, a CSV export that reaches one of nine
# tabs, and a network that could not resolve docs.google.com — and every one
# arrived as the same empty list. The approved macros are in the repo now and
# the sheet is an optional refresh. These are the tests that stop that
# quietly reverting.

def test_the_macros_are_actually_in_the_tree():
    assert C.VENDORED.exists(), \
        f"{C.VENDORED} is missing — every reply loses its tone reference"


def test_the_checked_in_macros_yield_usable_replies():
    rows = C._rows_from_vendored()
    assert len(rows) > 150, f"only {len(rows)} replies survived parsing"


def test_the_trustpilot_macros_are_among_them():
    """This dashboard drafts Trustpilot replies. Macros for every other
    channel and none for this one is the failure that started all of it."""
    tabs = {r["tab"] for r in C._rows_from_vendored()}
    assert any("TP" in t for t in tabs), tabs


def test_every_reply_channel_survived_the_import():
    tabs = {r["tab"] for r in C._rows_from_vendored()}
    for expected in (TP, TWITTER, "Macros for SM", "ORM Email Macro"):
        assert expected in tabs, f"{expected} did not survive: {sorted(tabs)}"


def test_the_tag_mapping_tab_did_not_survive_the_import():
    assert MAPPING not in {r["tab"] for r in C._rows_from_vendored()}


def test_line_breaks_survived_the_html_import():
    """A macro is a laid-out reply. Losing <br> runs the greeting into the body
    and every tone example becomes one paragraph — which teaches the model the
    wrong shape while looking perfectly fine in a row count."""
    rows = [r for r in C._rows_from_vendored() if r["tab"] == TP]
    assert any("\n" in r["response"] for r in rows), \
        "not one Trustpilot macro has a line break"


def test_the_replies_are_replies_not_labels():
    rows = C._rows_from_vendored()
    long = [r for r in rows if len(r["response"]) > 120]
    assert len(long) > len(rows) * 0.8, \
        f"only {len(long)} of {len(rows)} look like actual replies"


# ── the sheet is now optional, and that must not read as a failure ──────────

def _fresh(monkeypatch):
    monkeypatch.setattr(C, "_cache_rows", [], raising=False)
    monkeypatch.setattr(C, "_cache_at", 0, raising=False)
    monkeypatch.setattr(C, "_last_reason", "", raising=False)


def test_with_no_sheet_configured_the_macros_still_load(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(C, "is_live", lambda s: False)
    rows = asyncio.run(C._get_rows())
    assert rows, "no sheet configured left the pipeline with no tone reference"


def test_an_unreachable_sheet_is_not_reported_as_a_failure(monkeypatch):
    """The tone reference is present and approved; only the refresh did not
    happen. Marking that a failure makes a healthy run look broken — the
    inverse bug, and it trains people to ignore the mark."""
    _fresh(monkeypatch)
    monkeypatch.setattr(C, "is_live", lambda s: True)

    async def _boom():
        raise RuntimeError("docs.google.com unreachable")
    monkeypatch.setattr(C, "_fetch_rows", _boom)
    rows = asyncio.run(C._get_rows())
    assert rows, "an unreachable sheet emptied the tone reference"
    assert C.last_failure_reason() == "", \
        f"a working run reported a failure: {C.last_failure_reason()!r}"


def test_a_readable_sheet_wins_over_the_checked_in_copy(monkeypatch):
    """Someone editing the sheet has to see their edit."""
    _fresh(monkeypatch)
    monkeypatch.setattr(C, "is_live", lambda s: True)

    async def _live():
        return [{"situation": "edited", "response": "x" * 200, "tab": "live"}]
    monkeypatch.setattr(C, "_fetch_rows", _live)
    rows = asyncio.run(C._get_rows())
    assert [r["situation"] for r in rows] == ["edited"]


def test_losing_both_sources_says_so(monkeypatch):
    """The only genuine failure left, and it must not be silent."""
    _fresh(monkeypatch)
    monkeypatch.setattr(C, "is_live", lambda s: False)
    monkeypatch.setattr(C, "_rows_from_vendored", lambda: [])
    assert asyncio.run(C._get_rows()) == []
    why = C.last_failure_reason()
    assert why and "checked-in macros are missing" in why, why


def test_the_status_line_names_the_tabs(monkeypatch):
    """doctor.py prints this. A bare count cannot answer "is Trustpilot in
    there", which is the only question that matters."""
    s = C.vendored_status()
    assert "replies across" in s and TP in s, s


def test_the_importer_keeps_the_line_breaks_a_macro_is_laid_out_with():
    """<br> is a real newline in a macro. Stripping it runs the greeting into
    the body, so every tone example becomes one wall of text — which teaches
    the model the wrong shape while a row count still looks perfect."""
    import sys as _sys
    _sys.path.insert(0, "tools")
    import import_macros
    got = import_macros._cells(
        "<td>Unable to trace</td>"
        "<td>Hey &lt;first name&gt;,<br><br>I'm sorry.<br>Best,</td>")
    assert got[0] == "Unable to trace"
    assert got[1].count("\n") >= 2, repr(got[1])
    assert got[1].startswith("Hey <first name>,")


def test_the_importer_strips_the_markup_but_not_the_text():
    import sys as _sys
    _sys.path.insert(0, "tools")
    import import_macros
    got = import_macros._cells('<td><span style="x">Refund</span> &amp; more</td>')
    assert got == ["Refund & more"]


def test_an_import_that_yields_nothing_is_not_reported_as_success(tmp_path):
    """The importer exits 0 and prints "wrote ..." either way. An export that
    parsed to zero usable replies would then look like a refresh that worked,
    and the tone reference would be empty on the next deploy with a green
    commit behind it."""
    import subprocess
    import sys as _sys
    d = tmp_path / "export"
    d.mkdir()
    (d / "Nothing.html").write_text(
        "<table><tr><td></td><td>A</td></tr>"
        "<tr><td>1</td><td>Some Heading</td></tr>"
        "<tr><td>2</td><td>a value</td></tr></table>", encoding="utf-8")
    out = tmp_path / "out.json"
    r = subprocess.run([_sys.executable, "tools/import_macros.py", str(d)],
                       capture_output=True, text=True,
                       env={**__import__("os").environ,
                            "CANNED_MACROS_OUT": str(out)})
    assert r.returncode != 0, \
        f"an empty import exited 0:\n{r.stdout}\n{r.stderr}"
    assert "nothing usable" in (r.stdout + r.stderr)


# ── the tags are a routing vocabulary, not junk ─────────────────────────────

def test_the_tag_tab_is_read_as_a_taxonomy():
    """It is rejected as REPLIES and used as the issue-type vocabulary. Those
    are two different jobs for one tab and both have to happen."""
    v = C.channel_issue_types()
    assert "TP MACRO Issue Type" in v, sorted(v)
    assert len(v["TP MACRO Issue Type"]) > 60


def test_every_channel_has_its_own_vocabulary():
    v = C.channel_issue_types()
    assert len(v) == 3, sorted(v)
    assert all(len(x) > 40 for x in v.values())


def test_the_tag_vocabulary_matches_the_macros_it_names():
    """72 of 75 TP tags are a macro's Use Case verbatim. If that collapses, the
    tags and the macros have drifted apart and routing on one to reach the
    other stops working — silently, since both still parse."""
    tags = set(C.channel_issue_types()["TP MACRO Issue Type"])
    macros = {r["situation"] for r in C._rows_from_vendored() if "TP" in r["tab"]}
    overlap = len(tags & macros)
    assert overlap > len(tags) * 0.85, \
        f"only {overlap} of {len(tags)} tags name a real macro"


# ── matching, and the blank that is a decision ──────────────────────────────

def _match(monkeypatch, l1, l2, text):
    monkeypatch.setattr(C, "is_live", lambda s: False)
    monkeypatch.setattr(C, "_cache_rows", [], raising=False)
    monkeypatch.setattr(C, "_cache_at", 0, raising=False)
    return asyncio.run(C.get_canned_responses(l1, l2, None, text))


def test_a_real_issue_finds_its_macro(monkeypatch):
    got = _match(monkeypatch, "Experience Issues", "Meeting Point Issues",
                 "the meeting point was wrong and nobody was there")
    assert got, "a meeting-point review matched no macro at all"
    assert "meeting point" in got[0]["situation"].lower(), got[0]["situation"]


def test_the_match_prefers_the_trustpilot_voice(monkeypatch):
    """The macros are split by channel and the voices genuinely differ — a
    Twitter reply is 280 characters, a Trustpilot one is a paragraph. A macro
    from the wrong tab is the wrong answer even when its situation fits."""
    got = _match(monkeypatch, "Experience Issues", "Meeting Point Issues",
                 "the meeting point was wrong")
    assert C.TP_TAB_HINT in got[0]["tab"], got[0]["tab"]


def test_word_soup_matches_nothing(monkeypatch):
    """The old bar was score > 0, which one shared word clears — so a meeting
    point review could 'match' a refund macro on the word 'booking' and go out
    looking approved."""
    assert _match(monkeypatch, "", "", "zxqw plimf glorp") == []


def test_one_incidental_word_is_not_a_match(monkeypatch):
    """Every macro contains 'booking'. On its own it must not carry one."""
    assert _match(monkeypatch, "", "", "booking") == []


def test_a_match_carries_its_score_and_tab(monkeypatch):
    """tone_entry names the closest macro on the card. It cannot if the match
    does not say which one it was."""
    got = _match(monkeypatch, "Experience Issues", "Meeting Point Issues",
                 "meeting point")
    assert got[0]["score"] >= C.MATCH_MIN
    assert got[0]["tab"]


def test_matching_survives_a_classification_that_is_only_words(monkeypatch):
    """"Meeting Point Issues" and "Meeting point issue//" share every
    meaningful word and no substring. An exact-substring test scored that
    perfectly good macro at zero."""
    got = _match(monkeypatch, "Experience Issues", "Meeting Point Issues", "")
    assert got, "token matching is not reaching the macro"


# ── the three ranking rules, each isolated so it can actually fail ──────────
#
# The first pass of these tests asserted things that were true whether or not
# the rule fired: the Trustpilot macro also had the top score, "Meeting Point
# Issues" still matched on two unstemmed words, and no fixture had a macro
# whose BODY shared the review's words while its situation did not. Three
# survivors, all the same mistake — an assertion that cannot distinguish the
# rule working from the rule being absent.

def _macros(*specs):
    """(tab, situation, response) triples as _get_rows would hand them over.

    Named _macros, not _rows: this file already has a module-level _rows(tab)
    that reads the fixture, and shadowing it silently redefined what five
    earlier tests were calling."""
    return [{"situation": sit, "response": resp, "tab": tab,
             "l1_hint": "", "l2_hint": ""} for tab, sit, resp in specs]


def _rank(monkeypatch, rows, l1, l2, text):
    monkeypatch.setattr(C, "is_live", lambda s: False)
    monkeypatch.setattr(C, "_cache_rows", rows, raising=False)
    monkeypatch.setattr(C, "_cache_at", 9e18, raising=False)   # never expire
    return asyncio.run(C.get_canned_responses(l1, l2, None, text))


def test_the_trustpilot_voice_wins_even_when_another_channel_scores_higher(monkeypatch):
    """The point of the channel rule. A Twitter macro that fits BETTER is
    still the wrong answer — 280 characters where a paragraph belongs — so the
    test has to hand it a better-fitting Twitter macro and watch it lose."""
    got = _rank(monkeypatch, _macros(
        ("Twitter Macro", "Meeting point issue venue related service problem",
         "short " * 40),
        ("ORM main ( TP ) Macro", "Meeting point issue", "long " * 40),
    ), "Experience Issues", "Meeting Point Issues",
        "meeting point venue related service problem")
    assert "TP" in got[0]["tab"], \
        f"a better-scoring Twitter macro outranked the Trustpilot one: {got[0]}"
    assert got[1]["score"] > got[0]["score"], \
        "the fixture no longer scores the wrong-channel macro higher, so this " \
        "test cannot fail — fix the fixture, not the assertion"


def test_stemming_is_what_makes_the_plural_match(monkeypatch):
    """"Refund Issues" against a macro filed as "Refund Issue". Nothing else
    overlaps, so the stem is the only thing that can carry it — which is what
    makes this able to fail when the stem is removed."""
    got = _rank(monkeypatch, _macros(
        ("ORM main ( TP ) Macro", "Refund Issue", "x" * 200),
    ), "", "Refund Issues", "")
    assert got, "the plural classification no longer reaches the singular macro"


def test_the_review_is_scored_against_the_situation_not_the_reply_body(monkeypatch):
    """Every macro's BODY shares the same boilerplate — sorry, booking, team,
    resolve. Scoring the review against it makes everything equally relevant,
    so a macro whose situation is unrelated can win on words that appear in
    every reply."""
    got = _rank(monkeypatch, _macros(
        ("ORM main ( TP ) Macro", "Completely unrelated situation",
         "sorry booking team resolve refund voucher meeting point " * 12),
        ("ORM main ( TP ) Macro", "Meeting point issue", "x" * 200),
    ), "Experience Issues", "Meeting Point Issues",
        "sorry booking team resolve refund voucher meeting point")
    assert got, "nothing matched at all"
    assert "Meeting point" in got[0]["situation"], \
        f"a macro won on boilerplate its body happens to contain: {got[0]}"
