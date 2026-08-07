"""The candidate picker offered "#32885089 · —" and asked someone to choose.

The indicator shortlist built each card out of the Zendesk ticket's own custom
fields and never asked BigQuery. Those fields are blank on exactly the tickets
this search returns — the ones found by a requester-name search, which
`matches_indicators` deliberately keeps on the grounds that

    "the tickets with an empty guest-name field are the SAME sparse tickets
     that have an empty booking-id field"

They are kept because an empty field cannot contradict the review. What that
left was a card with nothing on it to choose by, in front of the one decision
the whole RCA is built on: a wrongly confirmed booking makes every finding
about somebody else's trip.

The sibling path already resolved every BID through `verify_bid` before
showing it, so the same kind of id produced a full card through one Zendesk
path and a blank one through the other, decided by which search found it.

`lookup` is injected, so these run without BigQuery and can make it fail on
purpose — which is the case that matters most, because a lookup that did not
complete must not read like a booking the warehouse does not have.
"""
import pytest

from server.pipeline import shortlist_rows, shortlist_lookup_trail


def _sig(bid="32885089", **kw):
    base = {"booking_id": bid, "guest_name": "", "experience": "",
            "visit_date": "", "vendor_name": "", "matched_on": ["name"]}
    base.update(kw)
    return base


def _bq(**kw):
    base = {"id": "32885089", "experienceName": "Eiffel Tower Summit",
            "date_of_visit": "2026-08-04", "vendorName": "Acme Tours",
            "primary_guest_name": "Mariana Compos", "tid": "1", "tgid": "2",
            "vid": "3"}
    base.update(kw)
    return base


# ── the warehouse is asked, and its answer reaches the card ────────────────

def test_a_blank_ticket_gets_its_details_from_the_warehouse():
    """The reported card. Every field the picker shows was empty on the
    ticket, and every one of them exists on the booking."""
    rows, tally = shortlist_rows([_sig()], lambda bid: _bq())
    assert tally == {"found": 1, "absent": 0, "failed": 0}
    r = rows[0]
    assert r["experienceName"] == "Eiffel Tower Summit"
    assert r["date_of_visit"] == "2026-08-04"
    assert r["vendorName"] == "Acme Tours"
    assert r["details_lookup"] == "found"


def test_the_id_the_ticket_named_is_the_id_looked_up():
    seen = []
    shortlist_rows([_sig("111"), _sig("222")],
                   lambda bid: seen.append(bid) or _bq())
    assert seen == ["111", "222"]


def test_every_signature_produces_exactly_one_card():
    """A lookup that returns nothing must not make the option disappear — the
    booking is still one of the ones the guest's tickets point at, and a
    shortlist that silently shrinks is a shortlist the reader cannot audit."""
    rows, _ = shortlist_rows([_sig("1"), _sig("2"), _sig("3")],
                             lambda bid: None)
    assert [r["id"] for r in rows] == ["1", "2", "3"]


# ── which source wins ──────────────────────────────────────────────────────

def test_the_warehouse_wins_over_the_ticket_where_both_have_a_value():
    """The booking's own record is authoritative wherever it is readable."""
    rows, _ = shortlist_rows(
        [_sig(experience="Whatever the agent typed", visit_date="2026-01-01")],
        lambda bid: _bq())
    assert rows[0]["experienceName"] == "Eiffel Tower Summit"
    assert rows[0]["date_of_visit"] == "2026-08-04"


def test_the_ticket_fills_a_gap_the_warehouse_leaves():
    """An empty warehouse field must not blank out a value we do have. This
    is the merge direction that is easy to get backwards, and getting it
    backwards produces the same blank card the fix is for."""
    rows, _ = shortlist_rows(
        [_sig(vendor_name="Acme via the ticket")],
        lambda bid: _bq(vendorName="", experienceName="Eiffel Tower Summit"))
    assert rows[0]["vendorName"] == "Acme via the ticket"
    assert rows[0]["experienceName"] == "Eiffel Tower Summit"


def test_the_zendesk_guest_name_is_kept_alongside_the_warehouse_one():
    """On a hashed booking the ticket holds the only readable copy of the
    strongest identifier after the booking id — but it must not overwrite the
    booking's own record. Both are carried; the API decides which to show."""
    rows, _ = shortlist_rows(
        [_sig(guest_name="Mariana Compos")],
        lambda bid: _bq(primary_guest_name="jVwe+fjfm48WSok1xEK+I/8fnI="))
    assert rows[0]["primary_guest_name"] == "jVwe+fjfm48WSok1xEK+I/8fnI="
    assert rows[0]["zendesk_guest_name"] == "Mariana Compos"


def test_the_match_reasons_survive_the_lookup():
    """The chips are why this booking is in front of the reader at all."""
    rows, _ = shortlist_rows([_sig(matched_on=["name", "venue"])],
                             lambda bid: _bq())
    assert rows[0]["matched_on"] == ["name", "venue"]


def test_a_signature_with_no_reasons_still_carries_one():
    rows, _ = shortlist_rows([_sig(matched_on=None)], lambda bid: _bq())
    assert rows[0]["matched_on"] == ["name"]


# ── the three outcomes are three different facts ───────────────────────────

def test_a_booking_the_warehouse_does_not_have_says_so():
    rows, tally = shortlist_rows([_sig()], lambda bid: None)
    assert rows[0]["details_lookup"] == "absent"
    assert tally == {"found": 0, "absent": 1, "failed": 0}


def test_a_lookup_that_raises_is_not_reported_as_a_missing_booking():
    """THE PAIR THAT MUST NOT COLLAPSE. "the warehouse does not have this id"
    is a dead end an associate acts on; "the lookup did not complete" is a
    re-run. One sentence for both sends people looking for a booking that is
    sitting there."""
    def _boom(bid):
        raise RuntimeError("bigquery is down")
    rows, tally = shortlist_rows([_sig()], _boom)
    assert rows[0]["details_lookup"] == "failed"
    assert tally == {"found": 0, "absent": 0, "failed": 1}


def test_one_failing_lookup_does_not_take_the_others_down():
    def _flaky(bid):
        if bid == "2":
            raise RuntimeError("boom")
        return _bq(id=bid)
    rows, tally = shortlist_rows([_sig("1"), _sig("2"), _sig("3")], _flaky)
    assert [r["details_lookup"] for r in rows] == ["found", "failed", "found"]
    assert tally == {"found": 2, "absent": 0, "failed": 1}


def test_a_signature_with_no_booking_id_is_never_looked_up():
    called = []
    rows, tally = shortlist_rows([_sig("")], lambda bid: called.append(bid))
    assert not called, "an empty id was sent to the warehouse"
    assert rows[0]["details_lookup"] == "absent"


def test_an_empty_shortlist_produces_nothing_and_no_tally_noise():
    rows, tally = shortlist_rows([], lambda bid: _bq())
    assert rows == []
    assert tally == {"found": 0, "absent": 0, "failed": 0}


# ── the trail says what could not be read ──────────────────────────────────

def test_a_fully_read_shortlist_adds_no_trail_line():
    """"0 could not be read" on every healthy shortlist is the noise that
    makes a reader stop reading the counts that matter."""
    assert shortlist_lookup_trail({"found": 3, "absent": 0, "failed": 0}) is None


def test_an_empty_shortlist_adds_no_trail_line():
    assert shortlist_lookup_trail({"found": 0, "absent": 0, "failed": 0}) is None


def test_a_missing_booking_is_counted_on_the_trail():
    got = shortlist_lookup_trail({"found": 2, "absent": 1, "failed": 0})
    assert got and got["mark"] == "warn"
    assert "2 read from the warehouse" in got["text"], got
    assert "1 not in the warehouse" in got["text"], got


def test_a_failed_lookup_is_counted_separately_from_a_missing_one():
    got = shortlist_lookup_trail({"found": 0, "absent": 1, "failed": 2})
    assert "1 not in the warehouse" in got["text"], got
    assert "2 could not be looked up" in got["text"], got
    assert "nothing about them was ruled out" in got["text"], got


def test_the_trail_line_counts_every_option_it_describes():
    got = shortlist_lookup_trail({"found": 1, "absent": 1, "failed": 1})
    assert "these 3 option(s)" in got["text"], got


# ── the card message is per booking, not per path ──────────────────────────

def test_the_card_no_longer_infers_the_message_from_the_path():
    """NEGATIVE source assertion on CLIENT-SIDE JAVASCRIPT, which has no test
    harness here — the exception CLAUDE.md names.

    The empty-details message used to branch on
    `c.narrowing_path === 'indicator_shortlist'` meaning "this path never
    queried the warehouse". That stopped being true the moment the shortlist
    started resolving its ids, and a message inferred from a path goes stale
    silently while still reading as an explanation."""
    src = open("client/index.html", encoding="utf-8").read()
    assert "narrowing_path === 'indicator_shortlist'" not in src, \
        "the card is inferring the empty-details reason from the path again"


def test_the_lookup_answer_is_not_dropped_by_the_client_remap():
    """The remap builds a FIXED SHAPE, so a field not named in it is dropped
    and every branch reading that field silently stops firing. Client-side
    JavaScript, no harness — see the docstring above."""
    src = open("client/index.html", encoding="utf-8").read()
    i = src.find("r.candidatesList = draft.candidates_list.map")
    assert i > 0
    assert "details_lookup" in src[i:src.find("}))", i)], \
        "details_lookup never reaches the card"


def test_the_pipeline_copies_the_lookup_answer_onto_the_candidate():
    """THE WIRING HALF, which driving `shortlist_rows` cannot show.

    `shortlist_rows` sets `details_lookup` on the row it returns; the branch
    in `process_review` then has to copy it onto the candidate object the
    picker is actually handed. Drop that one line and every test above still
    passes while the card silently loses the answer — a mutation deleting it
    survived the whole file and proved exactly that.

    The branch is inside a coroutine wanting a database, Claude, Zendesk and
    BigQuery, which is why this is read rather than driven — the same
    compromise, and for the same reason, as
    test_the_pipeline_puts_the_zendesk_name_onto_the_candidate.
    """
    import inspect
    from server import pipeline
    src = inspect.getsource(pipeline)
    assert '_c["details_lookup"] = _row["details_lookup"]' in src, (
        "the shortlist candidate no longer carries the warehouse's answer, so "
        "the card cannot tell 'not in the warehouse' from 'could not be asked'")
