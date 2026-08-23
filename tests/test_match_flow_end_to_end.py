"""The possible-matches flow, checked at every hand-off.

Not "does shortlist work" — that has its own module. This checks the joins
between the pieces, which is where every bug in this area has actually lived:
a field named one thing on one side and another on the other, a search whose
results nothing reads, a candidate built without what the card renders, a
truncated search nobody is told about.

The order of the cascade is asserted here too. Each step exists because the
one before it did not answer, and a step that moves up the list starts
answering for reviews the step above could have matched outright.
"""
import re

import pytest

PIPE   = open("server/pipeline.py", encoding="utf-8").read()
ZD     = open("server/services/zendesk.py", encoding="utf-8").read()
BQ     = open("server/services/bigquery.py", encoding="utf-8").read()
API    = open("server/api.py", encoding="utf-8").read()
CLIENT = open("client/index.html", encoding="utf-8").read()


# ── 1. the cascade runs in the right order, each gated on the last ──────────

def test_the_cascade_order_is_intact():
    order = [
        ("Tier 1 confirmed",        "t1_regex_verified"),
        ("indicator shortlist",     'narrowing_path = "indicator_shortlist"'),
        ("zendesk requester",       "zendesk requester"),
        ("BQ venue+date 30",        'rows = _run_bq_attempt("venue_date_30"'),
        ("BQ venue+date 60",        'rows = _run_bq_attempt("venue_date_60"'),
        ("support contact",         "_sup = await bq.find_via_support("),
        ("untraceable",             "Date-only matching removed"),
    ]
    pos = [(nm, PIPE.find(frag)) for nm, frag in order]
    missing = [nm for nm, i in pos if i < 0]
    assert not missing, f"cascade step(s) missing: {missing}"
    idx = [i for _, i in pos]
    assert idx == sorted(idx), \
        f"the cascade is out of order: {[nm for nm, _ in pos]}"


@pytest.mark.parametrize("marker", [
    'narrowing_path = "indicator_shortlist"',
    "_sup = await bq.find_via_support(",
])
def test_each_later_step_is_gated_on_nothing_having_matched(marker):
    """Without the gate a later step answers for a review an earlier one
    already matched, which is how a working matcher gets second-guessed."""
    head = PIPE[:PIPE.find(marker)]
    # The nearest enclosing `if` above the marker must test cascade_done.
    gates = [m for m in re.finditer(r"if not cascade_done", head)]
    assert gates, f"{marker} has no cascade_done gate above it"
    # ...and nothing may re-open the cascade between that gate and the marker.
    between = head[gates[-1].end():]
    assert "cascade_done = False" not in between, \
        f"the gate above {marker} is reset before it runs"


def test_the_issue_pass_only_runs_when_the_direct_pass_found_nothing():
    """The direct indicators are the matcher. The problem text is a fallback
    and must never displace a name/venue match."""
    i = ZD.find("await _scan(issue_queries, issue_pass=True)")
    assert i > 0
    assert "if not by_bid and issue_queries:" in ZD[:i][-900:], \
        "the issue pass is no longer gated on the direct pass finding nothing"


# ── 2. what the searches return is shaped the way the picker reads ──────────

def test_shortlist_candidates_carry_every_field_the_card_renders():
    """DRIVEN, not read. The shortlist-to-candidate mapping moved into
    `shortlist_rows`, which takes the warehouse lookup as an argument — so the
    thing this test was reading off a source window can now be executed, with
    the warehouse answering nothing so only the ticket's own fields are in
    play. That is the case the mapping exists for.

    The old version sliced pipeline.py between two markers and asserted key
    names appeared inside. It failed the moment the mapping moved, which is
    not the same event as the mapping breaking.
    """
    from server.pipeline import shortlist_rows
    sig = {"booking_id": "32885089", "guest_name": "Mariana Compos",
           "experience": "Eiffel Tower Summit", "visit_date": "2026-08-04",
           "vendor_name": "Acme Tours", "matched_on": ["name"]}
    rows, _ = shortlist_rows([sig], lambda bid: None)
    got = rows[0]
    # The names `_make_candidate` reads, from the names a ticket signal uses.
    assert got["primary_guest_name"] == "Mariana Compos"
    assert got["experienceName"] == "Eiffel Tower Summit"
    assert got["date_of_visit"] == "2026-08-04"
    assert got["vendorName"] == "Acme Tours"
    assert got["id"] == "32885089"


def test_support_candidates_bridge_the_naming_difference():
    """_row_to_dict says guestName; _make_candidate reads primary_guest_name."""
    i = PIPE.find("for _r in _sup[:8]:")
    block = PIPE[i:i + 700]
    assert "primary_guest_name=" in block and "guestName" in block


def test_every_candidate_reaches_the_browser_and_is_read():
    # THE NAMES, not the whitespace. This matched the exact spacing of one
    # line, so wrapping the value in `_scrub_candidate_names(...)` — which
    # still sends every candidate, and sends them with the PII hashes taken
    # out — read as "the candidates no longer reach the browser".
    i = API.find('"candidates_list":')
    assert i > 0, "candidates_list is no longer sent to the browser"
    assert "d.candidates_list" in API[i:i + 200], API[i:i + 200]
    assert "r.candidatesList = draft.candidates_list.map" in CLIENT
    # Sliced to the end of the remap rather than to a character count; see
    # tests/test_io_contracts.py::_candidate_mapping for what that cost.
    j = CLIENT.find("r.candidatesList = draft.candidates_list.map")
    mapping = CLIENT[j:CLIENT.find("}))", j)]
    for key in ("experience", "experienceDate", "guestName", "matchReasons", "bid"):
        assert key in mapping, f"the picker no longer reads {key}"


def test_confirming_a_candidate_finds_it_by_the_id_it_was_given():
    """The picker sends back c.bid, which it read from c.id. The confirm
    endpoint looks the candidate up by "id" — those must be the same field."""
    assert 'bid:            c.id || c.bid' in CLIENT
    assert 'c["id"] == body.bid' in API


# ── 3. the searches are bounded, and say when they were not enough ──────────

def test_every_search_carries_a_date_floor():
    """Zendesk returns at most a fixed number of rows and drops the rest with
    no indication.

    The combined queries were left unbounded on the assumption they could not
    truncate. Live data disproved it: 'type:ticket Tom Tom guided tour' and
    'type:ticket Tom Tom France' both hit the cap, because Zendesk ANDs words
    that appear in almost every ticket. An unbounded query that truncates
    drops matches arbitrarily, which is worse than a floor dropping old ones.
    """
    assert "BOUND = f\" created>{since}\" if since else \"\"" in ZD
    built = re.findall(r"queries\.append\(\(f'([^']+)'", ZD)
    built += re.findall(r"issue_queries\.append\(\(f'([^']+)'", ZD)
    assert built, "no queries found to check"
    unbounded = [q for q in built if "{BOUND}" not in q]
    assert not unbounded, f"these queries can truncate silently: {unbounded}"


def test_the_pipeline_passes_the_floor_and_reads_the_notes():
    i = PIPE.find("_short = await zendesk.shortlist(")
    block = PIPE[i - 900:i + 2400]
    assert "since=_since" in block, "the date floor is never passed"
    assert "notes=_notes" in block, "truncation is reported and never read"
    assert "truncated" in block, "a truncated search is not surfaced"


def test_truncation_reaches_the_associate_not_just_the_log():
    """A log line is not a disclosure. Five candidates from a truncated search
    does not mean five exist, and the card is where that has to be said."""
    i = PIPE.find('if _n.get("kind") == "truncated"')
    assert i > 0, "truncation is not surfaced at all"
    block = PIPE[i:i + 700]
    assert "confidence_trail.append" in block
    # Matched on the DISCLOSURE, not on a sentence. This assertion broke
    # when the wording was shortened, which is what a source assertion
    # does: it spell-checks a string rather than testing the behaviour.
    assert "may not be" in block, \
        "the truncation warning no longer tells the reader the right "\
        "booking may be missing"
    # The branch existing is not enough — it has to actually be reached. A
    # disabled loop above it leaves all of the above true and nothing shown.
    loop = PIPE[:i][-400:]
    assert re.search(r"for _n in _notes\s*:", loop), \
        "the truncation branch is unreachable — nothing iterates the notes"


def test_the_confidence_trail_is_shown():
    assert '"confidence_trail":   d.confidence_trail or []' in API
    assert "r.confidenceTrail" in CLIENT


# ── 4. the guards that stop a fallback from behaving like the matcher ───────

def test_the_support_search_needs_two_facts():
    i = BQ.find("def support_search_sql")
    block = BQ[i:i + 1600]
    assert "if not (dates and tgids):" in block, \
        "the support search would run on one fact alone"


def test_the_support_search_never_matches_a_name():
    """639,109 of 639,109 bookings behind a support contact carry a PII hash
    in primary_guest_name."""
    i = BQ.find("def support_search_sql")
    block = BQ[i:BQ.find("async def find_via_support")]
    # Only the filter construction — the SELECT list legitimately returns the
    # column so the value can be inspected and blanked when it is a hash.
    where = block[block.find("where, params"):block.find("sql = f")]
    assert "primary_guest_name" not in where, \
        "the guest name is back in the WHERE clause"
    assert "@name" not in block, "a name parameter is back in the query"


def test_an_unverified_booking_id_never_becomes_the_match():
    """A 7-12 digit number in prose may be an order number or an amount. It
    becomes a candidate to confirm, never the matched booking."""
    i = PIPE.find("bq_row[\"low_confidence_bid_match\"] = True")
    assert i > 0
    block = PIPE[i - 500:i + 1400]
    assert "candidates = [_shape_weak_bid(bq_row, _why)]" in block
    assert "match_tier = 2" in block
    # booking must NOT be set on this path
    assert "booking = bq_row" not in block


# ── 5. the diagnostic tools must call the code they claim to test ───────────

TOOL = open("tools/rematch.py", encoding="utf-8").read()


def test_rematch_calls_find_via_support_with_the_signature_it_has():
    """The signature changed from author= to tgids= and the tool kept passing
    author=, which is a TypeError the moment step 3 is reached — on exactly
    the reviews the tool exists to diagnose."""
    assert "author=" not in TOOL.split("find_via_support(")[1][:80], \
        "rematch passes a keyword find_via_support no longer accepts"
    assert "tgids=tgids" in TOOL


def test_rematch_resolves_venues_before_the_support_search():
    """Without TGIDs the search declines to run, so a tool that does not
    resolve them would report 'nothing' on every review and look like a
    broken path rather than an unresolved venue."""
    assert "venue_resolver.resolve(hints)" in TOOL


def test_rematch_searches_the_way_the_pipeline_searches():
    """A diagnostic that omits the date floor is not testing what runs."""
    assert "since=_since_for(rv)" in TOOL
    assert "SHORTLIST_LOOKBACK_DAYS" in TOOL, \
        "the tool must take the floor from the pipeline, not invent one"
    assert TOOL.count("notes=notes") >= 2, \
        "both the single-review and batch paths must read the search notes"


def test_rematch_reports_truncation_it_is_told_about():
    for marker in ("hit Zendesk's result", "incomplete search"):
        assert marker in TOOL, f"truncation is collected but not reported ({marker})"


def test_rematch_tells_shortlist_the_review_date():
    """Without it, shortlist cannot tell a date the guest wrote from one the
    model inferred from "today" — so the tool reported five Amandas on five
    continents as matches while the pipeline, which does pass it, would not.
    A diagnostic that omits a parameter is diagnosing something else."""
    assert TOOL.count("review_date=_review_date(rv)") >= 2, \
        "both the single-review and batch paths must pass the review date"


def test_the_pipeline_tells_shortlist_the_review_date():
    i = PIPE.find("_short = await zendesk.shortlist(")
    assert "review_date=" in PIPE[i:i + 400]


# ── 6. the requester path: reasons must belong to the booking they are on ───

def test_requester_candidates_get_their_own_match_reasons():
    """This path built one reason list from any() over the whole set and put
    it on every card: one booking with a venue match made all five say
    "venue", one ticket-text hit made all five say "ticket-text". The card
    exists to tell an associate why a booking is in front of them, and four
    out of five were reading a reason that belonged to a different booking.

    It was also the same list object in all five, so anything appending to one
    would have appended to all."""
    i = PIPE.find("candidates = [_make_candidate(row, \"zendesk_requester\",")
    assert i > 0, "the requester candidate build is gone"
    block = PIPE[i - 1400:i + 400]
    assert "_reasons_for(bid, row)" in block, \
        "candidates are not getting per-booking reasons"
    assert "def _reasons_for(bid, row):" in block
    # the set-wide version must not come back
    assert "elif any(_ticket_pts(b) > 0 for b, _ in ranked):" not in PIPE, \
        "a set-wide any() is deciding a per-candidate reason again"


def test_requester_reasons_use_the_same_scorers_as_the_ranking():
    """A card saying "venue" beside a venue score of 0 is worse than a card
    saying nothing."""
    i = PIPE.find("def _reasons_for(bid, row):")
    block = PIPE[i:i + 700]
    for scorer in ("_both_pts(bid)", "_venue_pts(row, bid)", "_ticket_pts(bid)"):
        assert scorer in block, f"{scorer} is not what decides the reason"


def test_the_date_only_path_warns_that_it_is_weak():
    """zendesk_requester_date_only means no venue agreed and the ranking is on
    visit-date closeness, which proves very little."""
    i = PIPE.find('narrowing_path = ("zendesk_requester_candidates" if venue_signal')
    assert i > 0
    block = PIPE[i:i + 1200]
    assert "These are weak" in block, "the trail no longer says these are weak"
    # and the picker must render that warning
    assert "venueSignal" in CLIENT and "scoreVenue" in CLIENT


def test_a_single_requester_bid_is_not_auto_promoted_on_a_weak_name():
    """Being the only survivor is not evidence. A wrong BID promoted to Tier 1
    presents as a direct match and the whole RCA is built on another guest's
    booking.

    DRIVEN, not spelled. This used to assert that `_conf >= 3.0` appeared
    within 300 characters of `_conf = _score(...)`, which broke the moment a
    comment was written above the line and would have passed against a build
    where the branch had become unreachable. The rule now lives in
    `tier1_promotable` and is exercised directly.
    """
    from server.pipeline import tier1_promotable
    ok, why = tier1_promotable(2.9, 2.9)
    assert ok is False, "a score below the threshold was promoted"
    assert "below the 3.0" in why, why


def test_a_full_name_agreement_alone_is_not_a_tier_1_match():
    """THE REPORTED BUG. _name_pts returns 3.0 for a full name agreement,
    which cleared the 3.0 threshold by itself — so a review with no booking id,
    no venue and no city came back "T1 · BID 33211960" above a trail reading
    venue='—' city='—' visit≈'—'."""
    from server.pipeline import tier1_promotable
    ok, why = tier1_promotable(3.0, 0.0)
    assert ok is False, (
        "a guest name on its own still promotes to Tier 1, which asserts a "
        "confidence the trail on the same card contradicts")
    assert "only agreement is the guest name" in why, why


def test_a_corroborated_name_still_promotes():
    """The other direction. A rule that promotes nothing is not a fix — venue,
    date or ticket agreement alongside the name is a real match and must still
    reach Tier 1."""
    from server.pipeline import tier1_promotable
    assert tier1_promotable(4.0, 1.0)[0] is True
    assert tier1_promotable(3.0, 0.5)[0] is True


def test_the_two_refusals_do_not_read_alike():
    """"below the threshold" and "nothing but the name" are different facts
    and send the reader to different places."""
    from server.pipeline import tier1_promotable
    a = tier1_promotable(1.0, 0.0)[1]
    b = tier1_promotable(3.0, 0.0)[1]
    assert a and b and a != b, (a, b)


def test_the_promotion_rule_is_the_one_the_pipeline_uses():
    """The wire. A rule the pipeline does not call is a rule that does not
    exist, and this file has a history of asserting exactly that."""
    import inspect
    from server import pipeline as P
    src = inspect.getsource(P.process_review)
    assert "tier1_promotable(" in src, (
        "process_review no longer calls tier1_promotable, so the rule above "
        "guards nothing")



def test_an_unavailable_model_is_disclosed_like_an_unavailable_warehouse():
    """Everything after matching is written by the model. With it unavailable
    the classification, the RCA and the reply all come back empty and the card
    renders blank — indistinguishable from a review too thin to say anything
    about. BigQuery being down was already disclosed; this half was not."""
    assert 'not MOCK_MODE and not is_live("anthropic")' in PIPE, \
        "the pipeline never checks whether the model is available"
    i = PIPE.find('_ai_down = not MOCK_MODE and not is_live("anthropic")')
    assert i != -1, "the availability check no longer feeds a named flag"
    block = PIPE[i:i + 900]
    assert "confidence_trail.append" in block, \
        "it is logged but never shown to the person reading the card"
    assert "not because there was nothing to say" in block
    # The flag gates the per-field disclosures too: with the provider down,
    # every model-written field is empty and one sentence covers all of them.
    # Repeating it for the stated issue and again for the classification is
    # three warnings for one fact.
    # classification_entry moved into the warehouse-recovery if/elif/else, so
    # the suppression now reads as an explicit `if _ai_down: _cls_entry = None`
    # branch rather than a one-line ternary — same guarantee, different shape.
    assert "_ai_down else stated_issue_entry" in PIPE, \
        "the stated-issue warning is not suppressed when the provider is down"
    assert "if _ai_down:\n            _cls_entry = None" in PIPE, \
        "the classification warning is not suppressed when the provider is down"
    # ...and it must NOT fire in MOCK_MODE, where claude._call still reaches
    # the model and the RCA really is generated. Warning there would tell an
    # associate the analysis in front of them does not exist.
    assert "not MOCK_MODE and" in block[:120]


from server.pipeline import partial_trail                     # noqa: E402


def test_a_half_finished_run_says_so_on_the_row():
    """The early match persist writes the MATCHING half and replaces whatever
    a completed run left, while generated_at is untouched until the end. A run
    that dies after matching therefore leaves a draft that looks finished —
    old timestamp, full rca_v3, every column populated — with every analysis
    disclosure absent. Absent reads as "nothing to report".

    Driven, not grepped: the source assertion this replaces stayed green
    against a build where the marker was unreachable, because the string was
    still in the file."""
    got = partial_trail([{"mark": "pass", "text": "<strong>BID extracted</strong>"}])
    assert len(got) == 2, got
    assert got[0]["text"].startswith("<strong>BID extracted"), "the match half was lost"
    assert got[-1]["mark"] == "warn"
    assert "has not finished" in got[-1]["text"]
    assert "re-run the review" in got[-1]["text"], "it does not say what to do"


def test_the_marker_is_appended_to_a_new_list():
    """It must not mutate the caller's trail. The analysis appends to that
    same list afterwards, and the final save writes it — a marker left in it
    would ride through to a completed run."""
    original = [{"mark": "pass", "text": "a"}]
    got = partial_trail(original)
    assert original == [{"mark": "pass", "text": "a"}], "the caller's list was mutated"
    assert got is not original


def test_an_empty_trail_still_gets_the_marker():
    assert len(partial_trail([])) == 1
    assert len(partial_trail(None)) == 1


def test_the_pipeline_uses_it_at_the_early_persist():
    assert "_d.confidence_trail   = partial_trail(confidence_trail)" in PIPE, \
        "the early persist no longer marks itself as partial"


def test_the_final_save_replaces_the_partial_trail():
    """The marker has to be removed by finishing, or every completed run
    carries a warning that its analysis never ran — the inverse bug, and it
    would train people to ignore the mark."""
    i = PIPE.find("draft.confidence_trail     = confidence_trail")
    assert i != -1, "the final save no longer writes the whole trail"
    # The final save assigns the list itself, so the marker cannot survive a
    # completed run.
    assert "partial_trail(" not in PIPE[i:i + 200], \
        "the final save marks a finished run as unfinished"


def test_the_two_writes_are_distinguishable():
    """The whole point: a reader has to be able to tell a finished run from a
    half-finished one by looking at the row."""
    early = PIPE.find("_d.confidence_trail   = partial_trail(")
    final = PIPE.find("draft.confidence_trail     = confidence_trail")
    assert early != -1 and final != -1
    assert early < final, "the early persist must come first"
    assert "has not finished" not in PIPE[final:final + 300], \
        "the final save writes the partial marker too"
