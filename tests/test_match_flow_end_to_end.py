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
    """The shortlist hands raw ticket signals to _make_candidate. Each key it
    reads has to be supplied under the name it expects."""
    i = PIPE.find("_short = await zendesk.shortlist(")
    block = PIPE[i:i + 3200]
    for key in ("primary_guest_name", "experienceName", "date_of_visit",
                "vendorName"):
        assert key in block, f"{key} is not passed through to the candidate"
    assert '_sig.get("guest_name"' in block, "ticket signals name it guest_name"
    assert '_sig.get("visit_date"' in block, "ticket signals name it visit_date"


def test_support_candidates_bridge_the_naming_difference():
    """_row_to_dict says guestName; _make_candidate reads primary_guest_name."""
    i = PIPE.find("for _r in _sup[:8]:")
    block = PIPE[i:i + 700]
    assert "primary_guest_name=" in block and "guestName" in block


def test_every_candidate_reaches_the_browser_and_is_read():
    assert '"candidates_list":    d.candidates_list or []' in API
    assert "r.candidatesList = draft.candidates_list.map" in CLIENT
    i = CLIENT.find("r.candidatesList = draft.candidates_list.map")
    mapping = CLIENT[i:i + 1300]
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
    block = PIPE[i - 700:i + 1600]
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
    assert "may not be here" in block or "not everything" in block
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
