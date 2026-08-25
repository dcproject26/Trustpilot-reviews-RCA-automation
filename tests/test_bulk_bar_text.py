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


def _just_now(page):
    """A finished_at in the shape the server sends: utcnow().isoformat(), with
    NO timezone suffix. That shape is the whole point of the age helper."""
    return page.evaluate(
        "() => new Date().toISOString().replace('Z', '').slice(0, 19)")


def test_a_finished_batch_reports_the_count_not_a_state(page):
    txt = _render(page, running=False, done=9, remaining=0,
                  current_state="", finished_at=_just_now(page))
    assert "finished 9/9" in txt, txt


# ── the bar comes down ──────────────────────────────────────────────────────
# REPORTED: "even after completing the run, it's not going away." The database
# was clean throughout — done 83, dead 1, nothing queued or running — so there
# was nothing left for the server to fix. The bar was refusing to go.
#
# /api/reviews/bulk-status always answers about the NEWEST batch and keeps its
# finished_at for ever, and _bulkRender showed the bar whenever finished_at was
# set. So EVERY page load after any batch had ever run painted "finished 1/1"
# and left it there. The 20-second auto-hide could not help: it lived inside
# `else if (_bulkTimer)`, and a page that merely loaded has no timer.

def _visible(page, **st):
    _render(page, **st)
    return page.evaluate(
        "() => !document.getElementById('bulk-bar').hidden")


def test_a_batch_that_finished_long_ago_is_not_on_screen(page):
    """The headline defect. A summary from an hour ago is history, and history
    presented in the status bar reads as status."""
    assert _visible(page, running=False, done=1, total=1, remaining=0,
                    current_state="", finished_at="2026-08-25T04:00:00") is False


def test_a_batch_that_just_finished_is_still_on_screen(page):
    """The counterpart. Whisking the result away the instant it lands is the
    opposite failure — nobody would ever see how the run went."""
    assert _visible(page, running=False, done=1, total=1, remaining=0,
                    current_state="", finished_at=_just_now(page)) is True


def test_a_running_batch_is_shown_however_old_its_timestamps(page):
    """Staleness is only about a FINISHED summary. A long run must not have
    its own progress bar taken away."""
    assert _visible(page, running=True, done=0, total=9, remaining=9,
                    current_state="waiting",
                    finished_at="2026-08-25T04:00:00") is True


def test_the_finished_timestamp_is_read_as_utc_not_local(page):
    """THE TRAP, and it would have been invisible to me and permanent for the
    team. The server sends utcnow().isoformat() — "2026-08-25T10:00:00", no
    suffix — and `new Date(bare)` reads it as LOCAL time. In IST (+5:30) that
    puts the instant five and a half hours AHEAD of the truth, the age comes
    out negative, every finished batch reads as fresh and the bar never hides.

    Asserted against a timestamp 30 minutes old in UTC: correct parsing makes
    that stale, local parsing makes it fresh (or future-dated) anywhere east of
    Greenwich.
    """
    old = page.evaluate(
        "() => new Date(Date.now() - 30*60*1000).toISOString().replace('Z','').slice(0,19)")
    assert _visible(page, running=False, done=1, total=1, remaining=0,
                    current_state="", finished_at=old) is False, (
        "a batch that finished 30 minutes ago is still on screen — the "
        "timestamp is being read in local time")


def test_a_finished_at_in_the_future_does_not_read_as_infinitely_fresh(page):
    """Clock skew between the app server and the browser. A negative age must
    clamp to 0 (fresh, and it will go stale), never stay negative for ever."""
    future = page.evaluate(
        "() => new Date(Date.now() + 60*60*1000).toISOString().replace('Z','').slice(0,19)")
    age = page.evaluate("(s) => _bulkFinishedAgeS(s)", {"finished_at": future})
    assert age == 0, age


def test_an_unparseable_timestamp_does_not_hide_a_real_result(page):
    """If the age cannot be computed, showing the summary is the safe error:
    an extra bar someone dismisses beats a finished run nobody ever saw."""
    for junk in ("", None, "not-a-date", "  "):
        assert page.evaluate("(s) => _bulkFinishedAgeS(s)",
                             {"finished_at": junk}) is None
    assert _visible(page, running=False, done=1, total=1, remaining=0,
                    current_state="", finished_at="not-a-date") is True


def test_a_timestamp_that_already_carries_a_zone_is_not_double_suffixed(page):
    """The server could start sending an aware datetime. Appending a Z to
    "…+00:00" makes it unparseable, and the bar would then never hide again."""
    iso = page.evaluate("() => new Date(Date.now() - 30*60*1000).toISOString()")
    age = page.evaluate("(s) => _bulkFinishedAgeS(s)", {"finished_at": iso})
    assert age is not None and 1700 < age < 1900, age
    offset = iso.replace("Z", "+00:00")
    age2 = page.evaluate("(s) => _bulkFinishedAgeS(s)", {"finished_at": offset})
    assert age2 is not None and 1700 < age2 < 1900, age2


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


def test_a_stranded_batch_does_not_promise_a_retry(page):
    """A stranded run is out of attempts: claim_next matches neither of its
    branches, so nothing reclaims it. "They retry once the lease lapses" is
    false for it, and that sentence is why someone waits an hour in front of a
    bar reading 0/1."""
    txt = _render(page, current="", current_state="stalled",
                  stalled=1, stranded=1, total=1, remaining=1)
    assert "retry once the lease lapses" not in txt, txt
    assert "gave up" in txt or "out of retries" in txt, txt


def test_a_retryable_stall_still_says_it_comes_back(page):
    """The counterpart. Telling someone to restart when the run is about to
    resume on its own is the opposite error."""
    txt = _render(page, current="", current_state="stalled",
                  stalled=1, stranded=0)
    assert "retry once the lease lapses" in txt, txt


def test_the_bar_takes_itself_down_after_a_finished_poll(page):
    """THE HALF _bulkRender CANNOT DO. Its staleness check catches a summary
    you came BACK to. This catches one that went stale while you were looking
    at it: pollBulk only re-renders while a timer is running, so on a finished
    batch nothing would ever call _bulkRender again.

    The auto-hide used to live inside `else if (_bulkTimer)`, so a page that
    merely LOADED after the batch ended never scheduled one — and that is the
    page every reload produces.
    """
    fresh = _just_now(page)
    hidden = page.evaluate("""async (fin) => {
        const realFetch = window.fetch;
        const realHide = BULK_HIDE_MS;
        BULK_HIDE_MS = 150;                       // don't spend 20s of suite
        window.fetch = (u, o) => String(u).includes('/bulk-status')
          ? Promise.resolve(new Response(JSON.stringify({
              running: false, total: 1, done: 1, failed: 0, remaining: 0,
              current: '', current_state: '', stalled: 0, stranded: 0,
              eta_s: null, finished_at: fin, results: []}),
              {status: 200, headers: {'Content-Type': 'application/json'}}))
          : realFetch(u, o);
        try {
          await pollBulk(false);
          const shownFirst = !document.getElementById('bulk-bar').hidden;
          await new Promise(r => setTimeout(r, 400));
          return {shownFirst,
                  hiddenAfter: document.getElementById('bulk-bar').hidden};
        } finally {
          window.fetch = realFetch;
          BULK_HIDE_MS = realHide;
        } }""", fresh)
    assert hidden["shownFirst"] is True, \
        "a batch that just finished was never shown at all"
    assert hidden["hiddenAfter"] is True, \
        "the bar was never scheduled to come down — nothing will remove it"


def test_a_bare_timestamp_is_utc_in_a_non_utc_browser(ui_browser, ui_server):
    """THE TEST THAT HAD TO LEAVE UTC TO EXIST.

    The server sends `datetime.utcnow().isoformat()` — "2026-08-25T10:00:00",
    no zone — and `new Date(bare)` reads a bare string as LOCAL time. In IST
    (+5:30) the parsed instant lands five and a half hours ahead of the real
    one, the age comes out negative, every finished batch reads as fresh, and
    the bar never hides. Permanent for this team; invisible to me.

    An earlier version of this test asserted a 30-minute-old bare timestamp was
    hidden — and MUTATION TESTING CAUGHT THE TEST, not the code: deleting the
    UTC coercion changed nothing, because the CI browser runs in UTC where
    local IS UTC. A test that can only pass in the one timezone where the bug
    does not exist is not a test of the bug.

    So this opens its own context in Asia/Kolkata and compares the bare form
    against the same instant written with an explicit Z. They must agree.
    """
    ctx = ui_browser.new_context(timezone_id="Asia/Kolkata")
    pg = ctx.new_page()
    try:
        pg.set_default_timeout(15000)
        pg.goto(f"http://127.0.0.1:{ui_server.port}/", wait_until="load")
        got = pg.evaluate("""() => {
            const t = Date.now() - 30 * 60 * 1000;
            const zoned = new Date(t).toISOString();               // ...Z
            const bare  = zoned.replace('Z', '').slice(0, 19);     // no zone
            return {offsetMin: new Date().getTimezoneOffset(),
                    bare: _bulkFinishedAgeS({finished_at: bare}),
                    zoned: _bulkFinishedAgeS({finished_at: zoned})}; }""")
        assert got["offsetMin"] != 0, (
            "this context is still UTC — the test cannot detect the bug it "
            "exists for")
        assert got["zoned"] is not None and got["bare"] is not None
        assert abs(got["bare"] - got["zoned"]) < 5, (
            f"a bare timestamp is being read in local time: bare reads as "
            f"{got['bare']}s old, the same instant with a Z reads as "
            f"{got['zoned']}s old")
        assert got["bare"] > 1700, \
            "a 30-minute-old batch does not read as 30 minutes old"
    finally:
        pg.close()
        ctx.close()
