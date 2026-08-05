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
import re
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
        "claim_accuracy": "Accurate",
        # Owner rides the FIX now, per the what_went_wrong spec. Left at the
        # top level it does not survive projection, and the Slack post loses
        # the team beside the verdict — which is the line leadership scans.
        "fix": {"action": "Add the two-hour delivery window to the page",
                "owner": "Content",
                "because": "The experience page states no delivery window",
                "source": "exp-page",
                },
        "root_cause": "The page did not state the window.",
        # Three refs, three shapes, because the renderer treats them
        # differently and only one of them used to work: a URL, a ZD id (which
        # went into href= verbatim and produced a dead relative link), and a
        # plain string that is neither.
        "evidence": [{"text": "No timeline on the page.", "source": "exp-page",
                      "ref": "https://www.headout.com/tour/22238"},
                     {"text": "CE told the guest two hours.", "source": "zendesk",
                      "ref": "ZD-34011333"},
                     {"text": "41 negative reviews in the window.",
                      "source": "insights", "ref": "90 days before 2026-08-04"}]}]},
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
                  zendesk_ticket_ids=["34125496", "34256902"],
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


# "where is this evidence coming from" — the rail names a SYSTEM; without the
# ref the reader has been told where to look and not what to open.

def test_a_zendesk_reference_becomes_a_working_ticket_link(page):
    """It went into href= verbatim. `ZD-34011333` is not a URL, so the browser
    resolved it against the dashboard's own path and produced a link to a page
    that does not exist — a reference the model DID supply looked exactly like
    one it had not."""
    got = page.evaluate("""() => [...document.querySelectorAll('#rca-col .ev-row a')]
        .map(a => a.getAttribute('href'))""")
    zd = [h for h in got if "34011333" in (h or "")]
    assert zd, f"no link to the ZD reference in the evidence rows: {got}"
    assert zd[0].startswith("https://") and "zendesk.com/agent/tickets/34011333" in zd[0], \
        f"the ZD reference is not a real ticket url: {zd[0]!r}"


def test_a_reference_that_is_not_a_url_is_still_shown(page):
    """The insights window a count covers is the thing that changes underneath
    it, and dropping it left a number with no range attached."""
    got = page.evaluate("""() => [...document.querySelectorAll('#rca-col .ev-ref')]
        .map(e => e.textContent.trim())""")
    assert any("90 days before" in t for t in got), \
        f"the insights window was dropped rather than shown: {got}"


def test_every_evidence_row_that_has_a_ref_shows_it(page):
    """Not a sample. A ref silently dropped for one source shape is the same
    bug as dropping all of them, discovered later."""
    got = page.evaluate("""() => [...document.querySelectorAll('#rca-col .ev-row')]
        .map(r => ({rail: r.querySelector('.ev-src').textContent.trim(),
                    shown: r.textContent}))""")
    for row in got:
        if row["rail"] == "exp-page":
            continue          # its ref is a url, carried on the rail's own href
        assert "ZD-34011333" in row["shown"] or "90 days before" in row["shown"] \
            or row["rail"] == "—", f"{row['rail']} row shows no reference"


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


def test_the_fix_object_is_written_out_rather_than_stringified(page):
    """`fix` is an object now. Left in the generic label loop beside root_cause
    and pattern it stringified to "[object Object]" and went out on the post —
    the action, the owner, the gap it closes and the count that sized it all
    replaced by seven characters of nothing. It reached a real post and
    surfaced only when an unrelated fixture moved, so it is worth pinning from
    both ends: the parts are present, and the stringification is absent.
    """
    txt = _post(page)
    assert "[object Object]" not in txt, \
        txt[txt.find("*What went wrong*"):][:600]
    assert "• Fix: Add the two-hour delivery window to the page (owner: Content)" \
        in txt, txt[txt.find("*What went wrong*"):][:600]
    assert "- closes: The experience page states no delivery window" in txt


def test_an_issue_carries_one_fix_line(page):
    """Two fix lines for one fix is the same defect wearing a valid string:
    the object path and a fallback path both firing, so the post says the fix
    twice and the reader cannot tell which one the pipeline believes."""
    txt = _post(page)
    block = txt.split("*1. Delivery window not disclosed*")[1].split("\n\n")[0]
    assert block.count("• Fix:") == 1, block


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
    assert " sections included" in got["summary"]
    assert got["btn"] == "customize"


def _n_sections(page):
    """How many sections the composer publishes.

    Derived, not hardcoded. It was 12; TL;DR and SOP compliance were removed
    from the RCA and it became 10, and three tests failed on the number rather
    than on the behaviour they were about. The composer is the one definition
    of the list, so ask it.
    """
    m = re.search(r"of (\d+) sections included", _picker(page)["summary"] or "")
    assert m, f"the picker does not state a total: {_picker(page)['summary']!r}"
    return int(m.group(1))


def test_customize_reveals_the_chips_and_done_collapses_them(page):
    n = _n_sections(page)
    assert n, "the composer published no section list"
    page.click("[data-slack-customize]")
    page.wait_for_timeout(400)
    assert _picker(page)["chips"] == n
    assert _picker(page)["btn"] == "done"
    page.click("[data-slack-customize]")
    page.wait_for_timeout(400)
    assert _picker(page)["chips"] == 0


def test_the_collapsed_line_still_states_the_current_count(page):
    """A collapsed picker must not hide that sections are switched off."""
    n = _n_sections(page)
    page.click("[data-slack-customize]")
    page.wait_for_timeout(300)
    page.click(".slack-sec-chip:has(input[data-slack-section='insights'])")
    page.wait_for_timeout(600)
    assert f"{n - 1} of {n}" in _picker(page)["summary"]
    page.click("[data-slack-customize]")
    page.wait_for_timeout(400)
    got = _picker(page)
    assert got["chips"] == 0 and f"{n - 1} of {n}" in got["summary"]
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


# ── an empty section says which kind of empty it is ─────────────────────────
#
# One card carried three empty sections at once, each asserting a fact about
# the review that nothing had established: "Nothing was extracted" above a full
# two-issue RCA, "No Zendesk events were found" for a lookup whose outcome was
# unknown, and "? / ?" where a classification should have been.

def _events(page, events, ticket_ids=None):
    """Render the events timeline over a given event list and read it back."""
    return page.evaluate("""([events, tids]) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keepE = r.events, keepT = r.zendeskTicketIds;
      r.events = events; r.zendeskTicketIds = tids || [];
      renderRcaCol();
      const s = document.querySelector('#rca-events-timeline-section');
      const out = {text: s.innerText,
                   rows: s.querySelectorAll('.tl-event').length,
                   toggle: !!s.querySelector('[data-tl-toggle]')};
      r.events = keepE; r.zendeskTicketIds = keepT; renderRcaCol();
      return out; }""", [events, ticket_ids])


INTERNAL = [{"time": "22 Jul 15:41", "actor": "system", "label": "Auto-tagged",
             "is_internal": True, "internal_reason": "automation"},
            {"time": "22 Jul 15:42", "actor": "system", "label": "SLA clock started",
             "is_internal": True, "internal_reason": "automation"}]


def test_events_hidden_by_the_filter_are_not_reported_as_absent(page):
    """Every event on the booking is Headout machinery, so the internal filter
    empties the list. The panel said "No Zendesk events were found for this
    booking" — false — and dropped the toggle that reveals them, because the
    toggle is appended to the events string this branch never renders."""
    got = _events(page, INTERNAL)
    assert "No Zendesk events were found" not in got["text"], \
        "two hidden events were reported as no events"
    assert "2" in got["text"] and "internal" in got["text"].lower()
    assert got["toggle"], "no way to reveal the events it just admitted to having"


def test_the_hidden_events_can_actually_be_revealed(page):
    """A toggle that renders and does nothing is the same bug one layer down."""
    page.evaluate("""(events) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r._keepE = r.events; r.events = events; renderRcaCol(); }""", INTERNAL)
    page.click("#rca-events-timeline-section [data-tl-toggle]")
    page.wait_for_timeout(250)
    shown = page.evaluate(
        "() => document.querySelectorAll('#rca-events-timeline-section .tl-event').length")
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r.events = r._keepE; state.tlShowInternal = false; renderRcaCol(); }""")
    assert shown == 2, f"the toggle revealed {shown} of 2 internal events"


def test_tickets_that_matched_but_carried_no_events_are_named(page):
    """"No events were found" sends the reader nowhere. Two matched tickets is
    something they can open."""
    got = _events(page, [], ["4491", "4502"])
    assert "2 tickets matched" in got["text"], got["text"]


def test_a_genuinely_empty_timeline_points_at_the_trail(page):
    """No tickets, no events. The panel cannot know whether Zendesk was
    searched, so it must not claim it was."""
    got = _events(page, [], [])
    assert "No Zendesk events were found" in got["text"]
    assert "confidence trail" in got["text"], \
        "an empty timeline still asserts the lookup ran"


def test_an_empty_stated_issue_beside_a_full_rca_does_not_blame_the_review(page):
    """"Nothing was extracted" is a claim about the review. With issues on the
    same screen, drawn from the same text, it is provably false."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = r.statedIssue; r.statedIssue = ''; renderRcaCol();
      const e = document.querySelector('.stated-issue .rca-empty');
      const out = {text: e ? e.innerText : null,
                   issues: ((r.rca.v3.what_went_wrong||{}).guest_issues||[]).length};
      r.statedIssue = keep; renderRcaCol(); return out; }""")
    assert got["issues"] >= 1, "the fixture has no issues, so this proves nothing"
    assert got["text"], "the empty stated issue rendered no explanation at all"
    assert "Nothing was extracted" not in got["text"], got["text"]
    assert "the step that failed" in got["text"]
    assert str(got["issues"]) in got["text"], \
        "it does not say how many issues contradict it"


def test_an_empty_stated_issue_with_an_empty_rca_still_points_somewhere(page):
    """The other branch: nothing on screen contradicts it, so the honest line
    is that the panel does not know — not a claim about the review."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const kS = r.statedIssue, kW = r.rca.v3.what_went_wrong;
      r.statedIssue = ''; r.rca.v3.what_went_wrong = {guest_issues: []};
      renderRcaCol();
      const e = document.querySelector('.stated-issue .rca-empty');
      const out = e ? e.innerText : null;
      r.statedIssue = kS; r.rca.v3.what_went_wrong = kW; renderRcaCol();
      return out; }""")
    assert got and "confidence trail" in got, got


MIXED = [{"time": "22 Jul 15:40", "actor": "guest", "label": "Guest wrote in",
          "thread": "email", "summary": "Where are my tickets?"},
         {"time": "22 Jul 15:41", "actor": "system", "label": "Auto-tagged",
          "is_internal": True, "internal_reason": "automation"}]


def test_the_machinery_toggle_works_on_a_normal_timeline(page):
    """The binding was queried off review-col and called renderReviewCol. §13
    moved the timeline to the RCA column and left both behind, so the control
    rendered and did nothing — for every timeline, not just the all-internal
    one. A dead control looks exactly like a working one until it is clicked."""
    page.evaluate("""(events) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r._keepE = r.events; r.events = events;
      state.tlShowInternal = false; renderRcaCol(); }""", MIXED)
    before = page.evaluate(
        "() => document.querySelectorAll('#rca-events-timeline-section .tl-event').length")
    page.click("#rca-events-timeline-section [data-tl-toggle]")
    page.wait_for_timeout(250)
    after = page.evaluate(
        "() => document.querySelectorAll('#rca-events-timeline-section .tl-event').length")
    note = page.evaluate(
        "() => document.querySelector('#rca-events-timeline-section [data-tl-toggle]').innerText")
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r.events = r._keepE; state.tlShowInternal = false; renderRcaCol(); }""")
    assert before == 1, f"the filter showed {before} of 1 guest event"
    assert after == 2, f"clicking show revealed {after} of 2 events"
    assert "hide" in note.lower(), f"the toggle does not offer the way back: {note!r}"


def test_the_ticket_ids_survive_the_trip_from_the_draft(page):
    """The renderer tests set r.zendeskTicketIds by hand, so they proved the
    branch renders and nothing about the wiring that feeds it — dropping the
    `draft.zendesk_ticket_ids` read entirely left every one of them green.
    This drives the real ingest path against the real payload."""
    got = page.evaluate("""async () => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r.zendeskTicketIds = null;                       // wipe what is there
      await loadDraftOverlays();                        // the real mapping
      return REVIEWS.find(x => x.id === state.selected).zendeskTicketIds; }""")
    assert got == ["34125496", "34256902"], \
        f"the ticket ids did not reach the renderer: {got!r}"


def test_the_ticket_ids_the_api_sends_are_the_ones_the_draft_holds(page):
    """Both halves of the wire, so a rename on either side is caught here
    rather than as a silently empty section."""
    sent = page.evaluate("""async () => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const p = await (await fetch(`/api/reviews/${r.id}`)).json();
      return p.draft.zendesk_ticket_ids; }""")
    assert sent == ["34125496", "34256902"], sent


# ── which build is this page talking to ─────────────────────────────────
#
# These three lived in a SECOND copy of this whole file that had been
# concatenated onto the front of it. Every other test in that copy was
# shadowed by its twin below - same names, later definition wins - so 587
# lines of tests were being collected as source and executed as nothing.
# pytest reported 45 tests either way, which is the reason it survived: a
# suite that runs half of what it contains reports exactly like one that
# runs all of it. Only these three had no twin, so only these three ran.
def test_the_page_stamps_the_build_it_is_talking_to(page):
    """"The dashboard looks like the previous version" is unanswerable from the
    browser without this: a stale page, a server never restarted after a pull,
    and a failed deploy all look identical."""
    got = page.evaluate("() => document.body.dataset.build || ''")
    assert got and got != "unknown", got
    assert "dev" in got or "deployment" in got, got


def test_a_stale_build_says_so_loudly(page):
    """Quiet when current, loud when not — that is the case someone has to act
    on, and it must not be discoverable only by opening the console.

    The endpoint is intercepted and the page reloaded, so the REAL branch runs.
    A first version of this test built the bar itself and asserted on its own
    handiwork: deleting the `if (v.stale)` branch outright left it green.
    """
    stale = json.dumps({"commit": "a" * 40, "short": "aaaaaaa",
                        "fingerprint": "ff", "db": {},
                        "on_disk": "bbbbbbbbccccccc", "stale": True,
                        "environment": "dev", "reload": False,
                        "started_at": "2026-07-31T00:00:00", "uptime_s": 5})
    page.route("**/api/version",
               lambda route: route.fulfill(status=200,
                                           content_type="application/json",
                                           body=stale))
    try:
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        got = page.evaluate("""() => {
          const bar = document.querySelector('.stale-bar');
          if (!bar) return null;
          const cs = getComputedStyle(bar);
          return {text: bar.textContent, colour: cs.color,
                  top: Math.round(bar.getBoundingClientRect().top),
                  first: document.body.firstElementChild === bar,
                  flag: document.body.dataset.stale}; }""")
    finally:
        page.unroute("**/api/version")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(900)
        page.locator(".review-item").first.click()
        page.wait_for_timeout(1400)

    assert got, "a stale build rendered no notice at all"
    assert "older build" in got["text"] and "Restart the server" in got["text"]
    assert "aaaaaaa" in got["text"] and "bbbbbbb" in got["text"], \
        "the notice does not name which build is running vs checked out"
    assert "138, 100, 20" in got["colour"], f"stale must use the amber role: {got['colour']}"
    assert got["first"], "the notice is not the first thing on the page"
    assert got["flag"] == "yes"


def test_a_current_build_shows_no_notice(page):
    """The other half: it must be quiet when there is nothing to act on."""
    assert page.evaluate("() => !document.querySelector('.stale-bar')")
    assert page.evaluate("() => document.body.dataset.stale") == "no"


def test_the_stale_notice_uses_the_broken_role_not_an_empty_state(page):
    """Amber is the third meaning. It must not share a colour with either of
    the two empty-state treatments."""
    got = page.evaluate("""() => {
      const mk = c => { const e = document.createElement('div'); e.className = c;
                        document.body.append(e); return e; };
      const a = mk('stale-bar'), b = mk('rca-empty'), c = mk('rca-empty affirm');
      const out = [a, b, c].map(e => getComputedStyle(e).color);
      [a, b, c].forEach(e => e.remove()); return out; }""")
    assert len(set(got)) == 3, got
