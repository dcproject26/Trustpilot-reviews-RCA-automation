"""Finishing a review from the RCA card, with or without a Slack post.

POSTING TO THE THREAD NOW FINISHES THE REVIEW ITSELF. The RCA reaching the
thread is the work; leaving the review in Matched afterwards meant every
posted review needed a second, separate click, and one forgotten click left
finished work sitting in the queue. So Post to Slack thread posts AND moves
the review to Sent, through the same /send endpoint the Send button uses —
which refuses to post when rca_posted_at is set, so nothing reaches the thread
twice.

This button is the OTHER route: finishing a review without posting anything.

IT WAS DISABLED UNTIL THE RCA WAS IN THE THREAD. The reason was sound —
/send POSTS the RCA when nothing has been posted, and a second copy in a
thread people are reading is the one outcome this control exists to avoid.

The consequence was a dead end. `rca_posted_at` is only set by a SUCCESSFUL
Slack post, so wherever posting is impossible — Slack unconfigured, the app
not in the channel, a revoked token, or simply a review added by hand with no
thread — the button was greyed out for ever and a matched review could never
be finished from the card it lives on.

So it no longer gates; it CHANGES. With the RCA in the thread it marks the
review sent through /send. Without it, it finishes the review through /close,
which never touches Slack and is recorded as closed-by-hand so the Sent tab
still tells the two endings apart. Nothing is posted from this button in
either mode, which is what the disabling was protecting.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _render(page, posted_at):
    """Render the RCA column with or without a posted RCA, and report the
    button. The row is restored afterwards — `page` is module-scoped."""
    return page.evaluate("""(ts) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.rcaPostedAt;
      r.rcaPostedAt = ts;
      renderRcaCol();
      const b = document.querySelector('[data-mark-sent]');
      const out = b ? {mode: b.dataset.sentMode, text: b.textContent.trim(),
                       disabled: b.disabled, title: b.getAttribute('title')}
                    : null;
      r.rcaPostedAt = keep;
      renderRcaCol();
      return out; }""", posted_at)


def test_it_is_never_disabled(page):
    """The dead end. A control that can never become available is not a
    legitimate disabled state; it is a route that does not exist."""
    for ts in (None, "2026-08-05T10:00:00"):
        got = _render(page, ts)
        assert got, "the Mark sent button did not render"
        assert got["disabled"] is False, (ts, got)


def test_with_the_rca_posted_it_marks_sent(page):
    got = _render(page, "2026-08-05T10:00:00")
    assert got["mode"] == "send", got
    assert "Mark sent" in got["text"], got


def test_with_nothing_posted_it_offers_to_finish_without_posting(page):
    """The label has to say what will happen. "Mark sent" on a review nothing
    was sent for would be the Sent tab lying, which is the tab that exists to
    say what still needs doing."""
    got = _render(page, None)
    assert got["mode"] == "close", got
    assert "without posting" in got["text"].lower(), got
    assert "Slack" in got["title"], got


def test_the_two_modes_are_distinguishable_before_clicking(page):
    a = _render(page, None)
    b = _render(page, "2026-08-05T10:00:00")
    assert a["text"] != b["text"]
    assert a["title"] != b["title"]


# ── and each mode calls the endpoint it names ──────────────────────────────

def _click(page, posted_at):
    return page.evaluate("""async (ts) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.rcaPostedAt;
      r.rcaPostedAt = ts;
      renderRcaCol();
      const b = document.querySelector('[data-mark-sent]');
      const hits = [];
      const real = window.fetch;
      window.fetch = (u, o) => { hits.push(String(u));
        return Promise.resolve({ok: true, json: () => Promise.resolve(
          {ok: true, posted: false, sent_route: 'rca_posted'})}); };
      b.click();
      await new Promise(x => setTimeout(x, 500));
      window.fetch = real;
      r.rcaPostedAt = keep;
      return hits; }""", posted_at)


def test_close_mode_calls_close_and_never_send(page):
    """/send POSTS the RCA when nothing has been posted. Calling it here would
    put the RCA in the thread from a button labelled "without posting"."""
    hits = _click(page, None)
    assert any("/close" in u for u in hits), hits
    assert not any(u.endswith("/send") for u in hits), hits


def test_send_mode_calls_send_and_never_close(page):
    hits = _click(page, "2026-08-05T10:00:00")
    assert any(u.endswith("/send") for u in hits), hits
    assert not any("/close" in u for u in hits), hits


def test_posting_the_rca_flips_the_button_to_send_mode(page):
    """The post handler does not re-render — that would discard an
    in-progress edit of the post — so it updates the control directly. Left
    alone, the button would send a /close for a review whose RCA had just
    gone out."""
    import inspect  # noqa: F401  (kept for symmetry with the source note)
    got = page.evaluate("""() => {
      const src = [...document.querySelectorAll('script')]
        .map(s => s.textContent).join('\\n');
      return src.includes("_sentBtn.dataset.sentMode = 'send'"); }""")
    assert got, ("posting the RCA no longer flips Mark sent out of close mode")


# ── posting to the thread finishes the review ──────────────────────────────

def _post(page, send_ok=True):
    """Click Post to Slack thread with the network stubbed, and report every
    url it called plus the row state afterwards."""
    return page.evaluate("""async (sendOk) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = {p: r.rcaPostedAt, s: r.status, sr: r.sentRoute};
      r.rcaPostedAt = null;
      renderRcaCol();
      const b = document.querySelector('[data-slack-post]');
      if (!b) return {error: 'no post button'};
      const hits = [];
      const real = window.fetch;
      window.fetch = (u, o) => {
        const url = String(u);
        hits.push(url);
        if (url.includes('/post-rca')) {
          return Promise.resolve({ok: true, json: () => Promise.resolve(
            {ok: true, already_posted: false, ts: '1.5',
             posted_at: '2026-08-05T10:00:00'})});
        }
        if (url.endsWith('/send')) {
          return sendOk
            ? Promise.resolve({ok: true, json: () => Promise.resolve(
                {ok: true, posted: false, sent_route: 'rca_posted'})})
            : Promise.resolve({ok: false, status: 500,
                               text: () => Promise.resolve('boom'),
                               json: () => Promise.resolve({})});
        }
        return Promise.resolve({ok: true, json: () => Promise.resolve({})});
      };
      // TWO CLICKS. Post-to-thread is a confirm button on EVERY send now, not
      // only on a repeat: the first click arms it, the second posts. Clicking
      // once here would make no request at all and this test would report the
      // MOVE-TO-SENT as broken when nothing had been posted in the first
      // place. Asserted rather than assumed, so a regression in the arming
      // shows up as itself.
      b.click();
      await new Promise(x => setTimeout(x, 300));
      const armed = b.textContent || '';
      if (!/Confirm|second copy/i.test(armed)) {
        window.fetch = real;
        return {error: 'the first click did not arm the post button: ' + armed};
      }
      b.click();
      await new Promise(x => setTimeout(x, 700));
      const out = {hits, before: keep.s, status: r.status,
                   sentRoute: r.sentRoute,
                   postedAt: r.rcaPostedAt,
                   note: (document.querySelector('[data-slack-post-err]') || {})
                           .textContent || ''};
      window.fetch = real;
      r.rcaPostedAt = keep.p; r.status = keep.s; r.sentRoute = keep.sr;
      renderRcaCol();
      return out; }""", send_ok)


def test_posting_to_the_thread_also_moves_the_review_to_sent(page):
    """The RCA in the thread IS the work. Leaving the review in Matched meant
    a second, separate click, and one forgotten click leaves finished work in
    the queue."""
    got = _post(page, True)
    assert not got.get("error"), got
    assert any("/post-rca" in u for u in got["hits"]), got["hits"]
    assert any(u.endswith("/send") for u in got["hits"]), got["hits"]
    assert got["status"] == "sent", got
    assert got["sentRoute"] == "rca_posted", got


def test_the_move_goes_through_send_so_nothing_is_posted_twice(page):
    """/send refuses to post when rca_posted_at is set — which it is by the
    time this runs. Reusing that guard beats re-implementing it; a second copy
    of the RCA in a thread people are reading is the outcome the whole area
    guards against."""
    got = _post(page, True)
    sends = [u for u in got["hits"] if u.endswith("/send")]
    posts = [u for u in got["hits"] if "/post-rca" in u]
    assert len(posts) == 1, posts
    assert len(sends) == 1, sends


def test_a_failed_move_does_not_pretend_the_post_failed_too(page):
    """The RCA HAS gone out — that is a fact and the card must keep saying so.
    Only the move is reported as not having happened, and the reader is
    pointed at the control that completes it."""
    got = _post(page, False)
    assert got["postedAt"], "the post was forgotten because the move failed"
    # UNCHANGED, not "not sent": the fixture's own status is whatever the
    # server gave it, and asserting a literal would test the fixture rather
    # than the behaviour.
    assert got["status"] == got["before"], got
    assert "IS in the thread" in got["note"], got["note"]
    assert "did not" in got["note"], got["note"]
