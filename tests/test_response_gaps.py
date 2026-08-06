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

from server.response_gaps import response_gaps, gap_flags, SLOW_HOURS


def _e(t, actor):
    return {"time_sort": t, "actor": actor}


# ── the three findings ─────────────────────────────────────────────────────

def test_a_long_single_silence_is_a_delayed_response():
    got = response_gaps([_e("2026-08-02T09:14", "guest"),
                         _e("2026-08-03T10:07", "co")])
    assert len(got) == 1, got
    assert got[0]["check"] == "Delayed response to guest"
    assert got[0]["hours"] == pytest.approx(24.9, abs=0.2)


def test_a_guest_who_wrote_twice_is_flagged_without_a_clock():
    """They were plainly waiting, and said so by writing again. No duration
    test is needed or wanted — a second message an hour later is still a guest
    who had to chase."""
    got = response_gaps([_e("2026-08-02T09:14", "guest"),
                         _e("2026-08-02T10:14", "guest"),
                         _e("2026-08-02T10:30", "co")])
    assert got and got[0]["check"] == "2+ non-autoresolved queries from the guest"
    assert got[0]["chases"] == 2, got


def test_a_silence_that_never_ended_is_the_worst_case_and_its_own_check():
    """No reply from us anywhere on the timeline. Different from a slow reply,
    and the reader acts on it differently."""
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


# ── and what is NOT a finding ──────────────────────────────────────────────

def test_a_prompt_reply_raises_nothing():
    """Same-day handling is normal. Flagging it makes the section noise people
    skim past, and a section people skim past has stopped working."""
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
                         _e("2026-08-03T10:07", "co")])
    assert got and got[0]["check"] == "Delayed response to guest", got
    assert got[0]["hours"] > SLOW_HOURS


def test_the_supply_partner_replying_is_not_us_replying():
    """The SP being slow is a different finding and routes to a different
    team. It does not close a silence on our side."""
    got = response_gaps([_e("2026-08-02T09:14", "guest"),
                         _e("2026-08-02T12:00", "sp"),
                         _e("2026-08-03T10:07", "co")])
    assert got, "an SP message closed the guest's wait"


# ── two silences are two findings ──────────────────────────────────────────

def test_two_separate_silences_are_two_findings():
    """Merging them into one summary hides the second, and each is a separate
    thing that went wrong."""
    got = response_gaps([
        _e("2026-08-01T09:00", "guest"), _e("2026-08-02T09:00", "co"),
        _e("2026-08-03T09:00", "guest"), _e("2026-08-04T09:00", "co")])
    assert len(got) == 2, got


def test_events_are_read_in_time_order_not_list_order():
    """The list arrives sorted, but a hand-added row can land anywhere."""
    got = response_gaps([_e("2026-08-03T10:07", "co"),
                         _e("2026-08-02T09:14", "guest")])
    assert got and got[0]["check"] == "Delayed response to guest"


# ── times we cannot read ───────────────────────────────────────────────────

def test_an_undated_event_is_skipped_and_counted_never_guessed_at():
    """A duration computed from a time we invented is a number a reader cannot
    check, and they would act on it."""
    got = response_gaps([_e("2026-08-02T09:14", "guest"),
                         {"actor": "co", "time_sort": ""},
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
    fl = gap_flags([_e("2026-08-02T09:14", "guest"), _e("2026-08-03T10:07", "co")])
    assert fl and all(f["team"] == "CO" for f in fl), fl
    assert fl[0]["evidence"].strip(), fl


def test_the_flag_names_the_check_from_the_checklist_vocabulary():
    """A flag whose wording is not in CE_ERROR_CHECKS routes nowhere and
    aggregates against nothing."""
    from server.checklist import CE_ERROR_CHECKS
    fl = gap_flags([_e("2026-08-02T09:14", "guest"), _e("2026-08-03T10:07", "co")])
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
                        _e("2026-08-03T10:07", "co")])
    assert any(f["flag"] == "Delayed response to guest" for f in out["flags"]), \
        out["flags"]


def test_a_measured_flag_is_announced_on_the_trail():
    """It is on the card for a different reason from the rest — measured, not
    read out of the model's answer — and a reader comparing two runs deserves
    to know which."""
    _, notes = _validate([_e("2026-08-02T09:14", "guest"),
                          _e("2026-08-03T10:07", "co")])
    assert any("response-gap" in n for n in notes), notes


def test_it_does_not_duplicate_a_flag_the_model_already_raised():
    out, _ = _validate([_e("2026-08-02T09:14", "guest"),
                        _e("2026-08-03T10:07", "co")],
                       flags=[{"team": "CO", "flag": "Delayed response to guest",
                               "evidence": "the model spotted it"}])
    hits = [f for f in out["flags"] if f["flag"] == "Delayed response to guest"]
    assert len(hits) == 1, hits


def test_no_events_means_no_added_flags():
    out, _ = _validate([])
    assert out["flags"] == [], out["flags"]
