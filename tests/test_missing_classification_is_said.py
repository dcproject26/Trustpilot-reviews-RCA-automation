"""An unclassified review says so, in words, in both places it shows.

The panel read:

    no support-tag mapping for ? / ? - support contacts not compared
    · no L2 variants for ? - reviews not compared

Two sentences, four question marks, and no way to tell which of two very
different things had happened:

  * this L1/L2 pair is deliberately unmapped on the support side (11 of 32
    are) - a gap in the tag framework, somebody else's job, and the reviews
    half of the comparison is still trustworthy; or
  * this review has no classification at all, so there was nothing to look up
    - this review's own analysis is missing, and it is fixed by re-running or
    by setting the Classification selects by hand.

'?' pointed at neither. And upstream of the sentence, an empty L1/L2 was
completely silent: the classifier could throw and the only trace was a log
line, while the dashboard showed empty selects that read as "nobody has
classified this yet".
"""
import asyncio

import pytest

from server.services.insights import _no_mapping_note


TAIL = "support contacts not compared"


# ── the sentence ────────────────────────────────────────────────────────────

def test_a_real_pair_is_named():
    """The unmapped-category case is unchanged: name the pair, because filling
    the mapping in needs to know which one."""
    assert _no_mapping_note("Experience Issues", "Meeting Point Issues", TAIL) == \
        f"no support-tag mapping for Experience Issues / Meeting Point Issues - {TAIL}"


def test_no_classification_does_not_render_a_placeholder():
    note = _no_mapping_note("", "", TAIL)
    assert "?" not in note, note
    assert "no L1 or L2 classification" in note


def test_no_classification_does_not_blame_the_tag_mapping():
    """"no support-tag mapping" sends the reader to the taxonomy. The taxonomy
    is fine; the review has no category to look up in it."""
    assert "support-tag mapping" not in _no_mapping_note(None, None, TAIL)


@pytest.mark.parametrize("l1,l2,missing", [
    ("Experience Issues", "", "L2"),
    ("", "Meeting Point Issues", "L1"),
])
def test_half_a_classification_names_the_half_that_is_missing(l1, l2, missing):
    note = _no_mapping_note(l1, l2, TAIL)
    assert "?" not in note, note
    assert f"no {missing} classification" in note


def test_the_tail_always_survives():
    """Whatever the reason, the reader still has to be told which number is
    absent because of it."""
    for l1, l2 in [("A", "B"), ("", ""), ("A", ""), ("", "B")]:
        assert _no_mapping_note(l1, l2, TAIL).endswith(TAIL)


# ── the sentence, as get_insights actually composes it ──────────────────────
#
# The function above can be perfect and never be called. This drives the real
# entry point with the real gates.

@pytest.fixture()
def stub_bq(monkeypatch):
    import server.services.insights as I
    monkeypatch.setattr(I, "MOCK_MODE", False, raising=False)
    monkeypatch.setattr(I, "is_live", lambda svc: True)

    async def _run(sql, params):
        return []
    monkeypatch.setattr(I, "_run", _run)
    return I


def _why(I, l1, l2):
    out = asyncio.run(I.get_insights(
        {"tid": "43605", "vid": "4040", "tgid": "22238",
         "visitDate": "2026-07-22"}, l1, l2, window="30d"))
    return out.get("_partial_because") or ""


def test_an_unclassified_review_reaches_the_panel_without_question_marks(stub_bq):
    why = _why(stub_bq, "", "")
    assert why, "both comparisons were skipped and the panel was told nothing"
    assert "?" not in why, why
    assert "no L1 or L2 classification" in why
    assert "no L2 classification" in why, \
        "the reviews half still has to say why it did not run"


def test_the_two_halves_still_report_separately(stub_bq):
    """They are gated separately. One sentence for both would put the reviews
    count back under the support half's explanation, which is the bug the split
    gates were added to fix."""
    why = _why(stub_bq, "", "")
    assert " · " in why, why
    assert "support contacts not compared" in why
    assert "reviews not compared" in why


def test_a_classified_pair_with_no_mapping_still_names_it(stub_bq):
    """The other side of the branch, through the same entry point - so a change
    that fixes the '?' by dropping the names is caught here."""
    from server.taxonomy import SUPPORT_TAG_MAP
    from server.services.insights import _L2_BUCKETS, l2_variants, support_tags_for
    l1s = sorted({k[0] for k in SUPPORT_TAG_MAP if k[0] != "Supply Partner Issue"})
    pair = next(((a, b) for a in l1s for b in sorted(_L2_BUCKETS)
                 if support_tags_for(a, b) is None and l2_variants(b)), None)
    if not pair:
        pytest.skip("every L1/L2 pair is mapped - nothing unmapped to name")
    why = _why(stub_bq, *pair)
    assert f"no support-tag mapping for {pair[0]} / {pair[1]}" in why, why
    assert "reviews not compared" not in why, \
        "an unmapped support tag silenced the reviews half again"


# ── upstream: why the classification is empty in the first place ────────────

from server.pipeline import classification_entry                # noqa: E402


class _Boom(Exception):
    pass


def test_a_complete_classification_says_nothing():
    assert classification_entry("Experience Issues", "Meeting Point Issues", None) is None


def test_an_empty_classification_is_marked_warn_not_pass():
    e = classification_entry("", "", None)
    assert e is not None, "an empty classification was silent again"
    assert e["mark"] == "warn", \
        "a missing classification is not a step that succeeded"


def test_an_empty_classification_says_it_is_not_an_absent_category():
    """The whole point: blank selects look like nobody has classified this
    yet. The line has to say the classifier ran."""
    text = classification_entry("", "", None)["text"]
    assert "returned no L1 or L2" in text
    assert "not because this review has no category" in text


def test_a_thrown_classifier_reads_differently_from_an_empty_one():
    thrown = classification_entry("", "", _Boom("upstream 503 from the model"))["text"]
    empty = classification_entry("", "", None)["text"]
    assert thrown != empty, \
        "a classifier that crashed and one that shrugged read the same"
    assert "Classification failed" in thrown
    assert "503" in thrown, "the reason is only in the log"


def test_the_error_line_is_a_sentence_not_a_stack_trace():
    """_human_error exists because the trail once rendered 500 characters of
    SQL into the dashboard."""
    text = classification_entry("", "", _Boom("x" * 400))["text"]
    assert len(text) < 700, text
    assert "— _Boom: " in text and ". L1 and L2" in text, text


@pytest.mark.parametrize("l1,l2,absent", [
    ("Experience Issues", "", "L2"),
    ("", "Meeting Point Issues", "L1"),
])
def test_half_a_classification_is_still_a_warning(l1, l2, absent):
    e = classification_entry(l1, l2, None)
    assert e is not None and e["mark"] == "warn"
    assert f"returned no {absent}</strong>" in e["text"]


def test_it_names_what_was_skipped_with_it():
    """"L1 is empty" is a fact about a field. The reader needs to know which
    numbers on the same screen are absent because of it."""
    text = classification_entry("", "", None)["text"]
    for skipped in ("support-tag comparison", "review-variant comparison",
                    "scenario lookup"):
        assert skipped in text, f"{skipped} is not named as skipped"


# ── the same shape, for the two fields beside it ────────────────────────────
#
# One card carried all three at once: "? / ?" in the panel, "Nothing was
# extracted" under a full RCA, and "No Zendesk events were found" — three
# sentences asserting three facts about the review, none of which had been
# established.

from server.pipeline import stated_issue_entry, timeline_entry     # noqa: E402


def test_a_stated_issue_that_arrived_says_nothing():
    assert stated_issue_entry("Guest's tickets were cancelled by the vendor.", None) is None


@pytest.mark.parametrize("value", ["", "   ", "\n"])
def test_an_empty_stated_issue_is_disclosed(value):
    e = stated_issue_entry(value, None)
    assert e is not None and e["mark"] == "warn"
    assert "returned nothing" in e["text"]


def test_a_stated_issue_that_threw_reads_differently_from_one_that_was_blank():
    thrown = stated_issue_entry("", _Boom("read timed out"))
    blank = stated_issue_entry("", None)
    assert thrown["text"] != blank["text"]
    assert "could not be extracted" in thrown["text"]
    # _human_error maps "timed out" to a sentence, so the reader gets the
    # action rather than the exception class.
    assert "timed out" in thrown["text"]


def test_the_stated_issue_line_denies_the_claim_the_panel_makes():
    """The panel says "Nothing was extracted", which is a statement about the
    review. The trail has to contradict it, or the two agree on something
    false."""
    assert "not the same as a review with nothing to state" \
        in stated_issue_entry("", None)["text"]


def test_a_timeline_with_events_says_nothing():
    assert timeline_entry("32908218", [{"label": "Ticket created"}], ["4491"], None) is None


def test_an_honest_empty_timeline_is_a_pass_not_a_warning():
    """The inverse bug. A booking Zendesk genuinely has no tickets for is a
    working lookup, and marking it warn makes a healthy run look faulty."""
    e = timeline_entry("32908218", [], [], None)
    assert e["mark"] == "pass", e
    assert "no tickets are linked" in e["text"]
    assert "32908218" in e["text"], "the reader cannot check a booking it does not name"


def test_a_lookup_that_never_ran_is_not_an_empty_result():
    e = timeline_entry("", [], [], None)
    assert e["mark"] == "warn"
    assert "not searched" in e["text"]


def test_a_thrown_lookup_is_not_an_empty_result():
    e = timeline_entry("32908218", [], [], _Boom("connection refused"))
    assert e["mark"] == "warn"
    assert "lookup failed" in e["text"]
    assert "because this booking has no tickets" in e["text"]


def test_tickets_that_yielded_no_events_are_named():
    """The one an associate can act on immediately: the tickets exist, so open
    them. "No events were found" sent them nowhere."""
    e = timeline_entry("32908218", [], ["4491", "4502"], None)
    assert e["mark"] == "warn"
    assert "ZD-4491" in e["text"] and "ZD-4502" in e["text"]
    assert "2 ticket(s)" in e["text"]


def test_the_four_timeline_outcomes_all_read_differently():
    texts = {
        timeline_entry("", [], [], None)["text"],
        timeline_entry("1", [], [], _Boom("x"))["text"],
        timeline_entry("1", [], ["9"], None)["text"],
        timeline_entry("1", [], [], None)["text"],
    }
    assert len(texts) == 4, "two of the four empty timelines read the same"


def test_the_pipeline_actually_calls_it():
    """A disclosure wired into nothing looks exactly like one that works. This
    is a negative assertion — the inlined copy must not come back — plus a
    positive one that the name is referenced at the call site, which is as far
    as a source read can honestly go. The behaviour above is what is tested."""
    src = open("server/pipeline.py", encoding="utf-8").read()
    body = src[src.find("async def process_review("):]
    for call in ("classification_entry(\n            l1, l2, _classify_err, _classify_warnings)",
                 "stated_issue_entry(stated_issue, _si_err)",
                 "timeline_entry(bid_for_zd, timeline,"):
        assert call in body, f"{call} is not called from the pipeline"
    for sentence in ("the Classification selects are empty for that reason",
                     "not the same as a review with nothing to state",
                     "no tickets are linked to booking"):
        assert sentence not in body, \
            f"{sentence!r} was inlined again; two copies will drift"


# ── the reply's voice ───────────────────────────────────────────────────────

from server.pipeline import tone_entry                            # noqa: E402


def test_a_reply_written_against_approved_replies_says_so():
    e = tone_entry([{"situation": "s", "response": "r"}], "Experience Issues",
                   "Meeting Point Issues", None)
    assert e["mark"] == "pass"
    assert "1 approved macro" in e["text"]
    assert "Experience Issues / Meeting Point Issues" in e["text"]


def test_no_matching_macro_leaves_the_reply_blank_on_purpose():
    """Rule 20. An invented reply reads exactly like an approved one on the
    card — same register, same length, same shape — and Send puts it on a
    public review. Blank plus a reason is the smaller cost, but only if the
    reason is there; an unexplained blank box is the bug this whole branch is
    about."""
    e = tone_entry([], "Experience Issues", "Meeting Point Issues", None)
    assert e["mark"] == "warn"
    assert "No approved macro covers" in e["text"]
    assert "left blank on purpose rather than invented" in e["text"]
    assert "Write it yourself" in e["text"]


def test_no_classification_is_named_as_the_reason_the_lookup_matched_nothing():
    """The sheet is keyed on L1/L2. With neither, "no approved reply matches
    / " blames the sheet for a lookup that was never given a key."""
    e = tone_entry([], "", "", None)
    assert "this review has neither" in e["text"]
    assert "no approved reply matches" not in e["text"]


def test_a_thrown_lookup_is_not_an_empty_match():
    e = tone_entry([], "Experience Issues", "Meeting Point Issues",
                   _Boom("403 from the sheet"))
    assert "lookup failed" in e["text"]
    assert "403" in e["text"]


def test_an_unreadable_sheet_does_not_blame_the_taxonomy():
    """The lookup returns [] whether the sheet is unshared or the category has
    no reply written for it. "no approved reply matches Experience Issues /
    Meeting Point Issues" sends someone to write one, when what is needed is
    to share a document."""
    e = tone_entry([], "Experience Issues", "Meeting Point Issues", None,
                   "the canned-responses sheet could not be read (HTTPError) - "
                   "share it link-viewable, or with the service account")
    assert "no approved reply matches" not in e["text"], e["text"]
    assert "share it link-viewable" in e["text"]


def test_an_unconfigured_sheet_reads_differently_from_an_unshared_one():
    a = tone_entry([], "A", "B", None, "no canned-responses sheet is configured "
                   "on this server (CANNED_RESPONSES_SHEET_ID is unset)")["text"]
    b = tone_entry([], "A", "B", None, "the canned-responses sheet could not be "
                   "read (HTTPError)")["text"]
    c = tone_entry([], "A", "B", None, "")["text"]
    assert len({a, b, c}) == 3, "an unset env var, an unshared sheet and a real "\
        "no-match are one sentence"


def test_the_sheet_reason_outranks_the_missing_classification():
    """Both are true when a review is unclassified AND the sheet is down. The
    sheet is the one that makes every other review wrong too."""
    e = tone_entry([], "", "", None, "the canned-responses sheet could not be read")
    assert "could not be read" in e["text"]
    assert "this review has neither" not in e["text"]


def test_the_three_ways_to_have_no_tone_read_differently():
    texts = {tone_entry([], "", "", None)["text"],
             tone_entry([], "A", "B", None)["text"],
             tone_entry([], "A", "B", _Boom("x"))["text"]}
    assert len(texts) == 3, "two of the three read the same"


def test_the_pipeline_calls_it():
    src = open("server/pipeline.py", encoding="utf-8").read()
    body = src[src.find("async def process_review("):]
    assert "tone_entry(canned_list or [], l1, l2, _tone_err,\n                                     last_failure_reason(), last_source())" in body
    assert "model's own voice" not in body, "the sentence was inlined"


def test_examples_without_a_classification_are_not_reported_as_a_match():
    """The sheet still returns its top three on word overlap when L1/L2 are
    missing — so "matched" is true and "matched well" is not. A pass line here
    would rest the second claim on the evidence for the first."""
    e = tone_entry([{"situation": "s", "response": "r"}] * 3, "", "", None)
    assert e["mark"] == "warn", e
    assert "word overlap alone" in e["text"]
    assert "neither" in e["text"]


def test_a_half_classification_names_which_half_the_ranking_lost():
    e = tone_entry([{"situation": "s", "response": "r"}], "Experience Issues", "", None)
    assert e["mark"] == "warn"
    assert "no L2" in e["text"]


# ── which lookup produced no guest name ─────────────────────────────────────
#
# "[Guest name in Zendesk ticket]" was a sentence sitting in the value column.
# It looked like data, and it made three situations identical: the warehouse
# holds a hash for this booking, a ticket is linked but carries no requester,
# and no ticket was ever matched. The first two are worth opening Zendesk for;
# the third is not, and the third is the one where the match itself is
# suspect. One string for all three sent the reader to the wrong place twice
# out of three times.

def _dict(**kw):
    """A real RcaDraft, unsaved. A stub with only the fields I remembered would
    pass whatever _draft_dict happens to read today."""
    import server.db as db
    import server.api as api
    return api._draft_dict(db.RcaDraft(id="g1", review_id="tp_g", **kw))


def _note(**kw):
    return _dict(**kw)["guest_name_note"]


def _name(**kw):
    return _dict(**kw)["guest_name"]


HASH = "a3f9c1e07b2d4856"          # 16 hex chars, no spaces


def test_a_resolved_name_carries_no_note():
    assert _name(booking={"guestName": "Lewis MacAndrew"}) == "Lewis MacAndrew"
    assert _note(booking={"guestName": "Lewis MacAndrew"}) == ""


def test_a_hashed_name_is_named_as_a_hash():
    """The value IS there — it is just not a name. "no ticket was matched"
    would be false, and it would send someone to re-run the match instead of
    opening the ticket that is already linked."""
    assert _name(booking={"guestName": HASH}) == ""
    assert "hash" in _note(booking={"guestName": HASH})


def test_a_hash_in_ticket_facts_counts_too():
    assert "hash" in _note(ticket_facts={"guest_full_name": HASH})


def test_a_hash_beats_the_ticket_count():
    """A booking can hold a hash AND have linked tickets. The hash is the
    more specific finding and the one with an action attached."""
    assert "hash" in _note(booking={"guestName": HASH},
                           zendesk_ticket_ids=["4491"])


def test_a_linked_ticket_with_no_requester_says_so():
    n = _note(zendesk_ticket_ids=["4491"])
    assert "no requester name on the linked Zendesk ticket" == n
    assert "hash" not in n


def test_no_ticket_at_all_is_a_different_sentence():
    n = _note()
    assert "no Zendesk ticket was matched" in n
    assert "linked Zendesk ticket" not in n


def test_the_three_absences_never_read_the_same():
    assert len({_note(booking={"guestName": HASH}),
                _note(zendesk_ticket_ids=["4491"]),
                _note()}) == 3


def test_the_note_is_never_the_old_placeholder():
    """It went out as a value once. It must never be one again."""
    for kw in ({"booking": {"guestName": HASH}}, {"zendesk_ticket_ids": ["1"]}, {}):
        assert "[Guest name" not in _note(**kw)


# ── the classifier's own account of what went wrong ─────────────────────────
#
# classifier.py records a precise reason for every failure and every repair:
# "Invalid L1 'Booking Issues' - dropped to empty", "Response was not valid
# JSON", "Recovered L1 to 'Experience Issues' based on L2 match". All of it
# went to log.warning and stopped there, and the trail said "the classifier
# returned no L1 or L2" — the one part of the answer with nothing actionable
# in it. A taxonomy that has drifted and a model returning prose are fixed by
# different people.

def test_the_reason_the_classifier_gave_reaches_the_reader():
    text = classification_entry("", "", None,
                                ["Invalid L1 'Booking Issues' — dropped to empty"])["text"]
    assert "Invalid L1 'Booking Issues'" in text, text


def test_two_different_reasons_do_not_read_the_same():
    a = classification_entry("", "", None, ["Response was not valid JSON"])["text"]
    b = classification_entry("", "", None, ["Invalid L1 'Nonsense' — dropped to empty"])["text"]
    assert a != b


def test_no_reason_at_all_is_itself_reported():
    """An empty warnings list means the classifier returned a clean, parseable
    answer with no L1 in it — which is a different and stranger failure than
    any of the ones it knows how to describe. Silence about it would put the
    two back together."""
    text = classification_entry("", "", None, [])["text"]
    assert "gave no reason" in text


def test_the_reasons_are_capped_so_the_trail_stays_readable():
    text = classification_entry("", "", None, [f"reason {i}" for i in range(20)])["text"]
    assert "reason 3" in text and "reason 9" not in text, text


@pytest.mark.parametrize("junk", [None, [], ["", "  "]])
def test_empty_warnings_never_render_as_a_dangling_list(junk):
    text = classification_entry("", "", None, junk)["text"]
    assert "It reported: ." not in text
    assert "It reported:  " not in text


@pytest.mark.parametrize("junk", [None, [], ["", "  ", "\n"]])
def test_a_clean_classification_is_silent_whatever_shape_the_warnings_arrive_in(junk):
    """A warnings list of blank strings is truthy. Without the filter it makes
    a perfectly good classification report itself as repaired — and once every
    classification carries a warn mark, the mark stops distinguishing anything
    and the genuinely repaired ones are lost among them. That is the inverse
    of the bug this whole file is about, and it fails just as quietly."""
    assert classification_entry("Experience Issues", "Meeting Point Issues",
                                None, junk) is None, junk


def test_a_repaired_classification_is_not_silent():
    """The validator recovers an invalid L1 from a valid L2. The selects then
    look exactly like a clean run — same two values, no mark — while the
    model's actual answer was a category that does not exist. Everything keyed
    on L1/L2 is now keyed on the validator's guess."""
    e = classification_entry("Experience Issues", "Meeting Point Issues", None,
                             ["Invalid L1 'Booking Issues' — dropped to empty",
                              "Recovered L1 to 'Experience Issues' from valid L2"])
    assert e is not None, "a repair rendered identically to a clean classification"
    assert e["mark"] == "warn", "a repair is not a step that succeeded"
    assert "repaired" in e["text"]
    assert "Recovered L1 to 'Experience Issues'" in e["text"]


def test_a_repair_says_what_it_landed_on():
    e = classification_entry("Experience Issues", "Meeting Point Issues", None,
                             ["Invalid L1 'X' — dropped to empty"])
    assert "Experience Issues / Meeting Point Issues" in e["text"]


def test_a_clean_classification_is_still_silent():
    """The inverse bug. If every classification warns, the mark stops meaning
    anything and the repaired ones are lost in it."""
    assert classification_entry("Experience Issues", "Meeting Point Issues",
                                None, []) is None
    assert classification_entry("Experience Issues", "Meeting Point Issues",
                                None, None) is None


def test_a_repair_reads_differently_from_an_empty_classification():
    repaired = classification_entry("A", "B", None, ["w"])["text"]
    empty = classification_entry("", "", None, ["w"])["text"]
    assert repaired != empty
    assert "was skipped with it" not in repaired, \
        "a repaired classification did not skip the comparisons — it fed them"


def test_the_pipeline_passes_the_warnings_through():
    """Collecting them and not forwarding them is the same bug one step later."""
    src = open("server/pipeline.py", encoding="utf-8").read()
    body = src[src.find("async def process_review("):]
    assert "_classify_warnings = list(result.warnings)" in body, \
        "the classifier's reasons are dropped at the call site"
    assert "_classify_err, _classify_warnings)" in body, \
        "they are collected and then not passed on"


# ── the canned sheet reports its own state ──────────────────────────────────

GOOD_TAB = [["Use Case", "Response"],
            ["Unable to trace", "Hey <first name>, " + "x" * 200]]
LABEL_TAB = [["TP MACRO Issue Type", "SM MACRO Issue Type"],
             ["Unable to trace booking", "Unable to trace"]]


def _sheet(monkeypatch, tabs=None, boom=None):
    """Drive the REAL _fetch_rows against a stubbed sheet.

    These three used to stub _fetch_rows and then call it, which exercised the
    stub and nothing else. The reasons they check are set inside _fetch_rows,
    so the stub has to be one layer lower.
    """
    import server.services.canned as C
    monkeypatch.setattr(C, "_cache_rows", [], raising=False)
    monkeypatch.setattr(C, "_cache_at", 0, raising=False)
    monkeypatch.setattr(C, "_last_reason", "a stale reason", raising=False)
    if boom:
        def _t():
            raise boom
    else:
        def _t():
            return list((tabs or {}).items())
    monkeypatch.setattr(C, "_tabs_via_service_account", _t)
    return C


def test_the_sheet_reason_is_empty_when_rows_came_back(monkeypatch):
    """It has to be able to say "I read fine" in words distinguishable from
    never having run — otherwise a stale reason from a previous poll gets
    attached to a healthy read."""
    C = _sheet(monkeypatch, {"ORM main ( TP ) Macro": GOOD_TAB})
    assert asyncio.run(C._fetch_rows())
    assert C.last_failure_reason() == ""


def test_a_readable_sheet_with_no_usable_rows_is_its_own_reason(monkeypatch):
    """The columns did not match, or every tab was a tag map. The sheet IS
    reachable, so "share it link-viewable" would be the wrong advice."""
    C = _sheet(monkeypatch, {"Refer Macro Tags": LABEL_TAB})
    assert asyncio.run(C._fetch_rows()) == []
    r = C.last_failure_reason()
    assert "no tab held usable replies" in r, r
    assert "link-viewable" not in r, r


def test_an_unreachable_sheet_falls_back_and_says_which_copy_is_in_use(monkeypatch):
    """Not a failure any more — the checked-in macros carry the run. But an
    edit someone made in the sheet that silently did not take effect is the
    same class of bug as everything else here, so the source is reported."""
    C = _sheet(monkeypatch, boom=RuntimeError("Sheets API HTTP 403"))
    monkeypatch.setattr(C, "is_live", lambda s: True)
    monkeypatch.setattr(C.httpx, "AsyncClient", _dead_client())
    rows = asyncio.run(C._get_rows())
    assert rows, "an unreachable sheet emptied the tone reference"
    assert C.last_failure_reason() == "", \
        "a run with a full tone reference reported a failure"
    src = C.last_source()
    assert "checked-in macros" in src, src
    assert "could not be read" in src, "it does not say the refresh was missed"


def _dead_client():
    class _C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): raise RuntimeError("no network")
    return _C


def test_an_unconfigured_sheet_is_no_longer_a_failure(monkeypatch):
    """It used to be: no sheet id meant no macros meant no tone reference. The
    approved macros are checked in now, so an unset secret means "do not
    refresh" and the pipeline is fully armed without it."""
    import server.services.canned as C
    monkeypatch.setattr(C, "is_live", lambda s: False)
    monkeypatch.setattr(C, "_cache_rows", [], raising=False)
    monkeypatch.setattr(C, "_cache_at", 0, raising=False)
    monkeypatch.setattr(C, "_last_reason", "", raising=False)
    out = asyncio.run(C.get_canned_responses(
        "Experience Issues", "Meeting Point Issues", None,
        "the meeting point was wrong and nobody was there"))
    assert out, "no sheet configured left the pipeline with no macros"
    assert C.last_failure_reason() == "", C.last_failure_reason()


# ── the prompt rule that produces a dated timeline ──────────────────────────
#
# Every `time` came back null on a real card, so the column rendered a row of
# dashes — which is what a failed timestamp lookup looks like. The cause was
# rule 10: with no system events the model builds the sequence from the guest's
# own account, and a guest narrates an order, not a clock. Rule 10 said nothing
# about `time`, so null was the only thing left to return.
#
# These drive rca_v3_prompt(), which assembles the string the model actually
# receives. Asserting against RCA_V4_TEMPLATE instead would pass just as
# happily against a build where the substitution is broken.

def _prompt(booking=None, **kw):
    """Whitespace-normalised: the template hard-wraps, so a phrase that is
    plainly present fails a naive `in` check purely on where the line broke."""
    from server import prompts
    return " ".join(prompts.rca_v3_prompt(
        review_text="the tickets never arrived", booking=booking or {},
        timeline=[], insights={}, dss_rec={}, l1="", l2="", sub_theme="",
        support_summary="", checklist={}, review_id="r1", **kw).split())


def test_the_assembled_prompt_asks_for_undated_not_null():
    out = _prompt()
    assert '`time` is the string "undated" rather than null' in out


def test_it_says_why_null_is_wrong_there():
    """A rule with no reason attached is the first thing dropped in an edit."""
    out = _prompt()
    assert "reads as timestamps we failed to load" in out


def test_null_is_still_allowed_for_a_real_undated_event():
    """Overcorrecting would put "undated" on system events whose time genuinely
    is not recorded — the inverse bug, and it hides a real gap."""
    assert "not as a shorthand" in _prompt()


def test_the_bookend_dates_are_substituted_not_left_as_tokens():
    out = _prompt({"date_of_booking": "2026-07-01", "visitDate": "2026-07-22"})
    assert "<<BOOKING_DATE>>" not in out and "<<VISIT_DATE>>" not in out
    assert "01 Jul" in out and "22 Jul" in out


def test_a_missing_bookend_is_named_rather_than_left_blank():
    """An empty string would leave the rule asking for a date that is not
    there, which is an invitation to invent one."""
    out = _prompt({})
    assert "not recorded — omit this bookend" in out


def test_no_token_survives_the_assembly():
    import re
    left = re.findall(r"<<[A-Z_]+>>", _prompt({"visitDate": "2026-07-22"}))
    assert left == [], f"unsubstituted tokens reach the model: {left}"


def test_the_prompt_stamp_moved_with_the_rule():
    """The stamp is content-addressed so a finding can be tied to a prompt
    body. A rule added without the stamp changing makes "did the new clause
    run?" unanswerable again."""
    from server.prompts import RCA_PROMPT_VERSION, RCA_V4_TEMPLATE, _prompt_digest
    assert RCA_PROMPT_VERSION.endswith(_prompt_digest(RCA_V4_TEMPLATE))


def test_the_voice_line_names_the_closest_macro():
    """"written against 3 approved macros" is true of a perfect fit and of
    three that scraped the bar. Naming the closest is what lets a reader judge
    the reply against something, and it is the only part of the line that
    changes when the match is wrong."""
    e = tone_entry([{"situation": "SP issue - Meeting point issue",
                     "response": "r"},
                    {"situation": "Something else", "response": "r"}],
                   "Experience Issues", "Meeting Point Issues", None)
    assert "SP issue - Meeting point issue" in e["text"], e["text"]
    assert "Something else" not in e["text"], "it named all of them, not the closest"


def test_a_macro_with_no_situation_does_not_render_empty_quotes():
    e = tone_entry([{"situation": "", "response": "r"}], "A", "B", None)
    assert "closest" not in e["text"], e["text"]
    assert "“”" not in e["text"]


def test_the_reply_voice_names_which_copy_it_used():
    """"written against 3 approved replies" is true of both the live sheet and
    the checked-in macros, and someone who just edited the sheet needs to know
    which one they are reading."""
    e = tone_entry([{"situation": "s", "response": "r"}], "Experience Issues",
                   "Meeting Point Issues", None, "", "the live sheet")
    assert "from the live sheet" in e["text"], e["text"]


def test_the_voice_line_names_the_checked_in_copy_too():
    e = tone_entry([{"situation": "s", "response": "r"}], "A", "B", None, "",
                   "the checked-in macros — the live sheet could not be read")
    assert "checked-in macros" in e["text"]
    assert "could not be read" in e["text"], \
        "a missed refresh is invisible on the card"


def test_the_voice_line_is_still_a_sentence_with_no_source():
    """Older drafts and any caller that does not pass one must not render
    'as tone only, from .'"""
    e = tone_entry([{"situation": "s", "response": "r"}], "A", "B", None)
    assert e["text"].rstrip().endswith(".")
    assert ", from" not in e["text"], e["text"]
    assert " ." not in e["text"], e["text"]
