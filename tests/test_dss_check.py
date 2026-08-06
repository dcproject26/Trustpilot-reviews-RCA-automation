"""Was the DSS path followed, on a case where the guest asked us first?

THE TWO THINGS THAT MUST NOT LOOK ALIKE. "The DSS path was not followed" on a
booking where the guest never wrote in is blame for a step nobody was owed.
"Not applicable" on a booking where they wrote in three times and were
mishandled hides the finding. The timeline decides which case this is, in
code, because a stored draft cannot be re-asked.
"""
from server.dss_check import (APPLIES, NO_PRIOR_CONTACT, NO_TIMELINE,
                              guest_contacted_before, gate_dss_followed)
from server.services.rca_v4_validate import validate

GUEST = [{"actor": "guest", "time_sort": "2026-07-01T10:00:00"}]
POSTED = "2026-07-05T00:00:00"


# ── the precondition ───────────────────────────────────────────────────────

def test_a_guest_who_wrote_in_first_opens_the_check():
    state, why = guest_contacted_before(GUEST, POSTED)
    assert state == APPLIES, (state, why)
    assert "before the review" in why


def test_contact_only_after_the_review_does_not_open_it():
    """They wrote in because of the review, not before it. There was no path
    to follow when they wrote it."""
    late = [{"actor": "guest", "time_sort": "2026-07-09T10:00:00"}]
    state, why = guest_contacted_before(late, POSTED)
    assert state == NO_PRIOR_CONTACT, (state, why)


def test_only_the_guest_counts_as_the_guest_reaching_out():
    """An internal note before the review is us talking to ourselves, and
    reading one as contact opens a verdict on a case that never had one."""
    for actor in ("agent", "system", "internal"):
        rows = [{"actor": actor, "time_sort": "2026-07-01T10:00:00"}]
        assert guest_contacted_before(rows, POSTED)[0] == NO_PRIOR_CONTACT, actor


def test_no_timeline_is_not_the_same_as_a_quiet_guest():
    """CLAUDE.md §1. "We have no case history" and "they never contacted us"
    are different facts; collapsing them makes an unfetched ticket look like a
    guest who never wrote in."""
    state, why = guest_contacted_before([], POSTED)
    assert state == NO_TIMELINE, (state, why)
    assert "no support timeline was loaded" in why
    assert guest_contacted_before(GUEST, POSTED)[0] != state


def test_a_missing_review_timestamp_keeps_the_finding_and_says_so():
    """Refusing to look because one field is missing loses a real finding."""
    state, why = guest_contacted_before(GUEST, None)
    assert state == APPLIES, (state, why)
    assert "assumed" in why, "a judgement must announce itself"


# ── the gate ───────────────────────────────────────────────────────────────

def test_a_verdict_with_no_standing_is_dropped_and_reported():
    quiet = [{"actor": "agent", "time_sort": "2026-07-01T10:00:00"}]
    verdict, note = gate_dss_followed("not_followed", quiet, POSTED)
    assert verdict is None, verdict
    assert "not applicable" in note, note


def test_a_verdict_with_standing_is_left_alone_and_says_nothing():
    """Ran and found nothing to change — distinguishable from not running,
    because the demotion path returns a note and this does not."""
    verdict, note = gate_dss_followed("not_followed", GUEST, POSTED)
    assert verdict == "not_followed"
    assert note is None


def test_an_unanswered_check_that_HAD_standing_is_reported():
    """The expensive silence: the sheet governed this contact and whether we
    took its path went unrecorded."""
    verdict, note = gate_dss_followed(None, GUEST, POSTED)
    assert verdict == "unestablished", verdict
    assert "was not answered" in note, note


def test_unestablished_is_a_real_answer_not_a_miss():
    verdict, note = gate_dss_followed("unestablished", GUEST, POSTED)
    assert verdict == "unestablished"
    assert note is None, "a real answer must not be reported as a coercion"


def test_silence_where_the_check_does_not_apply_reports_nothing():
    quiet = [{"actor": "agent", "time_sort": "2026-07-01T10:00:00"}]
    assert gate_dss_followed(None, quiet, POSTED) == (None, None)


# ── the feeder, driven ─────────────────────────────────────────────────────

def test_validate_actually_runs_the_dss_gate():
    """A gate wired into no path looks exactly like one that works."""
    out, notes = validate({"dss": {"prescribes": "Refund in full",
                                   "followed": "not_followed"}},
                          events=[{"actor": "agent",
                                   "time_sort": "2026-07-01T10:00:00"}],
                          review_at=POSTED)
    assert out["dss"]["followed"] is None, out["dss"]
    assert any("not applicable" in n for n in notes), notes


def test_validate_keeps_a_verdict_the_timeline_supports():
    out, _ = validate({"dss": {"prescribes": "x", "followed": "not_followed"}},
                      events=GUEST, review_at=POSTED)
    assert out["dss"]["followed"] == "not_followed", out["dss"]


def test_validate_reports_a_case_that_had_standing_and_was_unanswered():
    out, notes = validate({"dss": {"prescribes": "x"}},
                          events=GUEST, review_at=POSTED)
    assert out["dss"]["followed"] == "unestablished", out["dss"]
    assert any("was not answered" in n for n in notes), notes


def test_validate_without_a_timeline_does_not_raise():
    for ev in (None, [], [{}]):
        out, _ = validate({"dss": {"prescribes": "x"}}, events=ev,
                          review_at=None)
        assert "followed" in out["dss"], ev
