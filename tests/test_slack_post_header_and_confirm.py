"""What you SEE before you post, and how many clicks it takes to send it.

TWO DEFECTS, both about the moment just before a message reaches the whole
team.

1. THE HEADER BLOCK WAS NEVER ON SCREEN. The composed post opens with two
   lines — "RCA — @reviewteam / BID … · guest · stars" and "Issue: L1 / L2 /
   sub-theme" — and the preview rendered nine editable rows starting at
   "Booking details". So the classification went out with every post and the
   person checking the draft could not see it.

   It matters most where it is WRONG. An unclassified review posts with NO
   Issue line at all — a deliberate rule, because "Issue: — / —" once reached
   a thread and read as a broken generator — and off screen that is
   indistinguishable from a classified review whose line is simply above the
   fold. Two different states, one blank.

2. THE FIRST POST WENT ON ONE CLICK. The confirm step existed only for a
   REPEAT post. The button sits directly under nine contenteditable rows that
   people click in and out of all day, and a Slack post cannot be recalled.

Driven in a real browser: this is client-side, and a source assertion passes
just as happily against a build where the branch it names is unreachable.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import (page, CHROME, _rca_tab,   # noqa: E402,F401
                                        _n_sections)


# ── the header block is shown ───────────────────────────────────────────────

def test_the_header_block_has_a_row_of_its_own(page):
    _rca_tab(page, "slack")
    assert page.locator(".spost-row.spost-fixed").count() == 1


def test_the_classification_that_will_be_posted_is_on_screen(page):
    """The whole point: what goes out is what you can read."""
    _rca_tab(page, "slack")
    shown = page.locator(".spost-fixed .spost-readonly").inner_text()
    l1 = page.evaluate("() => (REVIEWS.find(x => x.id === state.selected)||{}).rca.issueL1")
    assert l1 and l1 in shown, f"the Issue line is not on screen: {shown!r}"


def test_what_is_shown_is_what_the_composer_actually_sends(page):
    """Not a second rendering of the same idea. A header row that agrees with
    the post today and drifts tomorrow is worse than no row."""
    _rca_tab(page, "slack")
    shown = page.locator(".spost-fixed .spost-readonly").inner_text().strip()
    sent = page.evaluate("""() => {
        const a = document.querySelector('[data-slack-edit]');
        return (a ? a.value : '').split('_'.repeat(61))[0].trim(); }""")
    assert shown == sent, f"preview {shown!r} != post {sent!r}"


def test_the_header_is_not_editable_here(page):
    """Its home is the L1/L2 selects in the RCA above. A second place to set
    it is two places to disagree."""
    _rca_tab(page, "slack")
    ro = page.locator(".spost-fixed .spost-readonly")
    assert ro.get_attribute("contenteditable") in (None, "false")
    assert page.locator(".spost-fixed [data-slack-sec-body]").count() == 0, \
        "the header row is wired into the recompose as if it were a section"


def test_the_header_row_is_not_counted_as_a_section(page):
    """It is never toggled and never left out, so it must not move the
    "N of M sections included" count."""
    _rca_tab(page, "slack")
    n = _n_sections(page)          # the count line the page itself derives
    rows = page.locator(".spost-row:not(.spost-fixed)").count()
    assert rows == n, f"{rows} section rows for {n} sections"


def test_an_unclassified_rca_says_the_post_will_carry_no_issue_line(page):
    """THE INVERSE BUG. Blank and blank-because-unclassified were the same
    nothing. Now one of them is a warning that names the fix."""
    page.evaluate("""() => {
        const r = REVIEWS.find(x => x.id === state.selected);
        r.rca.issueL1 = ''; r.rca.issueL2 = ''; r.rca.subTheme = null;
        r.rca.primaryScenario = ''; r.rca.overlayScenarios = [];
        renderRcaCol(); }""")
    _rca_tab(page, "slack")
    assert page.locator(".spost-warn").count() == 1, \
        "an unclassified RCA posts with no Issue line and said nothing about it"
    warn = page.locator(".spost-warn").inner_text()
    assert "Classification" in warn, warn


def test_a_classified_rca_shows_no_such_warning(page):
    """The counterpart — a warning on every card is one nobody reads."""
    page.evaluate("""() => {
        const r = REVIEWS.find(x => x.id === state.selected);
        r.rca.issueL1 = 'Operations Issue'; r.rca.issueL2 = 'Ticket Issues';
        renderRcaCol(); }""")
    _rca_tab(page, "slack")
    assert page.locator(".spost-warn").count() == 0


# ── two clicks to post ──────────────────────────────────────────────────────

def _btn(page):
    return page.locator("[data-slack-post]")


def _reset(page, posted=None):
    """Start from a known button state, then reopen the Slack tab.

    The confirm window is a live 4-second timer on a DOM node, so a sibling
    test that armed the button and finished 200ms later would hand the next one
    a button that is ONE click from posting to the channel. Each test sets its
    own starting state rather than inheriting a countdown.

    `posted` sets rcaPostedAt, because which of the two confirmations appears
    depends on it.
    """
    page.evaluate("""(posted) => {
        const b = document.querySelector('[data-slack-post]');
        if (b) {
          if (b.dataset.confirmTimer) clearTimeout(Number(b.dataset.confirmTimer));
          delete b.dataset.confirmTimer;
          delete b.dataset.confirming;
          b.classList.remove('confirming');
          b.disabled = false;
        }
        const r = REVIEWS.find(x => x.id === state.selected);
        if (r) r.rcaPostedAt = posted;
        renderRcaCol(); }""", posted)
    _rca_tab(page, "slack")


def _arm_no_network(page):
    """Count the posts instead of making them."""
    page.evaluate("""() => {
        window.__posts = [];
        window.__origFetch = window.fetch;
        window.fetch = (u, o) => {
          if (String(u).includes('/post-rca')) {
            window.__posts.push(String(u));
            return Promise.resolve(new Response(
              JSON.stringify({posted_at: '2026-08-25T10:00:00Z'}),
              {status: 200, headers: {'Content-Type': 'application/json'}}));
          }
          return window.__origFetch(u, o); }; }""")


def _restore(page):
    page.evaluate("() => { if (window.__origFetch) window.fetch = window.__origFetch; }")


def test_one_click_does_not_post(page):
    """THE DEFECT: a first post reached the channel on a single click, from a
    button sitting directly under nine contenteditable rows."""
    _reset(page)
    _arm_no_network(page)
    try:
        _btn(page).click()
        page.wait_for_timeout(300)
        assert page.evaluate("() => window.__posts.length") == 0, \
            "one click sent the RCA to the whole team"
    finally:
        _restore(page)


def test_the_first_click_says_what_the_second_will_do(page):
    """An armed button that looks unarmed is a trap, not a guard."""
    _reset(page)
    _arm_no_network(page)
    try:
        _btn(page).click()
        page.wait_for_timeout(200)
        assert "Confirm" in _btn(page).inner_text(), _btn(page).inner_text()
        assert "confirming" in (_btn(page).get_attribute("class") or ""), \
            "only the wording changed; a skimming reader sees the same button"
    finally:
        _restore(page)


def test_the_second_click_posts(page):
    """The guard must not block the work."""
    _reset(page)
    _arm_no_network(page)
    try:
        _btn(page).click()
        page.wait_for_timeout(200)
        _btn(page).click()
        page.wait_for_timeout(500)
        assert page.evaluate("() => window.__posts.length") == 1, \
            "two clicks did not post"
    finally:
        _restore(page)


def test_a_first_post_is_not_sent_with_force(page):
    """force=true tells the server to add a SECOND copy to the thread. Sending
    it on an ordinary first post would defeat the duplicate guard entirely —
    which is why `again` is read from what the FIRST click knew, not from
    rcaPostedAt at send time."""
    _reset(page, posted=None)
    _arm_no_network(page)
    try:
        _btn(page).click(); page.wait_for_timeout(200)
        _btn(page).click(); page.wait_for_timeout(500)
        urls = page.evaluate("() => window.__posts")
        assert urls, "nothing was posted"
        assert "force=true" not in urls[0], urls
    finally:
        _restore(page)


def test_an_already_posted_rca_warns_about_the_second_copy_instead(page):
    """Two different risks, two different sentences. "Confirm — post to the
    thread" over a thread that already has a copy loses the warning that
    actually needed saying."""
    _reset(page, posted="2026-08-25T09:00:00Z")
    _arm_no_network(page)
    try:
        _btn(page).click()
        page.wait_for_timeout(200)
        assert "second copy" in _btn(page).inner_text().lower(), _btn(page).inner_text()
        _btn(page).click()
        page.wait_for_timeout(500)
        urls = page.evaluate("() => window.__posts")
        assert urls and "force=true" in urls[0], \
            f"the repeat post did not ask the server to allow a duplicate: {urls}"
    finally:
        _restore(page)
        _reset(page)


def test_the_arm_lapses_so_a_later_unrelated_click_cannot_finish_it(page):
    """Someone clicks, is interrupted, comes back and clicks something. The
    window closes on its own rather than leaving a live trigger on screen."""
    _reset(page)
    _arm_no_network(page)
    try:
        _btn(page).click()
        page.wait_for_timeout(4400)
        assert "Confirm" not in _btn(page).inner_text(), _btn(page).inner_text()
        assert "confirming" not in (_btn(page).get_attribute("class") or "")
        _btn(page).click()
        page.wait_for_timeout(300)
        assert page.evaluate("() => window.__posts.length") == 0, \
            "a click after the window lapsed posted anyway"
    finally:
        _restore(page)
