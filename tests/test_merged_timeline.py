"""One timeline, in the facts column, in chronological order.

The card carried TWO. "Booking timeline" in the facts column, built from the
model's booking_logs, and "Events timeline" in the RCA column, built from the
real Zendesk events. Two lists of the same story, in two places, each missing
what the other had: the events had the actor, the channel and the ticket link
and could not be corrected; the logs had the readable detail line and were
editable but knew nothing about where anything came from. A reader had to hold
both in their head and neither was complete.

They merge into the facts column, under the heading that column already had.

WHAT THIS FILE PINS:
  * one section, not two — the RCA column has no timeline at all;
  * sorted by time, with NOTHING pinned. The review used to be forced last by
    a bookend rule; it now takes its place by its own timestamp, which matters
    since the publish date started coming from the payload rather than from
    the moment Slack relayed it;
  * the event's provenance survives the move — channel, actor, ticket link;
  * booking_logs rows stay editable and events do not, because one is the
    model's account and the other is what Zendesk recorded.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _render(page, events, logs):
    """Put events and booking_logs on the selected review and render."""
    return page.evaluate("""([evs, logs]) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keepE = r.events, keepR = r.rca;
      r.events = evs;
      r.rca = Object.assign({}, r.rca || {});
      r.rca.v3 = Object.assign({}, (r.rca && r.rca.v3) || {}, {booking_logs: logs});
      renderReviewCol();
      const sec = document.querySelector('#rca-booking-logs-section');
      const out = {
        exists: !!sec,
        rows: sec ? [...sec.querySelectorAll('.tl-row')].map(el => ({
          time: (el.querySelector('.tl-time') || {}).textContent || '',
          what: (el.querySelector('.tl-what') || {}).textContent || '',
          detail: (el.querySelector('.tl-detail-line') || {}).textContent || '',
          thread: (el.querySelector('.tl-thread') || {}).textContent || '',
          tid: (el.querySelector('.tl-tid') || {}).textContent || '',
          editable: !!el.querySelector('[data-log-field="what"]'),
          deletable: !!el.querySelector('[data-log-del]'),
        })) : [],
        rcaHasTimeline: !!document.querySelector('#rca-events-timeline-section'),
      };
      r.events = keepE; r.rca = keepR;
      renderReviewCol();
      return out; }""", [events, logs])


EV = [
  {"time": "2026-07-21 15:28", "time_sort": "2026-07-21T15:28:00",
   "thread": "api", "actor": "system", "label": "Booking details posted",
   "summary": "2 Adults; 03 Aug 08:30; pickup Wielopole 2",
   "ticket_id": "33978941", "is_internal": False},
  {"time": "2026-08-05 09:00", "time_sort": "2026-08-05T09:00:00",
   "thread": "review", "actor": "review", "label": "Review posted",
   "summary": "1-star review", "ticket_id": "", "is_internal": False},
  {"time": "2026-08-01 12:03", "time_sort": "2026-08-01T12:03:00",
   "thread": "email", "actor": "co", "label": "CE response",
   "summary": "Apology sent", "ticket_id": "33978941", "is_internal": False},
]


def test_there_is_one_timeline_and_it_is_in_the_facts_column(page):
    got = _render(page, EV, [])
    assert got["exists"], "the merged Booking timeline did not render"
    assert got["rcaHasTimeline"] is False, (
        "the RCA column still renders its own timeline — the card has two "
        "lists of one story again")


def test_rows_are_in_time_order(page):
    got = _render(page, EV, [])
    times = [r["time"] for r in got["rows"] if r["time"] and r["time"] != "—"]
    assert times == sorted(times), times


def test_the_review_is_not_pinned_last(page):
    """It used to be injected as a mandatory final bookend. A review published
    before a later CE reply then read as having come after it."""
    late_ce = [dict(EV[2], time="2026-08-09 12:03",
                    time_sort="2026-08-09T12:03:00")]
    got = _render(page, [EV[1]] + late_ce, [])
    order = [r["what"] for r in got["rows"] if r["what"]]
    assert order.index("Review posted") < order.index("CE response"), order


def test_an_events_provenance_survives_the_move(page):
    """The channel pill, the actor's label and the ticket link were the whole
    reason the RCA-column list existed. Losing them in the merge would trade
    one incomplete list for another."""
    got = _render(page, [EV[0]], [])
    row = next(r for r in got["rows"] if r["what"] == "Booking details posted")
    assert row["thread"] == "api", row
    assert "33978941" in row["tid"], row
    assert "Wielopole" in row["detail"], row


def test_booking_log_rows_stay_editable_and_events_do_not(page):
    """One is the model's account and a person may need to correct it; the
    other is what Zendesk recorded. Only the editable rows carry delete."""
    got = _render(page, [EV[0]],
                  [{"time": "2026-07-20 09:00", "what": "Booking created",
                    "detail": "2 Adults"}])
    ev = next(r for r in got["rows"] if r["what"] == "Booking details posted")
    log = next(r for r in got["rows"] if r["what"] == "Booking created")
    assert log["editable"] is True and log["deletable"] is True, log
    assert ev["editable"] is False and ev["deletable"] is False, ev


def test_events_and_logs_interleave_by_time(page):
    """Not events-then-logs. The whole point is one chronology."""
    got = _render(page, [EV[0], EV[1]],
                  [{"time": "2026-08-02 10:00", "what": "Payment taken",
                    "detail": "PLN 606"}])
    order = [r["what"] for r in got["rows"] if r["what"]]
    assert order == ["Booking details posted", "Payment taken", "Review posted"], order


def test_an_undated_row_sinks_rather_than_leading(page):
    """An undated row at the top reads as the first thing that happened, which
    is a claim nobody made."""
    got = _render(page, [EV[0]],
                  [{"time": "", "what": "Guest narrated something",
                    "detail": "no clock"}])
    order = [r["what"] for r in got["rows"] if r["what"]]
    assert order[-1] == "Guest narrated something", order


def test_the_section_renders_with_nothing_in_it(page):
    """It used to collapse to '' on an empty list, taking "+ Add event" with
    it — so the one case where a human most needs to write the timeline by
    hand was the one case with no way to do it."""
    got = _render(page, [], [])
    assert got["exists"], "the section vanished when there was nothing in it"


def test_internal_notes_are_counted_when_hidden(page):
    """They are on the card because they change the escalation. Hiding them
    with no count would make a broken filter and a clean ticket identical."""
    ev = EV + [{"time": "2026-08-02 09:14", "time_sort": "2026-08-02T09:14:00",
                "thread": "email", "actor": "system", "label": "Reschedule blocked",
                "summary": "pending SP", "ticket_id": "33978941",
                "is_internal": True, "internal_reason": "internal"}]
    got = page.evaluate("""(evs) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.events, keepShow = state.tlShowInternal;
      r.events = evs; state.tlShowInternal = false;
      renderReviewCol();
      const note = document.querySelector('#rca-booking-logs-section .tl-hidden-note');
      const txt = note ? note.textContent : '';
      r.events = keep; state.tlShowInternal = keepShow;
      renderReviewCol();
      return txt; }""", ev)
    assert "1 internal note" in got and "hidden" in got, got


# ── the two time shapes actually sort against each other ───────────────────
#
# The bug this section exists for: events carry `time_sort` (ISO), booking_logs
# carry whatever the model wrote — normally "21 Jul 15:28", no year. Compared
# as strings, an August log row sorted above a July event.

def _order(page, events, logs):
    return [r["what"] for r in _render(page, events, logs)["rows"] if r["what"]]


def test_a_model_written_day_month_time_sorts_against_an_iso_event(page):
    """The real shape. "21 Jul 15:28" has no year and no ISO separator, and
    lexically it sorts nowhere near an event's timestamp."""
    order = _order(page,
        [{"time": "2026-07-20 09:00", "time_sort": "2026-07-20T09:00:00",
          "thread": "api", "actor": "system", "label": "Booking created",
          "summary": "", "ticket_id": "", "is_internal": False},
         {"time": "2026-08-05 09:00", "time_sort": "2026-08-05T09:00:00",
          "thread": "review", "actor": "review", "label": "Review posted",
          "summary": "", "ticket_id": "", "is_internal": False}],
        [{"time": "21 Jul 15:28", "what": "Tickets sent", "detail": "x"}])
    assert order == ["Booking created", "Tickets sent", "Review posted"], order


def test_an_august_log_row_does_not_jump_above_a_july_event(page):
    """The exact inversion the string comparison produced."""
    order = _order(page,
        [{"time": "2026-07-21 15:28", "time_sort": "2026-07-21T15:28:00",
          "thread": "api", "actor": "system", "label": "Booking details posted",
          "summary": "", "ticket_id": "", "is_internal": False}],
        [{"time": "02 Aug 10:00", "what": "Payment taken", "detail": "x"}])
    assert order == ["Booking details posted", "Payment taken"], order


def test_two_log_rows_sort_against_each_other(page):
    order = _order(page, [],
        [{"time": "31 Jul 12:01", "what": "Reminder sent", "detail": "x"},
         {"time": "21 Jul 15:29", "what": "Confirmation sent", "detail": "x"}])
    assert order == ["Confirmation sent", "Reminder sent"], order


def test_a_time_with_no_clock_still_sorts_by_its_day(page):
    order = _order(page, [],
        [{"time": "05 Aug", "what": "Later", "detail": "x"},
         {"time": "21 Jul", "what": "Earlier", "detail": "x"}])
    assert order == ["Earlier", "Later"], order


def test_an_unparseable_time_sinks_instead_of_leading(page):
    """It must not be treated as time zero. A row we cannot place is not the
    first thing that happened."""
    order = _order(page,
        [{"time": "2026-07-21 15:28", "time_sort": "2026-07-21T15:28:00",
          "thread": "api", "actor": "system", "label": "Real event",
          "summary": "", "ticket_id": "", "is_internal": False}],
        [{"time": "sometime last week", "what": "Vague", "detail": "x"}])
    assert order == ["Real event", "Vague"], order
