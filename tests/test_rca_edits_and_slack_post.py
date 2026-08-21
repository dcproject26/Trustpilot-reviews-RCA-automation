"""Every editable control on the RCA card, clicked, and the Slack post it sends.

Two failures reported together, and they are the same failure twice:

  * "after editing the boxes in the rca, its not getting saved" — Area of
    improvement wrote to a column the reader does not consult, so add, delete
    and rewrite all reported success and changed nothing.
  * "i removed all fields except insights clicked send to slack and the entire
    ping with all the deselcted fields yet went to slack" — the post button
    sent no body at all, so the server rebuilt the whole RCA from the draft.
    The screen showed the short post the entire time.

Neither is visible without driving the real page: a control that saves to
nowhere renders exactly like one that saves, and a request that omits its body
succeeds.
"""
import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _draft(page):
    return page.evaluate(
        "async () => (await (await fetch('/api/reviews/tp_ui')).json()).draft")


# ── every editable path round-trips ─────────────────────────────────────────

ROUNDTRIP_JS = r"""
async (path) => {
  const dig = (o, p) => p.split('.').reduce(
      (a,k) => (a==null?a:a[/^\d+$/.test(k)?+k:k]), o);
  const el = document.querySelector(`[data-v3p="${path}"]`);
  if (!el) return {path, missing: true};
  const want = 'RT ' + path;
  el.focus();
  el.textContent = want;
  // focus()/blur() are no-ops on an element inside a collapsed section, and
  // several of these are. Dispatching the event directly drives the same
  // listener, so a field is judged on whether its HANDLER saves rather than
  // on whether the section it lives in happens to be open.
  el.blur();
  el.dispatchEvent(new FocusEvent('blur'));
  await new Promise(r => setTimeout(r, 400));
  const j = await (await fetch('/api/reviews/tp_ui')).json();
  return {path, want, got: dig(j.draft.rca_v3, path)};
}
"""


def test_no_two_fields_write_to_the_same_place(page):
    """Two different contact notes rendered with the same path.

    The unmatched-contact rows indexed into a FILTERED list while the save
    path writes to the real one, so editing an unmatched contact silently
    rewrote a matched one — both rows on screen, one of them quietly lying
    about which note it was.
    """
    dupes = page.evaluate("""() => {
      const seen = {}, out = [];
      for (const e of document.querySelectorAll('#rca-col [data-v3p]')) {
        const p = e.dataset.v3p, t = e.textContent.trim();
        if (seen[p] !== undefined && seen[p] !== t) out.push({path: p, a: seen[p], b: t});
        seen[p] = t;
      }
      return out; }""")
    assert not dupes, (
        "these paths are rendered twice with different content, so one row's "
        "edit overwrites the other's:\n" + json.dumps(dupes, indent=2))


def test_every_editable_field_saves(page):
    """Not a sample — every data-v3p on the card. A field rendered editable
    and wired to nothing is indistinguishable from a working one."""
    paths = page.evaluate(
        "() => [...new Set([...document.querySelectorAll('#rca-col [data-v3p]')]"
        ".map(e => e.dataset.v3p))]")
    assert paths, "no editable fields on the card at all"
    bad = []
    for p in paths:
        res = page.evaluate(ROUNDTRIP_JS, p)
        if res.get("missing") or res.get("got") != res.get("want"):
            bad.append(res)
    assert not bad, (
        f"{len(bad)} of {len(paths)} editable fields did not reach the server:\n"
        + json.dumps(bad, indent=2))


# ── area of improvement: NOT on the card any more ──────────────────────────
#
# Four tests lived here that clicked [data-aoi-add], [data-aoi-idx] and
# [data-aoi-del] and asserted the write reached `area_of_improving`. The card
# was removed from the dashboard — the BACKEND was deliberately left alone, so
# the column, the validator and the Slack section all still work and are
# covered by tests/test_area_of_improvement.py (18 tests, including the
# emptied-list case this file used to own).
#
# Clicking a control that no longer renders is not a failing guarantee, it is
# a test with no subject. What replaces them is the negative: if the card
# comes back, it comes back without any test driving it, and the control
# census in tests/test_controls_actually_work.py is what should catch that —
# so this asserts the controls are genuinely absent rather than merely
# unclicked.

def test_the_area_of_improvement_card_is_not_on_the_dashboard(page):
    """Removed from the card, kept in the backend. Asserted as an ABSENCE so
    a build that renders the controls but wires them to nothing cannot pass
    it, and so the day it returns this fails and asks for tests."""
    got = page.evaluate("""() => ({
        add: document.querySelectorAll('[data-aoi-add]').length,
        idx: document.querySelectorAll('[data-aoi-idx]').length,
        del: document.querySelectorAll('[data-aoi-del]').length})""")
    assert got == {"add": 0, "idx": 0, "del": 0}, (
        f"the Area of improvement controls are rendering again: {got}. They "
        f"have no tests driving them — restore them from git history if the "
        f"card is coming back.")


def test_the_backend_still_holds_the_points_the_card_stopped_showing(page):
    """The removal was a RENDER change. A draft whose column has points must
    still serve them, or "not shown" has quietly become "not stored"."""
    d = _draft(page)
    assert "area_of_improving" in d, (
        "the draft no longer carries area_of_improving at all — the card was "
        "removed and the field went with it")


# ── the Slack post carries only the chosen sections ─────────────────────────

def _open_picker(page):
    if not page.locator(".slack-sec-chip").count():
        page.click("[data-slack-customize]")
        page.wait_for_timeout(300)


def _chip(key):
    return f'.slack-sec-chip:has(input[data-slack-section="{key}"])'


def _post(page):
    """Click Post, answering the second-copy confirmation if it appears.

    An already-posted review turns the button into "Post a second copy?" —
    deliberately, because a repeat drops another copy into a thread people are
    reading. Tests that ignore it get no request at all and read as a silent
    failure of the thing under test rather than of the test.
    """
    page.click("[data-slack-post]")
    page.wait_for_timeout(400)
    if "second copy" in (page.locator("[data-slack-post]").text_content() or ""):
        page.click("[data-slack-post]")
        page.wait_for_timeout(400)
    page.wait_for_timeout(400)


def test_deselected_sections_do_not_reach_slack(page):
    """The reported bug, end to end: the request body is inspected."""
    _open_picker(page)
    page.click("[data-slack-sec-none]")
    page.wait_for_timeout(500)
    page.click(_chip("insights"))
    page.wait_for_timeout(500)

    shown = page.evaluate("() => document.querySelector('[data-slack-edit]').value")
    assert "Experience insights" in shown
    assert "What went wrong" not in shown, "the preview itself is wrong"

    sent = []
    page.route("**/post-rca*", lambda route: (
        sent.append(route.request.post_data),
        route.fulfill(status=200, content_type="application/json",
                      body='{"ok":true,"already_posted":false,"ts":"1",'
                           '"posted_at":"2026-08-02T00:00:00"}')))
    try:
        _post(page)
    finally:
        page.unroute("**/post-rca*")

    assert sent, "the post request carried no body at all — the server would " \
                 "rebuild the full RCA and every deselected section would go"
    body = json.loads(sent[0])
    assert body.get("text"), f"no text in the post body: {sent[0]}"
    assert "Experience insights" in body["text"]
    for gone in ("What went wrong", "Booking logs", "SOP compliance",
                 "Actions taken", "TL;DR"):
        assert gone not in body["text"], \
            f"{gone!r} was switched off and went to Slack anyway"


def test_what_is_shown_is_what_is_saved_is_what_is_sent(page):
    """One store. The preview, the stored override and the request body have
    to be the same string, or two of the three are lying."""
    _open_picker(page)
    page.click("[data-slack-sec-none]")
    page.wait_for_timeout(500)
    page.click(_chip("resolution"))
    page.wait_for_timeout(600)

    shown = page.evaluate("() => document.querySelector('[data-slack-edit]').value")
    saved = _draft(page)["slack_thread_override"]
    assert saved.strip() == shown.strip(), \
        f"the preview and the saved post differ:\n{shown!r}\n{saved!r}"

    sent = []
    page.route("**/post-rca*", lambda route: (
        sent.append(route.request.post_data),
        route.fulfill(status=200, content_type="application/json",
                      body='{"ok":true,"already_posted":false,"ts":"1",'
                           '"posted_at":"2026-08-02T00:00:00"}')))
    try:
        _post(page)
    finally:
        page.unroute("**/post-rca*")
    assert json.loads(sent[0])["text"].strip() == shown.strip()


def test_the_selection_survives_a_reload(page):
    """It lived in a JavaScript object. Reload and every chip came back on
    while the saved post still had eleven sections missing — the picker and
    the post disagreeing, with no way to tell which was real."""
    _open_picker(page)
    page.click("[data-slack-sec-none]")
    page.wait_for_timeout(500)
    page.click(_chip("insights"))
    page.wait_for_timeout(600)
    before = page.evaluate("() => document.querySelector('[data-slack-edit]').value")

    page.reload(wait_until="load")
    page.wait_for_timeout(1000)
    page.locator(".inbox-row").first.click()
    page.wait_for_timeout(1400)

    after = page.evaluate("() => document.querySelector('[data-slack-edit]').value")
    assert after.strip() == before.strip(), \
        "the saved post did not survive the reload"
    _open_picker(page)
    on = page.evaluate("""() => [...document.querySelectorAll('[data-slack-section]')]
        .filter(c => c.checked).map(c => c.dataset.slackSection)""")
    assert on == ["insights"], \
        f"the chips do not match the post that is actually stored: {on}"


def test_an_empty_section_is_not_read_back_as_a_deselection(page):
    """The chips are derived from the saved post by looking for each section's
    heading. A section the composer skipped because it had nothing to say has
    no heading either — and reading that as "the associate switched it off"
    would silently turn a section off for good, one reload at a time.

    Driven by actually emptying a section rather than by asserting the guard
    exists: the guard is one line, and deleting it leaves the whole suite
    green unless a section is genuinely empty when this runs.
    """
    # Takedown renders as "" when there is no verdict — most sections fall
    # back to a dash, so this is the one that actually goes empty.
    page.evaluate("""async () => {
        await fetch('/api/reviews/tp_ui/draft-v2', {method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({takedown: {}})}); }""")
    page.reload(wait_until="load")
    page.wait_for_timeout(1000)
    page.locator(".inbox-row").first.click()
    page.wait_for_timeout(1400)

    _open_picker(page)
    page.click("[data-slack-sec-all]")
    page.wait_for_timeout(700)
    saved = _draft(page)["slack_thread_override"] or ""
    assert "*Review takedown*" not in saved, \
        "the fixture is not empty here, so this test would prove nothing"

    page.reload(wait_until="load")
    page.wait_for_timeout(1000)
    page.locator(".inbox-row").first.click()
    page.wait_for_timeout(1400)
    _open_picker(page)
    off = page.evaluate("""() => [...document.querySelectorAll('[data-slack-section]')]
        .filter(c => !c.checked).map(c => c.dataset.slackSection)""")
    assert "takedown" not in off, (
        "an empty section came back switched off — absent because it had "
        "nothing to say has been read as absent because it was deselected")
    assert off == [], f"sections were switched off by a reload: {off}"


def test_a_hand_edit_is_what_gets_sent(page):
    """Typed into the box and pressed post without blurring first. The value
    on screen is the one that goes."""
    page.click("[data-slack-edit]")
    page.keyboard.type("\nHAND EDITED LINE")
    sent = []
    page.route("**/post-rca*", lambda route: (
        sent.append(route.request.post_data),
        route.fulfill(status=200, content_type="application/json",
                      body='{"ok":true,"already_posted":false,"ts":"1",'
                           '"posted_at":"2026-08-02T00:00:00"}')))
    try:
        _post(page)
    finally:
        page.unroute("**/post-rca*")
    assert "HAND EDITED LINE" in json.loads(sent[0])["text"]
    assert "HAND EDITED LINE" in (_draft(page)["slack_thread_override"] or ""), \
        "posting did not save what it posted, so the card and the thread now " \
        "show different things"


# ── a chip splices its own section and leaves manual edits alone (#1) ────────
#
# THE BUG THIS IS THE WHOLE POINT OF. Every chip called a recompose that
# rebuilt the entire post from the RCA fields and overwrote the override, so
# toggling any one section wiped every hand-written line. The behaviour to pin:
# edit the text, toggle a chip, and the edit is still there — a driven test,
# because a splice that quietly regenerates renders identically to one that
# does not until you read the bytes.

def test_a_chip_toggle_keeps_a_manual_edit(page):
    _open_picker(page)
    page.click("[data-slack-sec-all]")     # a known baseline: every section on
    page.wait_for_timeout(400)

    MARK = "MANUAL-EDIT-KEEP-ME-42"
    page.evaluate("""(mark) => {
        const ta = document.querySelector('[data-slack-edit]');
        ta.value = ta.value + String.fromCharCode(10) + mark;
        ta.dispatchEvent(new FocusEvent('blur'));   // the handler that saves
    }""", MARK)
    page.wait_for_timeout(400)

    # toggle ONE section off
    page.click(_chip("wwr"))
    page.wait_for_timeout(500)

    shown = page.evaluate("() => document.querySelector('[data-slack-edit]').value")
    assert MARK in shown, "toggling a chip wiped the manual edit — #1 is not fixed"
    assert "*What went wrong*" not in shown, "the toggled section was not removed"
    # and what actually gets sent (the saved override) carries the edit too
    assert MARK in (_draft(page)["slack_thread_override"] or ""), \
        "the edit was not persisted to the override the post sends"


def test_re_adding_a_section_restores_it_without_touching_the_edit(page):
    """The other half of the splice: turning a section back on inserts only
    that section, at its place, and still leaves the hand edit alone."""
    _open_picker(page)
    page.click("[data-slack-sec-all]")
    page.wait_for_timeout(400)

    MARK = "KEEP-ME-THROUGH-A-READD"
    page.evaluate("""(mark) => {
        const ta = document.querySelector('[data-slack-edit]');
        ta.value = ta.value + String.fromCharCode(10) + mark;
        ta.dispatchEvent(new FocusEvent('blur'));
    }""", MARK)
    page.wait_for_timeout(400)

    page.click(_chip("wwr"))                 # off
    page.wait_for_timeout(400)
    page.click(_chip("wwr"))                 # back on
    page.wait_for_timeout(500)

    shown = page.evaluate("() => document.querySelector('[data-slack-edit]').value")
    assert "*What went wrong*" in shown, "re-adding did not restore the section"
    assert MARK in shown, "re-adding a section wiped the manual edit"
