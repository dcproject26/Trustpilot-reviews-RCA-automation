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

from tests.test_rca_ui_rendered import page, CHROME, _rca_tab   # noqa: E402,F401


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


# ── the Slack post: one row per section, carries only the chosen sections ────
#
# The old chip picker + single textarea became one editable row per section
# (handoff §slack). The store is unchanged — a hidden [data-slack-edit] mirror
# still holds the composed text every consumer reads — so the value assertions
# below still read `.value` off it. What changed is how a section is turned off
# (leave out / put back, not a checkbox) and how a body is edited (in its row,
# not in one big box).

def _open_slack(page):
    # The Slack post lives in the Slack tab (handoff §1); show it first. Also
    # re-selects it after a reload, which resets the active tab to Diagnosis.
    _rca_tab(page, "slack")
    page.wait_for_timeout(150)


def _mirror(page):
    return page.evaluate(
        "() => (document.querySelector('[data-slack-edit]') || {}).value || ''")


def _drop(page, key):
    """Leave a section out. The 'leave out' control is hover-gated (handoff:
    .spost-acts is shown on row hover / focus-within), so reveal it first."""
    page.hover(f'.spost-row:has([data-slack-drop="{key}"])')
    page.wait_for_timeout(50)
    page.click(f'[data-slack-drop="{key}"]')
    page.wait_for_timeout(400)


def _restore(page, key):
    """Put a left-out section back."""
    page.click(f'[data-slack-restore="{key}"]')
    page.wait_for_timeout(400)


def _row_state(page):
    """Map section key -> 'in' (editable) | 'out' (left out). Nothing-to-post
    rows carry no key and are omitted — absent-because-empty is not a state a
    toggle can produce."""
    return page.evaluate("""() => {
      const out = {};
      document.querySelectorAll('.spost-row').forEach(row => {
        const drop = row.querySelector('[data-slack-drop]');
        const rest = row.querySelector('[data-slack-restore]');
        if (drop) out[drop.dataset.slackDrop] = 'in';
        else if (rest) out[rest.dataset.slackRestore] = 'out';
      });
      return out; }""")


def _post(page):
    """Click Post twice — the button is a two-step confirm on EVERY send.

    It used to confirm only a REPEAT post; a first post went to the channel on
    one click, from a button sitting directly under nine contenteditable rows.
    A Slack post cannot be recalled, so the guard is on every send now, with
    two different sentences for the two different risks ("this is going to the
    channel" / "there is already a copy in the thread").

    Tests that click once get no request at all, and that reads as a silent
    failure of the thing under test rather than of the test — so this asserts
    the button really did arm rather than clicking twice and hoping.
    """
    page.click("[data-slack-post]")
    page.wait_for_timeout(300)
    armed = page.locator("[data-slack-post]").text_content() or ""
    assert "Confirm" in armed or "second copy" in armed, (
        f"the first click did not arm the post button: {armed!r}")
    page.click("[data-slack-post]")
    page.wait_for_timeout(600)


def test_the_post_renders_one_row_per_section_not_a_wall_of_text(page):
    """THE UI CHANGE. The old build put the whole post in one textarea behind a
    chip picker; the redesign is one row per section. Asserted by driving: the
    section rows render and are editable, and the old edit surface is gone as a
    VISIBLE control (it survives only as a hidden mirror)."""
    _open_slack(page)
    rows = page.locator(".spost-row").count()
    assert rows >= 3, f"the per-section rows did not render: {rows}"
    editable = page.locator(".spost-body[contenteditable='true']").count()
    assert editable >= 1, "no section body is editable in place"
    # the mirror still exists as the store, but hidden — not a visible editor
    vis = page.evaluate("""() => {
      const t = document.querySelector('[data-slack-edit]');
      if (!t) return 'absent';
      return t.offsetParent === null ? 'hidden' : 'visible'; }""")
    assert vis == "hidden", f"the composed-text box is {vis}, should be a hidden mirror"
    assert page.locator("[data-slack-customize]").count() == 0, \
        "the old 'customize' chip toggle is still on the card"


def test_deselected_sections_do_not_reach_slack(page):
    """The reported bug, end to end: the request body is inspected."""
    _open_slack(page)
    page.click("[data-slack-sec-none]")
    page.wait_for_timeout(500)
    _restore(page, "insights")

    shown = _mirror(page)
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
    _open_slack(page)
    page.click("[data-slack-sec-none]")
    page.wait_for_timeout(500)
    _restore(page, "resolution")

    shown = _mirror(page)
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
    """It lived in a JavaScript object. Reload and every section came back on
    while the saved post still had the others missing — the rows and the post
    disagreeing, with no way to tell which was real."""
    _open_slack(page)
    page.click("[data-slack-sec-none]")
    page.wait_for_timeout(500)
    _restore(page, "insights")
    before = _mirror(page)

    page.reload(wait_until="load")
    page.wait_for_timeout(1000)
    page.locator(".inbox-row").first.click()
    page.wait_for_timeout(1400)

    after = _mirror(page)
    assert after.strip() == before.strip(), \
        "the saved post did not survive the reload"
    _open_slack(page)
    state = _row_state(page)
    on = sorted(k for k, v in state.items() if v == "in")
    assert on == ["insights"], \
        f"the rows do not match the post that is actually stored: {on}"


def test_an_empty_section_is_not_read_back_as_a_deselection(page):
    """The selection is derived from the saved post by looking for each
    section's heading. A section the composer skipped because it had nothing to
    say has no heading either — and reading that as "the associate switched it
    off" would silently turn a section off for good, one reload at a time.

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

    _open_slack(page)
    page.click("[data-slack-sec-all]")
    page.wait_for_timeout(700)
    saved = _draft(page)["slack_thread_override"] or ""
    assert "*Review takedown*" not in saved, \
        "the fixture is not empty here, so this test would prove nothing"
    # an empty section renders as a quiet 'nothing to post' line, never a row
    # with a leave-out/put-back toggle
    assert page.locator('[data-slack-drop="takedown"], [data-slack-restore="takedown"]').count() == 0, \
        "an empty section rendered a toggle — it should be 'nothing to post'"

    page.reload(wait_until="load")
    page.wait_for_timeout(1000)
    page.locator(".inbox-row").first.click()
    page.wait_for_timeout(1400)
    _open_slack(page)
    state = _row_state(page)
    off = sorted(k for k, v in state.items() if v == "out")
    assert "takedown" not in off, (
        "an empty section came back switched off — absent because it had "
        "nothing to say has been read as absent because it was deselected")
    assert off == [], f"sections were switched off by a reload: {off}"


def test_an_action_field_edit_is_saved(page):
    """Actions taken, rebuilt to the Slack one-row-per-section layout: folks
    edit these by hand, so a row-field edit must auto-save on blur — the same
    guarantee as the Slack rows. Each editable carries data-af; the blur handler
    writes that field and persists via actions_taken."""
    _rca_tab(page, 'actions')
    page.click('[data-add-action]')           # guarantee a row to edit
    page.wait_for_timeout(300)
    MARK = "ACTION-CONTEXT-EDIT-77"
    page.evaluate("""(m) => {
        const el = document.querySelector('.act-row [data-af="context"]');
        el.focus();
        el.textContent = m;
        el.dispatchEvent(new FocusEvent('blur'));
    }""", MARK)
    page.wait_for_timeout(500)
    saved = json.dumps(_draft(page)["actions_taken"] or {})
    assert MARK in saved, "the action field edit did not auto-save on blur"


def test_a_hand_edit_in_a_row_is_saved_and_is_what_gets_sent(page):
    """Auto-save is load-bearing here — folks edit these bodies by hand every
    day. Edit a section's body, and it must (a) persist on blur and (b) be what
    the post carries, even when Post is clicked from the edit without an
    explicit blur first."""
    _open_slack(page)
    MARK = "HAND-EDITED-ROW-LINE"
    # edit the resolution row's body and blur → auto-save
    page.evaluate("""(m) => {
        const el = document.querySelector('[data-slack-sec-body="resolution"]')
                || document.querySelector('[data-slack-sec-body]');
        el.focus();
        el.innerText = el.innerText + String.fromCharCode(10) + m;
        el.dispatchEvent(new FocusEvent('blur'));
    }""", MARK)
    page.wait_for_timeout(500)
    assert MARK in (_draft(page)["slack_thread_override"] or ""), \
        "the row edit did not auto-save on blur — the thing we cannot afford to lose"

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
    assert MARK in json.loads(sent[0])["text"], \
        "the hand edit did not reach the post body"


def test_the_post_button_captures_an_unblurred_row_edit(page):
    """Clicking Post must send the text on screen even if the row was never
    blurred — the post handler recomposes from the rows first."""
    _open_slack(page)
    MARK = "UNBLURRED-EDIT-99"
    # mutate the body WITHOUT dispatching blur, so only the recompose-on-post
    # can capture it
    page.evaluate("""(m) => {
        const el = document.querySelector('[data-slack-sec-body="resolution"]')
                || document.querySelector('[data-slack-sec-body]');
        el.innerText = el.innerText + String.fromCharCode(10) + m;
    }""", MARK)
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
    assert MARK in json.loads(sent[0])["text"], \
        "an unblurred row edit was dropped from the post"


# ── leaving a section out splices it and leaves manual edits alone (#1) ──────
#
# THE BUG THIS IS THE WHOLE POINT OF. A toggle used to rebuild the entire post
# from the RCA fields and overwrite the override, so toggling any one section
# wiped every hand-written line. The behaviour to pin: edit a body, leave a
# DIFFERENT section out, and the edit is still there — a driven test, because a
# splice that quietly regenerates renders identically to one that does not until
# you read the bytes.

def test_leaving_a_section_out_keeps_a_manual_edit(page):
    _open_slack(page)
    page.click("[data-slack-sec-all]")     # a known baseline: every section on
    page.wait_for_timeout(400)

    MARK = "MANUAL-EDIT-KEEP-ME-42"
    page.evaluate("""(m) => {
        const el = document.querySelector('[data-slack-sec-body="resolution"]');
        el.focus();
        el.innerText = el.innerText + String.fromCharCode(10) + m;
        el.dispatchEvent(new FocusEvent('blur'));
    }""", MARK)
    page.wait_for_timeout(400)

    _drop(page, "wwr")                       # leave a DIFFERENT section out

    shown = _mirror(page)
    assert MARK in shown, "leaving a section out wiped the manual edit — #1 is not fixed"
    assert "*What went wrong*" not in shown, "the section left out was not removed"
    assert MARK in (_draft(page)["slack_thread_override"] or ""), \
        "the edit was not persisted to the override the post sends"


def test_putting_a_section_back_restores_it_without_touching_the_edit(page):
    """The other half of the splice: putting a section back inserts only that
    section, at its place, and still leaves the hand edit alone."""
    _open_slack(page)
    page.click("[data-slack-sec-all]")
    page.wait_for_timeout(400)

    MARK = "KEEP-ME-THROUGH-A-READD"
    page.evaluate("""(m) => {
        const el = document.querySelector('[data-slack-sec-body="resolution"]');
        el.focus();
        el.innerText = el.innerText + String.fromCharCode(10) + m;
        el.dispatchEvent(new FocusEvent('blur'));
    }""", MARK)
    page.wait_for_timeout(400)

    _drop(page, "wwr")                       # out
    _restore(page, "wwr")                    # back on

    shown = _mirror(page)
    assert "*What went wrong*" in shown, "putting it back did not restore the section"
    assert MARK in shown, "putting a section back wiped the manual edit"
