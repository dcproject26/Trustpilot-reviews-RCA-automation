"""The bulk bar says WHY it is not advancing.

`current` empty had three causes and the bar rendered one word — "starting" —
at all three. After a restart mid-batch that word is false: the rows are
claimed by a container that is gone, nothing is draining them, and nothing will
until their leases lapse. The bar sat on a review nothing was working on.

Driven in a real browser, because _bulkRender is client-side and a source
assertion would pass just as happily against a build where the branch it names
is unreachable. jobs.batch_status supplies current_state; this is the half that
puts it in front of a reader.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _render(page, **st):
    base = {"running": True, "total": 9, "done": 0, "failed": 0,
            "remaining": 9, "current": "", "current_state": "waiting",
            "stalled": 0, "eta_s": None, "finished_at": None, "results": []}
    base.update(st)
    return page.evaluate("""(s) => { _bulkRender(s);
        return document.getElementById('bulk-text').textContent; }""", base)


def test_a_batch_with_a_live_worker_names_the_review(page):
    txt = _render(page, current="tp_1786924680_704509", current_state="running")
    assert "tp_1786924680" in txt, txt


def test_every_claim_frozen_says_stalled_and_never_starting(page):
    """THE OBSERVED STATE: 0/9 done, a review on the bar, nothing moving."""
    txt = _render(page, current="", current_state="stalled", stalled=2)
    assert "starting" not in txt, txt
    assert "stalled" in txt, txt
    assert "2" in txt, "the reader is not told how many are stuck"


def test_a_stalled_bar_says_the_work_is_retried_not_lost(page):
    """"Stalled" alone reads as "your re-runs are gone". They are reclaimed
    when the lease lapses, and that is the difference between waiting and
    clicking the button again."""
    txt = _render(page, current="", current_state="stalled", stalled=1)
    assert "lease" in txt.lower() or "retr" in txt.lower(), txt


def test_queued_with_no_worker_yet_is_waiting_not_stalled(page):
    txt = _render(page, current="", current_state="waiting")
    assert "waiting" in txt, txt
    assert "stalled" not in txt, txt


def test_a_running_state_with_no_review_does_not_claim_one(page):
    """current_state says running but current is blank — a shape that should
    not occur. It must not print an empty name as though it were a review."""
    txt = _render(page, current="", current_state="running")
    assert "· ·" not in txt and "·  " not in txt, txt


def test_a_finished_batch_reports_the_count_not_a_state(page):
    txt = _render(page, running=False, done=9, remaining=0,
                  current_state="", finished_at="2026-08-25T09:40:00")
    assert "finished 9/9" in txt, txt


def test_the_state_decides_what_is_shown_not_a_leftover_review_id(page):
    """`current_state` is the authority, `current` is only the name.

    They cannot disagree in a payload jobs.batch_status builds — `current` is
    filled only from a MOVING row, so a name implies "running". A client that
    branches on the name instead of the state is fine right up until the two
    come apart: a stale poll answered by an older instance, a deploy mid-batch,
    a cached response. Then the bar prints a review nobody is on, which is the
    exact failure this pair of fields was added to end.
    """
    txt = _render(page, current="tp_1787158427_544909",
                  current_state="stalled", stalled=2)
    assert "tp_1787158427" not in txt, (
        "the bar named a review while reporting that nothing is running: " + txt)
    assert "stalled" in txt, txt
