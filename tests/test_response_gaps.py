"""Being left unanswered is computed, not noticed.

The timeline says what happened. It cannot say what DIDN'T — and a guest who
wrote three times over two days before anyone replied is a failure that lives
entirely in the space between rows. The checklist has had the words since
v7.1 ("Delayed response to guest", "2+ non-autoresolved queries from the
guest", "Guest query not addressed / no response given") and nothing computed
them: they were left for the model to spot in a list of forty events, which it
does when the gap is glaring and misses when it is merely long.

IT IS A FLAG, NOT A TIMELINE ROW. The timeline states what the events say;
being left waiting is a finding about our handling, and findings route to a
team.
"""
import pytest

from server.response_gaps import (response_gaps, gap_flags,
                                  promised_window, CHASE_FLAG)


def _e(t, actor, body=""):
    return {"time_sort": t, "actor": actor, "raw_body": body}


# ── what we promised is the clock ──────────────────────────────────────────
#
# An earlier version flagged any silence over 12 hours. That is a rule nobody
# agreed to: it accuses us of lateness against a deadline we never set, and it
# says nothing when we promise two hours and take eight. The commitment we
# made to the guest is the only honest measure, and it is in our own message.

@pytest.mark.parametrize("text,hours,has_phrase", [
    ("We will get back to you within 24 hours.", 24, True),
    ("Our team will respond in 2-3 business days.", 72, True),
    ("I will update you in 48 hrs", 48, True),
    ("no later than 7 days", 168, True),
    ("We will revert in 1 week", 168, True),
    ("Sorry for the trouble, here is your refund.", None, False),
])
def test_the_timeframe_is_read_out_of_our_own_message(text, hours, has_phrase):
    got, phrase = promised_window(text)
    assert got == hours, (text, got)
    assert bool(phrase) is has_phrase, (text, phrase)


def test_a_range_is_measured_at_its_OUTER_bound():
    """"2-3 business days" is not missed until the third day is gone. Reading
    the inner bound would flag us for a reply that arrived inside what the
    guest was told."""
    assert promised_window("in 2-3 business days")[0] == 72


def test_a_promise_with_no_number_is_recorded_but_not_a_deadline():
    """"Shortly" IS a promise — the guest was told to expect something soon —
    but nothing can be missed on paper. Inventing a duration for it would be
    us setting the deadline and then judging ourselves against it."""
    hours, phrase = promised_window("We will revert shortly.")
    assert hours is None
    assert phrase, "a vague promise was not recorded at all"


def test_missing_a_stated_timeframe_is_flagged():
    got = response_gaps([
        _e("2026-08-02T09:00", "guest"),
        _e("2026-08-02T09:05", "co", "We will get back to you within 24 hours."),
        _e("2026-08-03T10:07", "co", "Sorted")])
    assert got and got[0]["check"] == "Missed follow-ups or deadline crossed", got
    assert "24h we stated" in got[0]["detail"], got[0]["detail"]


def test_keeping_a_stated_timeframe_is_not_flagged():
    """The case the old constant got wrong in the other direction: 25 hours is
    late against 24 and perfectly fine against 48."""
    assert response_gaps([
        _e("2026-08-02T09:00", "guest"),
        _e("2026-08-02T09:05", "co", "We will revert in 48 hours."),
        _e("2026-08-03T10:07", "co", "Sorted")]) == []


def test_a_promise_never_returned_to_is_flagged():
    got = response_gaps([
        _e("2026-08-02T09:00", "guest"),
        _e("2026-08-02T09:05", "co", "We will revert within 24 hours."),
        _e("2026-08-05T09:00", "review")])
    assert got and any(g["check"] == "Missed follow-ups or deadline crossed"
                       for g in got), got


def test_a_vague_promise_is_only_raised_when_nothing_came_at_all():
    """It cannot be late, but it can be unkept."""
    never = response_gaps([
        _e("2026-08-02T09:00", "guest"),
        _e("2026-08-02T09:05", "co", "We will revert shortly."),
        _e("2026-08-05T09:00", "review")])
    assert never, "a promise nobody returned to raised nothing"
    kept = response_gaps([
        _e("2026-08-02T09:00", "guest"),
        _e("2026-08-02T09:05", "co", "We will revert shortly."),
        _e("2026-08-04T09:05", "co", "Sorted")])
    assert kept == [], "a vague promise was treated as a deadline"


# ── elapsed time alone is NOT a finding ────────────────────────────────────

def test_a_slow_reply_with_no_promise_and_no_chase_raises_nothing():
    """THE CORRECTION. Without a commitment there is no breach, and inventing
    one turns the flags section into a clock nobody set."""
    assert response_gaps([_e("2026-08-02T09:00", "guest"),
                          _e("2026-08-04T10:07", "co", "Sorted")]) == []


def test_a_guest_who_wrote_twice_is_flagged_without_any_clock():
    """They were plainly waiting, and said so by writing again — that is the
    guest telling us we were late, which beats any threshold we could pick."""
    got = response_gaps([_e("2026-08-02T09:14", "guest"),
                         _e("2026-08-02T10:14", "guest"),
                         _e("2026-08-02T10:30", "co")])
    assert got and got[0]["check"] == "2+ non-autoresolved queries from the guest"
    assert got[0]["chases"] == CHASE_FLAG, got


def test_a_silence_that_never_ended_is_its_own_check():
    """No reply from us anywhere. Different from a slow reply, and the reader
    acts on it differently."""
    got = response_gaps([_e("2026-08-02T09:14", "guest"),
                         _e("2026-08-05T09:00", "review")])
    assert got and got[0]["check"] == "Guest query not addressed / no response given"
    assert got[0]["answered"] is False


def test_the_unanswered_case_names_the_follow_ups():
    got = response_gaps([_e("2026-08-02T09:14", "guest"),
                         _e("2026-08-02T19:02", "guest"),
                         _e("2026-08-03T08:41", "guest"),
                         _e("2026-08-05T09:00", "review")])
    assert "followed up 2 more times" in got[0]["detail"], got[0]["detail"]


def test_a_prompt_reply_raises_nothing():
    assert response_gaps([_e("2026-08-02T09:14", "guest"),
                          _e("2026-08-02T11:07", "co")]) == []


def test_a_timeline_with_no_guest_message_raises_nothing():
    assert response_gaps([_e("2026-08-02T09:14", "system"),
                          _e("2026-08-03T10:07", "co")]) == []


def test_an_empty_timeline_does_not_raise():
    for v in ([], None, [None, "not a dict"]):
        assert response_gaps(v) == []


# ── what counts as an answer ───────────────────────────────────────────────

def test_a_system_mail_is_not_a_reply():
    """An automated "we've received your message" is exactly what the guest
    was not asking for. Counting it as an answer is how a two-day silence
    reads as handled."""
    got = response_gaps([_e("2026-08-02T09:14", "guest"),
                         _e("2026-08-02T09:15", "system"),
                         _e("2026-08-02T19:00", "guest"),
                         _e("2026-08-03T10:07", "co")])
    assert got, "an automated acknowledgement closed the guest's wait"


def test_the_supply_partner_replying_is_not_us_replying():
    """The SP being slow is a different finding and routes to a different
    team. It does not close a silence on our side."""
    got = response_gaps([_e("2026-08-02T09:14", "guest"),
                         _e("2026-08-02T12:00", "sp"),
                         _e("2026-08-02T19:00", "guest"),
                         _e("2026-08-03T10:07", "co")])
    assert got, "an SP message closed the guest's wait"


# ── two silences are two findings ──────────────────────────────────────────

def test_two_separate_silences_are_two_findings():
    """Merging them into one summary hides the second, and each is a separate
    thing that went wrong."""
    got = response_gaps([
        _e("2026-08-01T09:00", "guest"), _e("2026-08-01T10:00", "guest"),
        _e("2026-08-02T09:00", "co"),
        _e("2026-08-03T09:00", "guest"), _e("2026-08-03T10:00", "guest"),
        _e("2026-08-04T09:00", "co")])
    assert len(got) == 2, got


def test_events_are_read_in_time_order_not_list_order():
    """The list arrives sorted, but a hand-added row can land anywhere."""
    got = response_gaps([_e("2026-08-03T10:07", "co"),
                         _e("2026-08-02T19:00", "guest"),
                         _e("2026-08-02T09:14", "guest")])
    assert got and got[0]["check"] == "2+ non-autoresolved queries from the guest"


# ── times we cannot read ───────────────────────────────────────────────────

def test_an_undated_event_is_skipped_and_counted_never_guessed_at():
    """A duration computed from a time we invented is a number a reader cannot
    check, and they would act on it."""
    got = response_gaps([_e("2026-08-02T09:14", "guest"),
                         {"actor": "co", "time_sort": ""},
                         _e("2026-08-02T19:00", "guest"),
                         _e("2026-08-03T10:07", "co")])
    assert got, "the undated reply silently closed the gap"
    assert got[0]["undated_events"] == 1, got[0]


def test_an_unanswered_gap_is_measured_to_the_last_known_event():
    """Not to now — that would grow every time the card is opened and make the
    number meaningless."""
    a = response_gaps([_e("2026-08-02T09:14", "guest"),
                       _e("2026-08-03T09:14", "review")])
    assert a[0]["hours"] == pytest.approx(24.0, abs=0.1)


# ── as flags ───────────────────────────────────────────────────────────────

def test_the_flags_route_to_the_support_team():
    """These are failures of OUR handling. The supply partner being slow is a
    different finding and routes elsewhere."""
    fl = gap_flags([_e("2026-08-02T09:14", "guest"),
                    _e("2026-08-02T19:00", "guest"),
                    _e("2026-08-03T10:07", "co")])
    assert fl and all(f["team"] == "CO" for f in fl), fl
    assert fl[0]["evidence"].strip(), fl


def test_the_flag_names_the_check_from_the_checklist_vocabulary():
    """A flag whose wording is not in CE_ERROR_CHECKS routes nowhere and
    aggregates against nothing."""
    from server.checklist import CE_ERROR_CHECKS
    fl = gap_flags([_e("2026-08-02T09:14", "guest"),
                    _e("2026-08-02T19:00", "guest"),
                    _e("2026-08-03T10:07", "co")])
    assert fl[0]["flag"] in CE_ERROR_CHECKS, fl[0]["flag"]


# ── and it reaches the card ────────────────────────────────────────────────

def _validate(events, flags=None):
    from server.services.rca_v4_validate import validate
    return validate({"l1": "Operations Issue", "l2": "Ticket Issues",
                     "flags": flags or [],
                     "what_went_wrong": {"guest_issues": []},
                     "takedown": {"verdict": "No"}},
                    ["Tickets sent late"], events=events)


def test_the_gap_flag_reaches_the_validated_flags():
    out, _ = _validate([_e("2026-08-02T09:14", "guest"),
                        _e("2026-08-02T19:00", "guest"),
                        _e("2026-08-03T10:07", "co")])
    assert any(f["flag"] == "2+ non-autoresolved queries from the guest"
               for f in out["flags"]), out["flags"]


def test_a_measured_flag_is_announced_on_the_trail():
    """It is on the card for a different reason from the rest — measured, not
    read out of the model's answer — and a reader comparing two runs deserves
    to know which."""
    _, notes = _validate([_e("2026-08-02T09:14", "guest"),
                          _e("2026-08-02T19:00", "guest"),
                          _e("2026-08-03T10:07", "co")])
    assert any("response-gap" in n for n in notes), notes


def test_it_does_not_duplicate_a_flag_the_model_already_raised():
    CHECK = "2+ non-autoresolved queries from the guest"
    out, _ = _validate([_e("2026-08-02T09:14", "guest"),
                        _e("2026-08-02T19:00", "guest"),
                        _e("2026-08-03T10:07", "co")],
                       flags=[{"team": "CO", "flag": CHECK,
                               "evidence": "the model spotted it"}])
    hits = [f for f in out["flags"] if f["flag"] == CHECK]
    assert len(hits) == 1, hits


def test_no_events_means_no_added_flags():
    out, _ = _validate([])
    assert out["flags"] == [], out["flags"]
