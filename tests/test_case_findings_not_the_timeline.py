"""§1 case findings: chronological, but not a second copy of the events
timeline and not a place clock times appear on the card.

The card renders a finding's `text` and `ref` only. So a timestamp written
INSIDE the text puts the clock on screen through the back door, and a finding
that reproduces a timeline summary puts the same event on screen twice.

Every test drives `validate()` or `_case_findings`; none asserts source text.
"""
from server.services.rca_v4_validate import (
    validate,
    _case_findings,
    _split_leading_time,
)


def _wwr(**kw):
    return {"what_went_wrong": {"guest_issues": [], **kw}}


def _texts(rows):
    return [r["text"] for r in rows]


# The real events timeline these findings must not restate.
EVENTS = [
    {"time": "22 Jul 15:22 IST", "actor": "system",
     "summary": "Automated email sent to guest confirming booking is under "
                "process and tickets will be delivered within two hours."},
    {"time": "22 Jul 15:28 IST", "actor": "system",
     "summary": "Automated Selenium run attempted ticket retrieval from "
                "vendor site but returned no ticket URLs."},
    {"time": "22 Jul 15:50 IST", "actor": "system",
     "summary": "Booking confirmed email sent to guest with tickets for "
                "22 July 2026, valid until 22 July 2027."},
]


# ── the clock comes out of the text, and stays in the ordering ─────────────

def test_a_leading_clock_time_is_taken_out_of_the_text():
    text, time = _split_leading_time(
        "22 Jul 15:28 IST — 'Tickets were never issued to the guest'")
    assert text == "Tickets were never issued to the guest"
    assert time == "22 Jul 15:28"


def test_an_iso_stamp_and_a_source_marker_come_off_too():
    text, _ = _split_leading_time(
        "[timeline] 2026-07-22 15:28 — Fulfilment returned no ticket URLs")
    assert text == "Fulfilment returned no ticket URLs"


def test_a_row_that_is_only_a_timestamp_is_left_alone():
    # Stripping would leave nothing; a cosmetic rule must never empty a row.
    text, time = _split_leading_time("22 Jul 15:28 IST")
    assert text == "22 Jul 15:28 IST"
    assert time == ""


def test_a_timestamp_with_a_separator_and_nothing_after_is_left_alone():
    # This one MATCHES the prefix pattern and would strip to "", which is the
    # case the guard exists for — the one above exits before ever reaching it.
    text, time = _split_leading_time("22 Jul 15:28 — ")
    assert text == "22 Jul 15:28 —"
    assert time == ""


def test_a_finding_that_is_only_a_timestamp_is_not_deleted_from_the_card():
    # Emptying the text would make the row vanish silently on the way out.
    rows = _case_findings([{"text": "22 Jul 15:28 — "}], [], [], events=[])
    assert len(rows) == 1
    assert rows[0]["text"].strip() != ""


def test_text_without_a_clock_is_untouched():
    text, time = _split_leading_time("Tickets were sent to the wrong email")
    assert text == "Tickets were sent to the wrong email"
    assert time == ""


def test_the_card_text_no_longer_carries_the_clock():
    rows = _case_findings(
        [{"text": "22 Jul 15:50 IST — Guest was refunded out of policy"}],
        [], [], events=[])
    assert _texts(rows) == ["Guest was refunded out of policy"]


def test_the_stripped_time_still_orders_the_section():
    # THE POINT THE CLOCK IS KEPT FOR. Written out of order, with the time only
    # ever present inside the prose: the section must still read chronologically.
    rows = _case_findings([
        {"text": "23 Jul 11:29 — Out-of-policy refund approved"},
        {"text": "22 Jul 15:22 — Guest was told tickets would take two hours"},
    ], [], [], events=[])
    assert _texts(rows) == [
        "Guest was told tickets would take two hours",
        "Out-of-policy refund approved",
    ]


def test_an_explicit_time_field_is_not_overwritten_by_the_prose():
    rows = _case_findings(
        [{"text": "23 Jul 11:29 — Refund approved", "time": "22 Jul 09:00"}],
        [], [], events=[])
    assert rows[0]["time"] == "22 Jul 09:00"


# ── a timeline row is not a case finding ───────────────────────────────────

def test_a_finding_that_reproduces_a_timeline_event_is_dropped():
    rows = _case_findings([
        {"text": "22 Jul 15:28 IST — Automated Selenium run attempted ticket "
                 "retrieval from vendor site but returned no ticket URLs"},
    ], [], [], events=EVENTS)
    assert rows == []


def test_a_real_finding_survives_beside_the_timeline():
    rows = _case_findings([
        {"text": "Automated Selenium run attempted ticket retrieval from "
                 "vendor site but returned no ticket URLs"},
        {"text": "The guest was never told before paying that delivery "
                 "would take two hours"},
    ], [], [], events=EVENTS)
    assert _texts(rows) == [
        "The guest was never told before paying that delivery would take two hours"]


def test_evidence_for_a_guest_claim_is_kept_even_if_it_restates_the_timeline():
    # §1's second job is settling what the guest said; the timeline cannot do
    # that job, so a claim-backing row is never dropped as a repeat.
    issues = [{"claim": "They said tickets would arrive instantly",
               "evidence": [{"text": "Automated email sent to guest confirming "
                                     "booking is under process and tickets "
                                     "will be delivered within two hours",
                             "backs_claim": 0}]}]
    rows = _case_findings([], issues, [], events=EVENTS)
    assert len(rows) == 1
    assert rows[0]["backs_claim"] == 0


def test_no_events_means_nothing_is_dropped_as_a_restatement():
    # A missing timeline must not read as "everything is a duplicate".
    rows = _case_findings([
        {"text": "Automated Selenium run attempted ticket retrieval from "
                 "vendor site but returned no ticket URLs"},
    ], [], [], events=[])
    assert len(rows) == 1


# ── the drops and rewrites are announced, never silent ─────────────────────

def test_a_dropped_restatement_is_said_out_loud():
    notes = []
    _case_findings([
        {"text": "Automated Selenium run attempted ticket retrieval from "
                 "vendor site but returned no ticket URLs"},
    ], [], notes, events=EVENTS)
    assert any("timeline" in n and "dropped" in n for n in notes), notes


def test_moving_a_clock_out_of_the_text_is_said_out_loud():
    notes = []
    _case_findings([{"text": "22 Jul 15:50 — Guest was refunded"}],
                   [], notes, events=[])
    assert any("clock time" in n for n in notes), notes


# ── it is wired: validate() hands the real timeline through ────────────────

def test_validate_passes_the_events_timeline_into_the_section():
    out, _ = validate(_wwr(case_findings=[
        {"text": "22 Jul 15:28 IST — Automated Selenium run attempted ticket "
                 "retrieval from vendor site but returned no ticket URLs"},
        {"text": "Guest was never warned about the two-hour delay at checkout"},
    ]), events=EVENTS)
    rows = out["what_went_wrong"]["case_findings"]
    assert _texts(rows) == [
        "Guest was never warned about the two-hour delay at checkout"]
