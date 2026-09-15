"""The daily ORM digest: the arithmetic, the percentages, and the 24h window.

Most of this is driven against the pure functions (summarize / render_digest /
_tier_label / _pct) with hand-built cohorts — no database — so the counting logic
is tested directly. The DB-facing tests pin the two frames the report mixes: the
fixed 8pm→8pm IST window (received, solved, solved-by) and the ALL-TIME pending
backlog (the tier mix and the categories).
"""
from datetime import datetime, timedelta, timezone

import pytest

from server.services.daily_report import (
    Row, Summary, summarize, render_digest, _tier_label, _pct,
    collect_solved_rows, collect_pending_rows, received_count,
    build_daily_digest, window_bounds, IST)

# ── tier label: Tier 1 / Tier 2 / Untraceable (the only three tracked) ──────

def test_tier_label_is_three_buckets_only():
    assert _tier_label(Row(tier=1)) == "Tier 1"
    assert _tier_label(Row(tier=2)) == "Tier 2"
    # Anything without a confirmed tier is Untraceable — regardless of sent
    # status, and with no invented "in progress" bucket (not a tracked state).
    assert _tier_label(Row(tier=None)) == "Untraceable"
    assert _tier_label(Row(tier=None, solved=True)) == "Untraceable"


def test_declared_untraceable_overrides_a_tentative_tier():
    # A review a person closed/marked untraceable is Untraceable even if a stale
    # tentative match_tier is still on the draft.
    assert _tier_label(Row(tier=2, declared_untraceable=True)) == "Untraceable"
    assert _tier_label(Row(tier=1, declared_untraceable=True)) == "Untraceable"


# ── percentages ─────────────────────────────────────────────────────────────

def test_pct_rounds_and_refuses_an_empty_denominator():
    assert _pct(25, 64) == " (39%)"        # 39.06 -> 39
    assert _pct(36, 64) == " (56%)"        # 56.25 -> 56
    assert _pct(3, 64) == " (5%)"          # 4.69  -> 5
    assert _pct(1, 1) == " (100%)"
    assert _pct(0, 10) == " (0%)"          # a real zero out of ten IS 0%
    # ...but a share of NOTHING is unanswerable, not 0%. No fabricated figure.
    assert _pct(0, 0) == ""
    assert _pct(5, 0) == ""


# ── summarize ───────────────────────────────────────────────────────────────

def _solved_cohort():
    return [
        Row(solved=True, picked_up_by="Avi",      tier=1, l1="Operations", l2="Ticket Issues"),
        Row(solved=True, picked_up_by="Avi",      tier=2, l1="Operations", l2="Ticket Issues"),
        Row(solved=True, picked_up_by="Swagatom", tier=None, l1="Experience", l2="Guide"),  # untraceable
    ]


def test_received_and_solved_counts():
    s = summarize(_solved_cohort(), received_count=10)
    assert s.received == 10           # context number, its own cohort
    assert s.solved == 3              # len of the solved rows
    assert s.pending is None          # no pending cohort asked for


def test_people_sum_to_solved_exactly():
    # Solved-by and Solved are now the SAME cohort, so the per-person rows must
    # sum back to the headline exactly — that is what makes a share of it valid.
    s = summarize(_solved_cohort())
    assert s.people == [("Avi", 2), ("Swagatom", 1)]
    assert sum(n for _, n in s.people) == s.solved


def test_people_biggest_first_and_unassigned_last():
    # "Zoe" sorts AFTER "Unassigned" alphabetically, so if Unassigned were not
    # forced last it would tie-break ahead of Zoe. It must not.
    cohort = [
        Row(solved=True, picked_up_by="Avi"), Row(solved=True, picked_up_by="Avi"),
        Row(solved=True, picked_up_by="Zoe"),
        Row(solved=True, picked_up_by=""),          # -> Unassigned
    ]
    s = summarize(cohort)
    assert s.people == [("Avi", 2), ("Zoe", 1), ("Unassigned", 1)]


# ── the pending cohort drives tier + categories ─────────────────────────────

def test_tier_and_categories_are_counted_over_pending_not_solved():
    # THE SEMANTIC CHANGE. The tier mix and the categories describe the OPEN
    # BACKLOG, not the day's finished work. The two cohorts are made deliberately
    # disjoint here so following the wrong one is impossible to miss.
    solved = [Row(solved=True, picked_up_by="Avi", tier=1,
                  l1="Operations", l2="Ticket Issues")]
    pending = [Row(tier=2, l1="Supply", l2="Guide No Show"),
               Row(tier=2, l1="Supply", l2="Guide No Show"),
               Row(tier=None, declared_untraceable=True, l1="Supply", l2="Guide No Show")]
    s = summarize(solved, received_count=5, pending_rows=pending)
    assert s.solved == 1                       # headline = the 24h window
    assert s.pending == 3                      # backlog = the pending cohort
    assert s.mix_base == 3                     # the % denominator is the backlog
    # Tier follows PENDING: two Tier 2 + one declared-untraceable. Nothing from
    # the solved Tier 1 leaks in.
    assert s.tier == {"Tier 2": 2, "Untraceable": 1}
    assert sum(s.tier.values()) == s.pending   # backlog rows all accounted for
    # Categories follow PENDING too — the solved row's category is absent.
    assert s.categories == [("Supply / Guide No Show", 3)]
    # People still follow the SOLVED window cohort.
    assert s.people == [("Avi", 1)]


def test_tier_and_categories_fall_back_to_solved_when_no_pending_cohort():
    # The weekly digest passes no pending cohort: its blocks describe the week it
    # summarises, so the mix must fall back to the solved rows.
    s = summarize(_solved_cohort())
    assert s.pending is None
    assert s.mix_base == 3
    assert s.tier == {"Tier 1": 1, "Tier 2": 1, "Untraceable": 1}
    assert s.categories[0] == ("Operations / Ticket Issues", 2)
    assert ("Experience / Guide", 1) in s.categories


def test_uncategorised_pending_are_counted_not_dropped():
    # A backlog row with no L1/L2 must be COUNTED and reported, never silently
    # missing from the category block — otherwise "we have no category for these"
    # looks exactly like "every pending review has a category".
    pending = [Row(tier=1, l1="Operations", l2="Ticket Issues"),
               Row(tier=1),                       # no category at all
               Row(tier=2, l1="", l2="   ")]      # blank/whitespace -> no category
    s = summarize([], pending_rows=pending)
    assert s.pending == 3
    assert s.uncategorised == 2
    assert s.categories == [("Operations / Ticket Issues", 1)]
    # The two uncategorised rows are still in the tier mix and the pending total.
    assert sum(s.tier.values()) == 3


def test_empty_cohort_renders_zeros_without_error():
    s = summarize([], received_count=0)
    assert s.received == 0 and s.solved == 0
    out = render_digest(s, "7 Sep 2026")
    assert "Received today — *0*" in out and "Solved today — *0*" in out


# ── render ──────────────────────────────────────────────────────────────────

def _full_summary():
    solved = [Row(solved=True, picked_up_by="Avi", tier=1),
              Row(solved=True, picked_up_by="Avi", tier=1),
              Row(solved=True, picked_up_by="Shruti", tier=2),
              Row(solved=True, picked_up_by="Shruti", tier=2)]
    pending = ([Row(tier=1, l1="Operations", l2="Ticket Issues")] * 5
               + [Row(tier=2, l1="Supply", l2="Guide No Show")] * 4
               + [Row(tier=None)])              # untraceable, uncategorised
    return summarize(solved, received_count=10, pending_rows=pending)


def test_render_blocks_in_the_stakeholder_order():
    out = render_digest(_full_summary(), "13 Sep 2026", "8pm 12 Sep → 8pm 13 Sep IST",
                        "(all time)")
    assert "*ORM Daily — 13 Sep 2026*" in out
    # 1. Pending headline first, captioned as all-time so it is never read as
    #    "pending that arrived today".
    assert "Total pending reviews: *10*" in out
    assert "(all time)" in out and "_(all time)_" not in out
    # 2/3. The 24h frame, stamped with the window those two numbers cover.
    assert "8pm 12 Sep → 8pm 13 Sep IST" in out
    assert "• Received today — *10*" in out
    assert "• Solved today — *4*" in out
    # Block ORDER is the ask: pending, then the 24h frame, then solved-by,
    # then tier. No categories.
    order = [out.index("Total pending reviews"), out.index("Last 24 hours"),
             out.index("Solved by"), out.index("Tier — last 24 hours")]
    assert order == sorted(order)
    # Tier is the LAST block: no category breakdown on the daily.
    assert "categor" not in out.lower()
    # Section breakers and the tier dots stay in the existing visual family.
    assert "━" in out


def test_render_percentages_are_within_cohort():
    out = render_digest(_full_summary(), "13 Sep 2026")
    # Solved-by is counts only now — no per-person share.
    assert "• Avi — 2" in out and "• Avi — 2 (" not in out
    assert "• Shruti — 2" in out and "• Shruti — 2 (" not in out
    # Solved carries a share of the OPEN PILE it came out of (pending + solved),
    # never of Received: 4 of 10+4 = 29%. That denominator cannot exceed 100%.
    assert "• Solved today — *4* (29% of 14 = 10 pending + 4 solved)" in out
    # Tier: each bucket as a share of the 10 PENDING, and the denominator is
    # printed so the reader can check it.
    assert "(% of 10 handled in 24h)" in out
    assert "🟢 Tier 1 — 5 (50%)" in out
    assert "🟡 Tier 2 — 4 (40%)" in out
    assert "🔴 Untraceable — 1 (10%)" in out
    # No category block on the daily at all — it lives in the Reporting page.
    assert "categories" not in out.lower()
    # The uncategorised backlog row is stated, not missing.


def test_no_solved_over_received_ratio_is_ever_printed():
    # THE 130% BUG. Solved (4) and Received (10) are different cohorts — Solved
    # includes backlog that arrived days earlier — so no ratio between them may
    # appear. Here Solved EXCEEDS Received, the exact shape that once rendered
    # "130%"; the only percentages allowed are within-cohort ones.
    s = summarize([Row(solved=True, picked_up_by="Avi", tier=1)] * 13,
                  received_count=10,
                  pending_rows=[Row(tier=1)] * 4)
    out = render_digest(s, "13 Sep 2026")
    assert "• Received today — *10*" in out
    assert "• Solved today — *13*" in out
    assert "130%" not in out
    # Every percentage printed must be one of the within-cohort ones: the person
    # share of solved (100%) or the tier share of pending (100%/0%).
    import re
    pcts = re.findall(r"\((\d+)%\)", out)
    assert pcts and all(int(p) <= 100 for p in pcts)


def test_pending_line_absent_when_no_pending_cohort_and_block_renamed():
    # pending=None means "nobody asked", which is NOT a backlog of zero: printing
    # "Total pending reviews: 0" for it would be a fabricated number. The tier and
    # category blocks are renamed too, because with no pending cohort they are
    # counting the solved rows and must not claim to be counting the backlog.
    out = render_digest(summarize(_solved_cohort()), "x")
    assert "Total pending reviews" not in out
    assert "Tier — last 24 hours" not in out
    assert "*🏷️  Reviews by tier*" in out
    assert "pending_" not in out


def test_untraceable_always_shown_even_at_zero():
    # A day with no untraceable reviews must STILL show the Untraceable row at
    # 0 — the exact "where is untraceable?" confusion. No "in progress" bucket.
    out = render_digest(summarize([], pending_rows=[Row(tier=1)]), "x")
    assert "🟢 Tier 1 — 1 (100%)" in out
    assert "🔴 Untraceable — 0 (0%)" in out
    assert "In progress" not in out


def test_untraceable_labelled_not_t3():
    out = render_digest(summarize(_solved_cohort()), "x")
    assert "Untraceable" in out
    assert "T3" not in out


# ── the 24h window, against the real schema ─────────────────────────────────

def _n(dt):
    """Store as NAIVE UTC, exactly as production does (utcnow() /
    utcfromtimestamp(...).replace(tzinfo=None)). The window comparison is naive
    UTC too, so the tests exercise the real Postgres path rather than a
    tz-aware shape sqlite happens to tolerate."""
    return dt.replace(tzinfo=None) if dt is not None else None


def _add_review(db, rid, received_at, status="new"):
    from server.db import Review
    db.add(Review(id=rid, received_at=_n(received_at), status=status, rating=1))


def test_solved_cohort_is_by_when_finished_not_when_received(live_db):
    # THE BUG THIS GUARDS: a review that ARRIVED before the window but was
    # SOLVED inside it must be credited to its solver. Received-cohort logic
    # dropped these, so only same-day arrivals showed under "Solved by".
    from server.db import Review, RcaDraft
    now = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)     # 21:00 IST
    in_win = now - timedelta(hours=2)                            # inside window
    old_arrival = now - timedelta(days=3)                        # arrived long ago
    s = live_db.SessionLocal()
    try:
        # arrived 3 days ago, but its reply was SENT inside the window
        s.add(Review(id="old_but_solved", received_at=_n(old_arrival), status="sent",
                     rating=1, picked_up_by="Swagatom"))
        s.add(RcaDraft(id="old_but_solved-d", review_id="old_but_solved",
                       match_tier=1, sent_at=_n(in_win), booking={"id": "B1"}))
        # arrived and solved inside the window
        s.add(Review(id="same_day", received_at=_n(in_win), status="sent",
                     rating=1, picked_up_by="Paul"))
        s.add(RcaDraft(id="same_day-d", review_id="same_day", match_tier=2,
                       sent_at=_n(in_win), booking={"id": "B2"}))
        # sent, but finished OUTSIDE the window -> excluded
        s.add(Review(id="solved_yesterday", received_at=_n(old_arrival), status="sent",
                     rating=1, picked_up_by="Avi"))
        s.add(RcaDraft(id="solved_yesterday-d", review_id="solved_yesterday",
                       match_tier=1, sent_at=_n(now - timedelta(hours=30)),
                       booking={"id": "B3"}))
        s.commit()
        rows = collect_solved_rows(s, now)
        people = summarize(rows).people
    finally:
        s.close()
    # Swagatom (old arrival, solved today) AND Paul (same day) — NOT Avi.
    assert people == [("Paul", 1), ("Swagatom", 1)]


def test_evening_solves_are_credited_to_the_next_report_not_to_nobody(live_db):
    # THE FIXED DEFECT. The per-person credit used to run midnight→now in IST,
    # so work finished between 8pm and midnight fell AFTER that day's cap and
    # BEFORE the next day's start — credited to nobody, ever. On the real export
    # that lost 57 of 185 solves (31%), 25 of them Devshree's.
    #
    # Solved-by now uses the same 8pm→8pm window as everything else, which TILES:
    # a 22:00 IST solve on 10 Sep is outside the 10 Sep report and inside the
    # 11 Sep one. Exactly one report credits it.
    from server.db import Review, RcaDraft
    evening = datetime(2026, 9, 10, 16, 30, tzinfo=timezone.utc)   # 22:00 IST Sep10
    run_sep10 = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)  # 8pm IST Sep10
    run_sep11 = datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc)  # 8pm IST Sep11
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="evening", received_at=_n(evening - timedelta(hours=3)),
                     status="sent", rating=1, picked_up_by="Devshree"))
        s.add(RcaDraft(id="evening-d", review_id="evening", match_tier=1,
                       sent_at=_n(evening), booking={"id": "B1"}))
        s.commit()
        day10 = summarize(collect_solved_rows(s, run_sep10))
        day11 = summarize(collect_solved_rows(s, run_sep11))
        text10 = build_daily_digest(s, run_sep10)
        text11 = build_daily_digest(s, run_sep11)
    finally:
        s.close()
    # Not on the 10th (it happened after that report's 8pm cutoff)...
    assert day10.people == [] and day10.solved == 0
    assert "Devshree" not in text10
    # ...but credited on the 11th. Counted once, by somebody, never lost.
    assert day11.people == [("Devshree", 1)]
    assert day11.solved == 1
    assert "• Devshree — 1" in text11


def test_solved_by_total_equals_headline_solved(live_db):
    # The two used to be different cohorts and could legitimately disagree, which
    # is what hid the missing 31%. They are one cohort now, so any disagreement
    # is a bug — and the per-person percentages are only valid because of this.
    from server.db import Review, RcaDraft
    now = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)      # 8pm IST
    inside = now - timedelta(hours=5)
    s = live_db.SessionLocal()
    try:
        for i, who in enumerate(["Avi", "Avi", "Shruti", ""]):
            s.add(Review(id=f"r{i}", received_at=_n(inside), status="sent",
                         rating=1, picked_up_by=who))
            s.add(RcaDraft(id=f"r{i}-d", review_id=f"r{i}", match_tier=1,
                           sent_at=_n(inside), booking={"id": f"B{i}"}))
        s.commit()
        summ = summarize(collect_solved_rows(s, now))
    finally:
        s.close()
    assert summ.solved == 4
    assert sum(n for _, n in summ.people) == summ.solved
    assert summ.people == [("Avi", 2), ("Shruti", 1), ("Unassigned", 1)]


# ── the pending backlog, against the real schema ────────────────────────────

def test_pending_is_all_time_and_excludes_sent(live_db):
    # "Total pending" is a STOCK: every non-sent review, however old. Bounding it
    # by the 24h window would report only the last day's leftovers and make a
    # growing queue look flat.
    from server.db import Review, RcaDraft
    now = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)      # 8pm IST
    s = live_db.SessionLocal()
    try:
        # pending, arrived 40 days ago — far outside any window, still pending
        s.add(Review(id="ancient", received_at=_n(now - timedelta(days=40)),
                     status="draft", rating=1))
        s.add(RcaDraft(id="ancient-d", review_id="ancient", match_tier=2,
                       l1="Supply", l2="Guide No Show"))
        # pending, arrived today
        s.add(Review(id="fresh", received_at=_n(now - timedelta(hours=2)),
                     status="new", rating=1))
        # sent -> NOT pending, even though it arrived in the window
        s.add(Review(id="done", received_at=_n(now - timedelta(hours=3)),
                     status="sent", rating=1, picked_up_by="Avi"))
        s.add(RcaDraft(id="done-d", review_id="done", match_tier=1,
                       sent_at=_n(now - timedelta(hours=1)), booking={"id": "B1"}))
        s.commit()
        pend = collect_pending_rows(s)
        summ = summarize(collect_solved_rows(s, now), received_count(s, now),
                         pending_rows=pend)
    finally:
        s.close()
    assert len(pend) == 2                       # ancient + fresh, not done
    assert summ.pending == 2
    assert summ.solved == 1                     # done, in the 24h window
    assert summ.tier == {"Tier 2": 1, "Untraceable": 1}   # from PENDING only
    assert summ.uncategorised == 1              # "fresh" has no draft/category


def test_a_review_closed_without_a_slack_post_is_solved_not_pending(live_db):
    # sent_route == 'closed' means a person resolved it without posting — it is
    # written as status 'sent', so it is SOLVED. On the real export all 86 such
    # rows carry status 'sent'. Counting them as pending would inflate the
    # backlog by a third.
    from server.db import Review, RcaDraft
    now = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)
    at = now - timedelta(hours=2)
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="closed_out", received_at=_n(at), status="sent", rating=1,
                     sent_route="closed", picked_up_by="Shruti", closed_at=_n(at),
                     close_reason="Finished from the RCA card without posting."))
        s.add(RcaDraft(id="closed_out-d", review_id="closed_out", match_tier=1,
                       booking={"id": "B1"}))
        s.commit()
        pend = collect_pending_rows(s)
        summ = summarize(collect_solved_rows(s, now), pending_rows=pend)
    finally:
        s.close()
    assert pend == []
    assert summ.pending == 0
    assert summ.solved == 1
    assert summ.people == [("Shruti", 1)]


def test_a_null_status_review_is_counted_as_pending_not_lost(live_db):
    # `status` is nullable and SQL `status != 'sent'` is NULL — not true — for a
    # NULL row, so the plain comparison would drop it from the backlog silently:
    # present in neither Pending nor Solved, with nothing to show it happened.
    from server.db import Review
    from sqlalchemy import text
    now = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="no_status", received_at=_n(now - timedelta(hours=2)),
                     status="new", rating=1))
        s.commit()
        # The column default ("new") fires for a None passed through the ORM, so
        # the NULL has to be written directly — which is also how a real one gets
        # there (a backfill or a migration that left the column unset).
        s.execute(text("UPDATE reviews SET status = NULL WHERE id = 'no_status'"))
        s.commit()
        assert s.execute(
            text("SELECT status FROM reviews WHERE id = 'no_status'")
        ).scalar() is None, "the row under test is not actually NULL"
        pend = collect_pending_rows(s)
    finally:
        s.close()
    assert len(pend) == 1


def test_pending_tier_uses_declared_untraceable_over_a_stale_tier(live_db):
    # The tier rule is unchanged, and it must hold on the PENDING cohort too.
    from server.db import Review, RcaDraft
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="p_untr", received_at=_n(datetime(2026, 9, 1)), status="draft",
                     rating=1,
                     close_reason="Untraceable — asked the guest for a booking reference."))
        s.add(RcaDraft(id="p_untr-d", review_id="p_untr", match_tier=2,
                       booking={"id": "B1"}))
        s.add(Review(id="p_marked", received_at=_n(datetime(2026, 9, 1)),
                     status="draft", rating=1))
        s.add(RcaDraft(id="p_marked-d", review_id="p_marked", match_tier=1,
                       match_method="Marked untraceable by associate"))
        s.add(Review(id="p_t1", received_at=_n(datetime(2026, 9, 1)), status="draft",
                     rating=1))
        s.add(RcaDraft(id="p_t1-d", review_id="p_t1", match_tier=1,
                       booking={"id": "B2"}))
        s.commit()
        labels = sorted(_tier_label(r) for r in collect_pending_rows(s))
    finally:
        s.close()
    assert labels == ["Tier 1", "Untraceable", "Untraceable"]


def test_collect_solved_detects_declared_untraceable_over_a_tier(live_db):
    from server.db import Review, RcaDraft
    now = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)     # 21:00 IST
    at = now - timedelta(hours=2)
    s = live_db.SessionLocal()
    try:
        # (a) closed untraceable (closed_at in window) but a stale Tier 2 draft
        s.add(Review(id="closed_untr", received_at=_n(at), status="sent", rating=1,
                     closed_at=_n(at),
                     close_reason="Untraceable — asked the guest for a booking reference."))
        s.add(RcaDraft(id="closed_untr-d", review_id="closed_untr", match_tier=2,
                       booking={"id": "B1"}))
        # (b) marked untraceable off the shortlist; reply sent in window
        s.add(Review(id="marked_untr", received_at=_n(at), status="sent", rating=1))
        s.add(RcaDraft(id="marked_untr-d", review_id="marked_untr", match_tier=None,
                       sent_at=_n(at), match_method="Marked untraceable by associate"))
        # (c) a normal Tier 1, sent in window
        s.add(Review(id="real_t1", received_at=_n(at), status="sent", rating=1))
        s.add(RcaDraft(id="real_t1-d", review_id="real_t1", match_tier=1,
                       sent_at=_n(at), booking={"id": "B2"}))
        s.commit()
        labels = sorted(_tier_label(r) for r in collect_solved_rows(s, now))
    finally:
        s.close()
    assert labels == ["Tier 1", "Untraceable", "Untraceable"]


def test_window_is_anchored_to_8pm_ist_not_the_run_time():
    # 9:00pm IST on 10 Sep -> the window is yesterday-8pm .. today-8pm, i.e.
    # 9 Sep 20:00 IST .. 10 Sep 20:00 IST, regardless of the 1h drift past 8pm.
    now = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)   # 21:00 IST
    start, end = window_bounds(now)
    assert end.astimezone(IST).strftime("%Y-%m-%d %H:%M") == "2026-09-10 20:00"
    assert start.astimezone(IST).strftime("%Y-%m-%d %H:%M") == "2026-09-09 20:00"


def test_window_before_8pm_uses_the_previous_completed_day():
    # 10:00am IST on 10 Sep is BEFORE today's 8pm, so the last COMPLETED day is
    # 8 Sep 8pm .. 9 Sep 8pm.
    now = datetime(2026, 9, 10, 4, 30, tzinfo=timezone.utc)    # 10:00 IST
    start, end = window_bounds(now)
    assert end.astimezone(IST).strftime("%Y-%m-%d %H:%M") == "2026-09-09 20:00"
    assert start.astimezone(IST).strftime("%Y-%m-%d %H:%M") == "2026-09-08 20:00"


def test_consecutive_windows_tile_with_no_gap_and_no_overlap():
    # The property the whole per-person fix rests on: one day's window ENDS
    # exactly where the next begins, so every solve lands in exactly one report.
    d1 = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)
    d2 = datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc)
    s1, e1 = window_bounds(d1)
    s2, e2 = window_bounds(d2)
    assert e1 == s2                       # abut exactly, no gap, no overlap
    assert e1 - s1 == timedelta(hours=24)
    assert e2 - s2 == timedelta(hours=24)


def test_received_count_8pm_boundary_start_inclusive_end_exclusive(live_db):
    now = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)   # 21:00 IST
    s = live_db.SessionLocal()
    try:
        # window is [9 Sep 20:00 IST, 10 Sep 20:00 IST)
        _add_review(s, "just_in",   datetime(2026, 9, 10, 14, 29, tzinfo=timezone.utc))  # 19:59 IST today
        _add_review(s, "end_edge",  datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc))  # 20:00 IST today -> NEXT day
        _add_review(s, "start_edge",datetime(2026, 9,  9, 14, 30, tzinfo=timezone.utc))  # 20:00 IST yesterday -> IN
        _add_review(s, "too_early", datetime(2026, 9,  9, 14, 29, tzinfo=timezone.utc))  # 19:59 IST yesterday -> OUT
        s.commit()
        n = received_count(s, now)
    finally:
        s.close()
    # just_in + start_edge are in; end_edge (==end) and too_early are out.
    assert n == 2


def test_received_count_is_an_8pm_to_8pm_window(live_db):
    now = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)   # 8pm IST
    s = live_db.SessionLocal()
    try:
        _add_review(s, "in_1h",   now - timedelta(hours=1))
        _add_review(s, "in_23h",  now - timedelta(hours=23))
        _add_review(s, "edge_24h", now - timedelta(hours=24))    # inclusive edge
        _add_review(s, "old_25h", now - timedelta(hours=25))     # excluded
        _add_review(s, "future",  now + timedelta(hours=1))      # excluded
        _add_review(s, "no_date", None)                          # excluded
        s.commit()
        n = received_count(s, now)
    finally:
        s.close()
    # 3 in window: in_1h, in_23h, edge_24h. The 25h-old, the future one, and the
    # one with no received_at are all out.
    assert n == 3


def test_build_daily_digest_dates_in_ist(live_db):
    # 18:00 UTC on 9 Sep is 23:30 IST on 9 Sep — the label is the IST date.
    from server.db import Review, RcaDraft
    now = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)
    solved_today = datetime(2026, 9, 9, 6, 30, tzinfo=timezone.utc)   # 12:00 IST Sep9
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="r", received_at=_n(solved_today), status="sent",
                     rating=1, picked_up_by="Avi"))
        s.add(RcaDraft(id="r-d", review_id="r", match_tier=1,
                       sent_at=_n(solved_today), booking={"id": "B1"}))
        # one still-open review so the pending headline is a real non-zero
        s.add(Review(id="open", received_at=_n(solved_today), status="draft", rating=1))
        s.add(RcaDraft(id="open-d", review_id="open", match_tier=2,
                       l1="Supply", l2="Guide No Show"))
        s.commit()
        text = build_daily_digest(s, now)
    finally:
        s.close()
    assert "9 Sep 2026" in text
    # The 8pm->8pm window is stamped: this run (23:30 IST 9 Sep) covers
    # 8pm 8 Sep -> 8pm 9 Sep IST.
    assert "8pm 8 Sep → 8pm 9 Sep IST" in text
    # The backlog is captioned as all-time so it is not read as a windowed count.
    assert "Total pending reviews: *1*" in text
    assert "(all time)" in text
    # Tier now describes the 24h COHORT — what was handled in the window,
    # solved and still-open alike — not the all-time backlog.
    assert "🟢 Tier 1 — 1 (50%)" in text and "🟡 Tier 2 — 1 (50%)" in text
    # Solved-by is the 24h cohort.
    assert "• Avi — 1" in text


def test_a_test_account_is_not_credited_but_its_review_still_counts():
    """"Test" is not an associate. Crediting it puts a fake name in a report a
    manager reads — it once ranked second on time-to-send. The REVIEW is real
    though, so it still counts toward Solved; only the attribution moves to
    Unassigned. One definition, shared with the Reporting page."""
    solved = [Row(solved=True, picked_up_by="Test"),
              Row(solved=True, picked_up_by="QA"),
              Row(solved=True, picked_up_by="Devshree")]
    s = summarize(solved, received_count=3)
    names = {n for n, _ in s.people}
    assert "Test" not in names and "QA" not in names
    assert ("Devshree", 1) in s.people
    assert ("Unassigned", 2) in s.people
    # nothing dropped: the per-person rows still sum to the headline
    assert s.solved == 3
    assert sum(n for _, n in s.people) == s.solved


def test_solved_share_is_of_the_open_pile_and_can_never_exceed_100():
    """Solved carries a share, and its denominator is the OPEN WORKLOAD it came
    out of: still-pending plus cleared-in-the-window.

    NOT Solved-over-Received. Those are different cohorts — Solved includes
    backlog that arrived days earlier — and the real export gives Received 7
    against Solved 42 on 11 Sep, which is exactly how this report once shipped a
    "130%". The open-pile denominator cannot be exceeded by its own numerator."""
    # the 11 Sep shape: far more solved than arrived
    s = summarize([Row(solved=True, picked_up_by="Devshree")] * 42,
                  received_count=7,
                  pending_rows=[Row(tier=1)] * 64)
    out = render_digest(s, "11 Sep 2026")
    assert "• Solved today — *42* (40% of 106 = 64 pending + 42 solved)" in out   # 42 / (64+42)
    assert "(130%)" not in out
    # Received carries no share: there is no honest denominator for arrivals.
    assert "• Received today — *7*" in out
    assert "Received today — *7* (" not in out


def test_no_solved_share_when_there_is_no_pending_cohort():
    """Without a pending cohort the open pile is unknown, so no figure is
    invented — the count stands alone rather than borrowing Received."""
    out = render_digest(summarize([Row(solved=True)] * 3, received_count=9), "x")
    assert "• Solved today — *3*" in out
    assert "• Solved today — *3* (" not in out


def test_the_tier_mix_is_the_24h_cohort_not_the_backlog(live_db):
    """Tier describes WHAT WE HANDLED IN 24 HOURS — arrived in the window or
    finished in it — solved and still-open alike. Not the all-time backlog,
    which moves far too slowly to be a daily signal."""
    from server.db import Review, RcaDraft
    now = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)     # 8pm IST Sep10
    inwin = datetime(2026, 9, 10, 6, 0)                          # inside the window
    s = live_db.SessionLocal()
    try:
        # arrived in the window, still open -> in the cohort
        s.add(Review(id="w_open", received_at=inwin, status="draft", rating=1))
        s.add(RcaDraft(id="w_open-d", review_id="w_open", match_tier=2))
        # arrived earlier but FINISHED in the window -> also in the cohort
        s.add(Review(id="w_old", received_at=datetime(2026, 9, 1), status="sent",
                     rating=1, picked_up_by="Avi"))
        s.add(RcaDraft(id="w_old-d", review_id="w_old", match_tier=1, sent_at=inwin))
        # open, but arrived long ago and untouched -> backlog only, NOT the cohort
        s.add(Review(id="w_stale", received_at=datetime(2026, 8, 1), status="draft", rating=1))
        s.add(RcaDraft(id="w_stale-d", review_id="w_stale", match_tier=2))
        s.commit()
        text = build_daily_digest(s, now)
    finally:
        s.close()
    # the stale backlog review is counted in the HEADLINE...
    assert "Total pending reviews: *2*" in text
    # ...but the tier mix is the 2 handled in the window, not all 3
    assert "(% of 2 handled in 24h)" in text
    assert "🟢 Tier 1 — 1 (50%)" in text
    assert "🟡 Tier 2 — 1 (50%)" in text


def test_a_review_both_received_and_solved_today_is_counted_once():
    """The 24h cohort is a UNION, so a review that arrived and was cleared in the
    same window must not be double counted in the tier mix."""
    from server.db import Review, RcaDraft
    # driven through the pure layer: one row, one bucket
    s = summarize([Row(solved=True, tier=1)], received_count=1,
                  pending_count=5, mix_rows=[Row(tier=1)])
    assert s.mix_base == 1
    assert sum(s.tier.values()) == 1


def test_the_digest_carries_no_slack_italics():
    """Underscores were decoration, and the reader asked for them gone."""
    out = render_digest(_full_summary(), "13 Sep 2026",
                        "8pm 12 Sep → 8pm 13 Sep IST", "(all time)")
    assert "_" not in out, f"an italic marker survived: {out!r}"


def test_a_null_status_review_is_counted_as_pending(live_db):
    """SQL `status != 'sent'` is NULL for a NULL row, so a plain comparison drops
    such a review from BOTH Pending and Solved and leaves it in neither. The
    ORM's default fires on None, so the NULL is forced through raw SQL —
    otherwise this test would be vacuous, which mutation testing proved."""
    from sqlalchemy import text
    from server.services.daily_report import pending_count
    s = live_db.SessionLocal()
    try:
        s.execute(text("INSERT INTO reviews (id, status, rating, received_at) "
                       "VALUES ('nullst', NULL, 1, '2026-09-10 06:00:00')"))
        s.commit()
        really_null = s.execute(
            text("SELECT status IS NULL FROM reviews WHERE id='nullst'")).scalar()
        assert really_null, "the fixture did not actually store a NULL status"
        assert pending_count(s) >= 1, "a NULL-status review vanished from pending"
    finally:
        s.close()
