"""What the RCA column actually renders, in a real browser.

The client is one HTML file with no build step, so every other client test in
this suite is a source assertion — and a source assertion is a spelling check.
Four mutations proved it in one run: dropping the orphan-note branch, dressing
the empty state as a numbered row, and bringing back the "answer" pill all left
the strings in the file and passed.

This boots the app and Chromium once and asserts on the DOM. Skipped when
Playwright or the browser is unavailable, so a machine without them still runs
the rest of the suite rather than reporting a red it cannot fix.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime

import pytest

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
pytest.importorskip("playwright.sync_api", reason="playwright not installed")
if not os.path.exists(CHROME):
    pytest.skip("bundled chromium not present", allow_module_level=True)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


RCA = {
    "stated_issue": "Tickets were late and the guest was not warned.",
    "tldr": {"our_mistake": "No disclosure.", "our_fix": "Refunded."},
    "l1": "Operations Issue", "l2": "Ticket Issues", "sub_themes": ["C. Ticket Delayed"],
    "scenarios": ["Tickets sent late"],
    "what_went_wrong": {"guest_issues": [{
        "issue": "Delivery window not disclosed",
        "claim": "I never heard this before I paid for them.",
        "claim_accuracy": "Accurate", "owner": "Content",
        "root_cause": "The page did not state the window.",
        "evidence": [{"text": "No timeline on the page.", "source": "exp-page",
                      "ref": "https://www.headout.com/tour/22238"}]}]},
    "issue_specific_answers": [
        {"question": "Delivered before the slot?", "verdict": "Yes",
         "evidence": "Sent 15:50.", "source": "zendesk", "ref": "ZD-34011333"},
        {"question": "Reply within SLA?", "verdict": "No",
         "evidence": "28 minutes.", "source": "zendesk", "ref": None}],
    "sop_compliance": {"verdict": "deviated", "expected": "Refund.", "actual": "Delayed.",
                       "detail": None, "zd_ref": None},
    # One note joins a frame; one carries a ref that matches nothing.
    "support_interaction_notes": [
        {"zd_ref": "ZD-34011401", "summary": "Guest chased the voucher.",
         "detail": "Cited a cooking class.", "ce_miss": "No escalation attempted."},
        {"zd_ref": "ZD-99999", "summary": "A contact on no known ticket.",
         "detail": None, "ce_miss": None}],
    "sp_interaction_notes": {"raised": "N/A", "reason": "Vendor is not partnered.", "records": []},
    "booking_logs": [{"time": "22 Jul 15:22", "what": "Booking created", "detail": None}],
    "flags": [{"team": "CONTENT", "flag": "Page does not disclose the window.",
               "evidence": "Internal note only.", "zd_ref": "ZD-34011333"}],
    "area_of_improving": ["Surface the window at checkout."],
    "resolution": "Refunded in full.",
    "suggested_response": "I'm sorry your tickets were late. We have refunded you.",
    "takedown": {"verdict": "No"},
    "dss": {"prescribes": "Refund where tickets were sent too late.", "ref": None},
}

FRAMES = [{"ticket_id": "34011401", "time": "22 Jul 15:41", "time_sort": "2026-07-22T15:41:00",
           "thread": "chat", "guestSaid": "Where are my tickets?", "weDid": "Resent."},
          {"ticket_id": "34011401", "time": "22 Jul 15:44", "time_sort": "2026-07-22T15:44:00",
           "thread": "chat", "guestSaid": "Still nothing.", "weDid": ""}]


@pytest.fixture(scope="module")
def page():
    from playwright.sync_api import sync_playwright
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    url = f"sqlite:///{tmp.name}"
    env = dict(os.environ, DATABASE_URL=url, MOCK_MODE="true")

    seed = f"""
import os, sys, json
sys.path.insert(0, {os.getcwd()!r}); os.chdir({os.getcwd()!r})
os.environ["DATABASE_URL"] = {url!r}
import server.db as db; db.init_db()
from server.services.rca_v4_validate import validate, project_v4
from server.prompts import RCA_PROMPT_VERSION
from datetime import datetime
v, _ = validate(json.loads({json.dumps(json.dumps(RCA))}), ["Tickets sent late"])
s = db.SessionLocal()
s.query(db.RcaDraft).delete(); s.query(db.Review).delete()
s.add(db.Review(id="tp_ui", slack_ts="1", slack_channel="C1", rating=1,
                author="David", body_original="late tickets", status="draft",
                received_at=datetime.utcnow()))
s.add(db.RcaDraft(id="d_ui", review_id="tp_ui", rca_v3=v, booking={{"id": "32908218"}},
                  match_tier=1, rca_prompt_version=RCA_PROMPT_VERSION,
                  confidence_trail=[
                    {{"mark": "pass", "text": "<strong>BID extracted</strong> via attachment"}},
                    {{"mark": "warn", "text": "<strong>RCA</strong> — a coercion fired"}}],
                  generated_at=datetime.utcnow(),
                  support_interaction_frames=json.loads({json.dumps(json.dumps(FRAMES))}),
                  l1=v["l1"], l2=v["l2"], sub_theme="C. Ticket Delayed",
                  actions_taken={{"sp": [], "customer": [], "business": [], "ce": [], "product": []}},
                  **dict(project_v4(v))))
s.commit(); s.close()
"""
    subprocess.run([sys.executable, "-c", seed], check=True, capture_output=True, env=env)

    port = _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.main:app",
                            "--port", str(port), "--log-level", "warning"],
                           env=dict(env, SEED_MOCK="0"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.25)
    else:
        srv.terminate()
        pytest.skip("server did not start")

    pw = sync_playwright().start()
    br = pw.chromium.launch(executable_path=CHROME)
    pg = br.new_page(viewport={"width": 1600, "height": 1200})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
    pg.wait_for_timeout(900)
    pg.locator(".review-item").first.click()
    pg.wait_for_timeout(1400)
    pg.errors = errs
    yield pg
    br.close(); pw.stop(); srv.terminate()
    os.unlink(tmp.name)


def test_the_column_renders_without_a_javascript_error(page):
    assert page.errors == []


# ── defect 4: an absence is never dressed as a data row ─────────────────────

def test_an_empty_guest_support_section_has_no_number_pill_or_time(page):
    """"01 · UNKNOWN · Unknown · No guest contact found" was the deployed
    build. Driven, because the strings sit in the file either way."""
    # Per container: the SP section shares the .interactions class and has its
    # own legitimate empty state, so a page-wide count proves nothing.
    got = page.evaluate("""() => [...document.querySelectorAll('.interactions')].map(el => ({
        frames: el.querySelectorAll('.convo-frame').length,
        empty: el.querySelectorAll(':scope > .interactions-empty').length }))""")
    assert got, "no interactions container rendered"
    for c in got:
        assert not (c["frames"] and c["empty"]), \
            f"a container shows contacts AND the nothing-found line: {c}"


def test_the_empty_branch_itself_carries_no_row_furniture(page):
    """Defect 4 only shows when the section IS empty, which the fixture is not.
    So the data is emptied in-page and the real branch re-rendered — a mutation
    that dresses it as a numbered row survived every test that did not do this.
    """
    html = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = [r.rca.supportFrames, r.rca.supportNotes, r.rca.v3.support_interaction_notes];
      r.rca.supportFrames = []; r.rca.supportNotes = [];
      r.rca.v3.support_interaction_notes = [];
      renderRcaCol();
      const el = [...document.querySelectorAll('.interactions')]
        .find(e => e.querySelector(':scope > .interactions-empty'));
      const out = el ? el.innerHTML : null;
      [r.rca.supportFrames, r.rca.supportNotes, r.rca.v3.support_interaction_notes] = keep;
      renderRcaCol();
      return out; }""")
    assert html, "the empty state did not render when there were no contacts"
    for dressing in ("convo-num", "convo-type-pill", "convo-time", "convo-chevron"):
        assert dressing not in html, f"the empty state renders a {dressing} — that is defect 4"
    assert "never reached support" in html


def test_a_note_cannot_override_the_frames_time_or_channel(page):
    """Facts stay with the pipeline. A note that carries a time — an older
    draft, or a model reaching for the removed field — must not change the
    row."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const n = r.rca.supportNotes[0];
      const keep = {t: n.time, c: n.channel};
      n.time = '09:00'; n.channel = 'call';
      renderRcaCol();
      const f = document.querySelector('.convo-frame');
      const out = {time: f.querySelector('.convo-time').textContent,
                   pill: (f.querySelector('.convo-type-pill')||{}).textContent};
      n.time = keep.t; n.channel = keep.c; renderRcaCol();
      return out; }""")
    assert got["time"] == "22 Jul 15:41", got
    assert got["pill"] == "chat", got


# ── contacts, not events ────────────────────────────────────────────────────

def test_two_events_on_one_ticket_render_as_one_contact(page):
    rows = page.evaluate("""() => [...document.querySelectorAll('.convo-frame')].map(f => ({
        num: (f.querySelector('.convo-num')||{}).textContent,
        pill: (f.querySelector('.convo-type-pill')||{}).textContent || null,
        time: (f.querySelector('.convo-time')||{}).textContent,
        count: (f.querySelector('.convo-count')||{}).textContent || null,
        unverified: !!f.querySelector('.convo-unverified')}))""")
    joined = [r for r in rows if not r["unverified"]]
    assert len(joined) == 1, f"two frames on one ticket became {len(joined)} contacts"
    assert joined[0]["count"] == "2 events"
    assert joined[0]["pill"] == "chat", "the channel must come from the Zendesk frame"
    assert joined[0]["time"] == "22 Jul 15:41"


def test_a_note_whose_reference_matched_nothing_still_renders(page):
    """Dropping it hides a failed join, which looks the same as no notes."""
    rows = page.evaluate("""() => [...document.querySelectorAll('.convo-frame')]
        .filter(f => f.querySelector('.convo-unverified'))
        .map(f => ({why: f.querySelector('.convo-unverified').textContent,
                    text: f.innerText}))""")
    assert len(rows) == 1, "the orphan note was dropped"
    assert "unmatched ZD reference" in rows[0]["why"]
    assert "A contact on no known ticket" in rows[0]["text"]


# ── defect 9: no raw token in a verdict chip ────────────────────────────────

def test_every_issue_specific_answer_uses_the_same_fixed_chip(page):
    chips = page.evaluate("""() => [...document.querySelectorAll('#rca-issue-answers-section select')]
        .map(s => ({w: Math.round(s.getBoundingClientRect().width), v: s.value,
                    opts: [...s.options].map(o=>o.text)}))""")
    assert len(chips) == 2, chips
    assert {c["w"] for c in chips} == {82}, "the ISA chip is not a fixed 82px"
    for c in chips:
        assert c["opts"] == ["Yes", "No", "Unknown"]


def test_no_grey_answer_pill_survives_anywhere(page):
    txt = page.evaluate("() => document.querySelector('#rca-issue-answers-section').innerText")
    assert "answer" not in txt.lower().split(), txt[:200]


# ── the chips that size to content vs the one that is fixed ─────────────────

def test_the_accuracy_and_owner_chips_sit_under_their_caps(page):
    got = page.evaluate("""() => [...document.querySelectorAll('#rca-col select.chip-sel')]
        .map(s => ({cls: s.className, w: Math.round(s.getBoundingClientRect().width)}))""")
    acc = [g for g in got if "chip-acc-sel" in g["cls"]]
    own = [g for g in got if "chip-owner-sel" in g["cls"]]
    assert acc and all(0 < g["w"] <= 140 for g in acc), acc
    assert own and all(0 < g["w"] <= 260 for g in own), own


def test_every_delete_control_is_the_same_grey(page):
    colours = page.evaluate("""() => { const m = {};
        document.querySelectorAll('#rca-col .x-del').forEach(e => {
          const c = getComputedStyle(e).color; m[c] = (m[c] || 0) + 1; }); return m; }""")
    assert len(colours) == 1, colours
    assert "155, 150, 141" in list(colours)[0]


# ── evidence rows ───────────────────────────────────────────────────────────

def test_evidence_renders_as_the_three_column_grid(page):
    got = page.evaluate("""() => [...document.querySelectorAll('#rca-col .ev-row')].map(r => ({
        cols: getComputedStyle(r).gridTemplateColumns,
        rail: r.querySelector('.ev-src').textContent.trim()}))""")
    assert got, "no evidence rows rendered"
    assert got[0]["cols"].startswith("62px "), got[0]["cols"]
    assert got[0]["rail"] == "exp-page"


def test_the_claim_quote_marks_share_a_line_with_the_text(page):
    """Defect 2, checked by geometry rather than by reading the CSS."""
    got = page.evaluate("""() => [...document.querySelectorAll('.wwr-claim-q')].map(q => {
        const ed = q.querySelector('[contenteditable]');
        return ed.getBoundingClientRect().top - q.getBoundingClientRect().top; })""")
    assert got and all(d < 4 for d in got), got


# ── defect 5: an exception is never a trail step ────────────────────────────

RAW_ERR = ("(psycopg2.OperationalError) SSL connection has been closed unexpectedly "
           "[SQL: SELECT rca_drafts.id AS rca_drafts_id, rca_drafts.review_id AS "
           "rca_drafts_review_id FROM rca_drafts WHERE rca_drafts.review_id = "
           "%(review_id_1)s LIMIT %(param_1)s]")


def _inject_failure(page):
    page.evaluate("""(raw) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r._keepTrail = r.confidenceTrail;
      r.confidenceTrail = [...(r.confidenceTrail || []), {
        mark: 'fail', title: 'Run failed — OperationalError',
        text: '<strong>Run failed</strong> — the database connection dropped mid-run. '
            + 'Nothing was saved for this step. Re-run the review.',
        raw: raw}];
      renderReviewCol(); }""", RAW_ERR)


def _restore(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (r._keepTrail) r.confidenceTrail = r._keepTrail;
      state.rawErrOpen = {};
      renderReviewCol(); }""")


def test_a_pipeline_exception_is_not_rendered_inline(page):
    """"✗ Run failed — OperationalError: (psycopg2…) SSL connection…" with 500
    characters of SELECT was the deployed build."""
    _inject_failure(page)
    got = page.evaluate("""() => {
      const s = [...document.querySelectorAll('.conf-step.fail')].pop();
      return {text: s.innerText, toggle: !!s.querySelector('[data-raw-err]')}; }""")
    _restore(page)
    assert "SELECT rca_drafts" not in got["text"], "the SQL is inline in the trail"
    assert "connection dropped mid-run" in got["text"], "no plain-language sentence"
    assert got["toggle"], "the raw error has nowhere to go"


def test_the_raw_error_is_behind_a_toggle_and_capped(page):
    _inject_failure(page)
    page.click(".conf-step.fail [data-raw-err]")
    page.wait_for_timeout(250)
    got = page.evaluate("""() => {
      const raw = [...document.querySelectorAll('.err-raw')].pop();
      if (!raw) return null;
      const cs = getComputedStyle(raw);
      return {mono: /mono|Menlo|SFMono|Consolas/i.test(cs.fontFamily),
              size: cs.fontSize, maxh: cs.maxHeight, overflow: cs.overflowY,
              wrap: cs.overflowWrap, sql: raw.innerText.includes('SELECT rca_drafts')}; }""")
    _restore(page)
    assert got, "the toggle did not reveal the raw error"
    assert got["mono"] and got["size"] == "10.5px"
    assert got["maxh"] == "120px" and got["overflow"] == "auto"
    assert got["wrap"] == "anywhere", "a long SQL line will push the panel apart"
    assert got["sql"], "the raw error was discarded rather than hidden"


def test_a_normal_trail_step_grows_no_toggle(page):
    """Only a step that carries a raw error gets one."""
    got = page.evaluate("""() => [...document.querySelectorAll('.conf-step')]
        .filter(s => !s.classList.contains('fail'))
        .every(s => !s.querySelector('[data-raw-err]'))""")
    assert got


# ── §14 the RCA header is one row ───────────────────────────────────────────

def test_the_rca_header_is_a_single_baseline_row(page):
    got = page.evaluate("""() => {
      const h = document.querySelector('#rca-col .rca-head');
      const t = h.querySelector('.rca-title'), s = h.querySelector('.rca-sub');
      return {same_line: Math.abs(t.getBoundingClientRect().top
                                  - s.getBoundingClientRect().top) < 4,
              height: Math.round(h.getBoundingClientRect().height),
              title: h.getAttribute('title') || ''}; }""")
    assert got["same_line"], "the label and sub-line are still stacked"
    assert got["height"] < 60, got["height"]
    assert "paste" in got["title"], "the dropped clause is not in the row's title"


# ── §13 the two timelines trade columns ─────────────────────────────────────

def _col(page, sel):
    return page.evaluate("""(sel) => { const el = document.querySelector(sel);
        if (!el) return null;
        return el.closest('#rca-col') ? 'rca' : 'facts'; }""", sel)


def test_the_booking_timeline_is_in_the_facts_column(page):
    """System-of-record data — created, fulfilment, sent, refunded — which is
    what the facts column is for."""
    assert _col(page, "#rca-booking-logs-section") == "facts"


def test_the_events_timeline_is_in_the_rca_column(page):
    """The interpreted guest journey, beside the analysis that cites it."""
    assert _col(page, "#rca-events-timeline-section") == "rca"


def test_the_booking_timeline_keeps_its_rows_and_controls(page):
    """No treatment change; only position. A move that drops the editable rows
    or the add button has swapped the section for a picture of it."""
    got = page.evaluate("""() => {
      const s = document.querySelector('#rca-booking-logs-section');
      return {rows: s.querySelectorAll('.tl-row').length,
              editables: s.querySelectorAll('[contenteditable]').length,
              dels: s.querySelectorAll('[data-log-del]').length,
              add: !!s.querySelector('[data-log-add]')}; }""")
    assert got["rows"] and got["editables"] and got["dels"] and got["add"], got


def test_add_event_still_works_from_the_column_it_moved_to(page):
    """The handlers were bound inside renderRcaCol. Moving the markup without
    moving them leaves a live-looking button that does nothing."""
    before = page.evaluate("() => document.querySelectorAll('#rca-booking-logs-section .tl-row').length")
    page.click("[data-log-add]")
    page.wait_for_timeout(700)
    after = page.evaluate("() => document.querySelectorAll('#rca-booking-logs-section .tl-row').length")
    assert after == before + 1, f"{before} -> {after}"
    page.click("#rca-booking-logs-section [data-log-del]:last-of-type")
    page.wait_for_timeout(600)


# ── step 6 the Slack post ───────────────────────────────────────────────────

def _post(page):
    return page.evaluate("""() => { const t = document.querySelector('[data-slack-edit]');
        return t ? t.value : ''; }""")


def test_the_post_has_one_block_per_guest_issue(page):
    txt = _post(page)
    i = txt.find("*What went wrong*")
    assert "*1. Delivery window not disclosed*" in txt, txt[i:i + 400]
    assert "·  Accurate" in txt and "·  Content" in txt, txt[i:i + 400]


def test_a_block_carries_only_the_lines_that_issue_has(page):
    """A dash for every absent field turns a focused block into a form with
    blanks in it."""
    txt = _post(page)
    block = txt.split("*1. Delivery window not disclosed*")[1].split("\n\n")[0]
    assert "Root cause:" in block
    assert "SOP / process gap:" not in block, "an absent line was printed anyway"


def test_evidence_keeps_its_source_and_reference_in_the_post(page):
    assert "- [exp-page] No timeline on the page. (https://www.headout.com/tour/22238)" in _post(page)


def test_the_guest_reply_is_never_in_the_post(page):
    """The RCA thread is internal; the reply goes to Trustpilot by hand. It is
    a field that would look entirely plausible in the output."""
    txt = _post(page)
    reply = page.evaluate(
        "() => (REVIEWS.find(x=>x.id===state.selected).rca.v3||{}).suggested_response || ''")
    assert reply, "the fixture has no reply, so this proves nothing"
    assert reply[:40] not in txt
    assert "refunded you" not in txt


def test_an_unclassified_review_posts_no_issue_line(page):
    """Defect 6: "Issue: — / —" reached the thread and read as a broken
    generator rather than as an unclassified review."""
    txt = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = [r.rca.issueL1, r.rca.issueL2, r.rca.subTheme,
                    r.rca.primaryScenario, r.rca.overlayScenarios, r.rca.slackThreadOverride];
      r.rca.issueL1 = ''; r.rca.issueL2 = ''; r.rca.subTheme = '';
      r.rca.primaryScenario = ''; r.rca.overlayScenarios = [];
      r.rca.slackThreadOverride = '';
      renderRcaCol();
      const out = (document.querySelector('[data-slack-edit]') || {}).value || '';
      [r.rca.issueL1, r.rca.issueL2, r.rca.subTheme,
       r.rca.primaryScenario, r.rca.overlayScenarios, r.rca.slackThreadOverride] = keep;
      renderRcaCol();
      return out; }""")
    assert "Issue:" not in txt, txt[:300]
    assert "— / —" not in txt


def test_editing_a_booking_log_row_persists(page):
    """The rows are editable in their new column too. A move that keeps the
    contenteditable but loses its save handler looks identical on screen —
    the text stays until you reload."""
    new_text = "Booking created (edited in a test)"
    page.evaluate("""(t) => {
      const el = document.querySelector('#rca-booking-logs-section [data-log-field="what"]');
      el.focus(); el.textContent = t;
      el.dispatchEvent(new FocusEvent('blur', {bubbles: true})); }""", new_text)
    page.wait_for_timeout(900)
    saved = page.evaluate("""async () => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const res = await fetch('/api/reviews/' + r.id);
      const d = await res.json();
      const logs = ((d.draft || {}).rca_v3 || {}).booking_logs || [];
      return logs.map(l => l && l.what); }""")
    assert new_text in saved, saved


# ── §6 the Slack section picker ─────────────────────────────────────────────

def _picker(page):
    return page.evaluate("""() => { const s = document.querySelector('.slack-sections');
        return {chips: s.querySelectorAll('.slack-sec-chip').length,
                summary: (s.querySelector('.slack-sections-summary')||{}).textContent || '',
                btn: (s.querySelector('[data-slack-customize]')||{}).textContent || ''}; }""")


def test_the_section_picker_is_collapsed_by_default(page):
    """Twelve checkboxes were a wall of clutter above the post itself."""
    got = _picker(page)
    assert got["chips"] == 0, "the chips are showing before anyone asked"
    assert "12 of 12 sections included" in got["summary"]
    assert got["btn"] == "customize"


def test_customize_reveals_the_chips_and_done_collapses_them(page):
    page.click("[data-slack-customize]")
    page.wait_for_timeout(400)
    assert _picker(page)["chips"] == 12
    assert _picker(page)["btn"] == "done"
    page.click("[data-slack-customize]")
    page.wait_for_timeout(400)
    assert _picker(page)["chips"] == 0


def test_the_collapsed_line_still_states_the_current_count(page):
    """A collapsed picker must not hide that sections are switched off."""
    page.click("[data-slack-customize]")
    page.wait_for_timeout(300)
    page.click(".slack-sec-chip:has(input[data-slack-section='insights'])")
    page.wait_for_timeout(600)
    assert "11 of 12" in _picker(page)["summary"]
    page.click("[data-slack-customize]")
    page.wait_for_timeout(400)
    got = _picker(page)
    assert got["chips"] == 0 and "11 of 12" in got["summary"]
    # restore
    page.click("[data-slack-customize]"); page.wait_for_timeout(300)
    page.click(".slack-sec-chip:has(input[data-slack-section='insights'])")
    page.wait_for_timeout(500)
    page.click("[data-slack-customize]"); page.wait_for_timeout(300)


# ── §4 exactly two empty-state treatments ───────────────────────────────────

def test_an_affirmative_empty_is_not_dressed_as_a_neutral_one(page):
    """"Every QA area was checked and none needed raising" is a result someone
    verified. In the same grey italic as "nothing here" that reading is lost."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.rca.v3.flags;
      r.rca.v3.flags = []; renderRcaCol();
      const e = document.querySelector('#rca-flags-section .rca-empty');
      const out = e ? {text: e.innerText, colour: getComputedStyle(e).color,
                       italic: getComputedStyle(e).fontStyle === 'italic'} : null;
      r.rca.v3.flags = keep; renderRcaCol();
      return out; }""")
    assert got, "the flags empty state did not render"
    assert got["text"].startswith("✓")
    assert "47, 122, 77" in got["colour"], got["colour"]
    assert not got["italic"]


def test_amber_stays_reserved_for_a_broken_pipeline(page):
    """A third meaning. It must never share styling with either empty state."""
    got = page.evaluate("""() => {
      const a = document.createElement('div'); a.className = 'rca-empty affirm';
      const b = document.createElement('div'); b.className = 'rca-empty err';
      const c = document.createElement('div'); c.className = 'rca-empty';
      document.body.append(a, b, c);
      const out = [a, b, c].map(e => getComputedStyle(e).color);
      a.remove(); b.remove(); c.remove(); return out; }""")
    assert len(set(got)) == 3, f"the three meanings share a colour: {got}"


# ── §12 SP interaction reads the split key ──────────────────────────────────

def test_the_sp_section_renders_from_the_notes_key(page):
    """Reading the pre-split key left the whole section blank after the rename
    — a lookup that could not say it had found nothing."""
    got = page.evaluate("""() => {
      const s = [...document.querySelectorAll('.sp-frame')];
      return {frames: s.length, text: s.map(x => x.innerText).join(' ')}; }""")
    assert got["frames"], "the SP section rendered nothing"
    reason = page.evaluate(
        "() => ((REVIEWS.find(x=>x.id===state.selected).rca.v3||{})"
        ".sp_interaction_notes||{}).reason || ''")
    assert reason, "the fixture has no reason, so this proves nothing"
    assert reason[:20] in got["text"], \
        "raised: N/A with no reason is indistinguishable from a skipped section"
