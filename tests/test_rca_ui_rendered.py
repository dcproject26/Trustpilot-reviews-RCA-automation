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

pytest.importorskip("playwright.sync_api", reason="playwright not installed")

# THE SECOND COPY OF THE CHROMIUM PATH, and it was the one that mattered.
# `conftest.py` had the same hardcoded "/opt/pw-browsers/chromium-1194/..."
# string and was fixed to resolve properly; this module kept its own copy and
# skipped AT MODULE LEVEL when the pinned path was missing — so the module was
# never collected at all, and the 34 other browser modules that import from it
# went with it. 533 tests, invisible, on every machine but one.
#
# A user who installed playwright to get exactly these tests saw the identical
# count twice, because the fix landed in one of the two places the rule lived.
# RE-EXPORTED, because 33 OTHER MODULES DO `from tests.test_rca_ui_rendered
# import CHROME`. Deleting the name here — after grepping only this file and
# seeing it unused — broke collection of all 33 at once. A name is not unused
# because the file that defines it does not use it.
#
# It is now conftest's single resolver, re-exported for those importers rather
# than redefined, so there is still exactly one rule for where Chromium is.
from tests.conftest import CHROME  # noqa: E402,F401


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
                "owner": "CONTENT",
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


def seed_db(url: str) -> None:
    """Put the fixture rows back, IN THIS PROCESS.

    The same seed as `seed_script`, without the interpreter. That difference
    is what makes it affordable to run between modules: the subprocess version
    re-imports the whole server stack every time, and doing that 28 times
    while a live uvicorn held the SQLite file is what wedged an earlier
    attempt at per-module reseeding — not the writing itself.

    The server is NOT restarted and does not need to be. It opens a session
    per request against this same file, so it reads whatever is here on the
    next request.

    Bound to an explicit engine rather than through `DATABASE_URL`, because
    the test process has its own `server.db` pointing somewhere else and
    reloading it to move a fixture is how live_db poisons later modules.
    """
    import json as _json
    from datetime import datetime
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import server.db as db
    from server.services.rca_v4_validate import validate, project_v4
    from server.prompts import RCA_PROMPT_VERSION

    eng = create_engine(url)
    try:
        db.Base.metadata.create_all(eng)
        s = sessionmaker(autocommit=False, autoflush=False, bind=eng)()
        try:
            v, _ = validate(_json.loads(_json.dumps(RCA)), ["Tickets sent late"])
            s.query(db.RcaDraft).delete()
            s.query(db.Review).delete()
            s.add(db.Review(id="tp_ui", slack_ts="1", slack_channel="C1", rating=1,
                            author="David", body_original="late tickets",
                            status="draft", received_at=datetime.utcnow()))
            s.add(db.RcaDraft(id="d_ui", review_id="tp_ui", rca_v3=v,
                              booking={"id": "32908218"},
                              match_tier=1, rca_prompt_version=RCA_PROMPT_VERSION,
                              zendesk_ticket_ids=["34125496", "34256902"],
                              confidence_trail=[
                                {"mark": "pass", "text": "<strong>BID extracted</strong> via attachment"},
                                {"mark": "warn", "text": "<strong>RCA</strong> — a coercion fired"}],
                              generated_at=datetime.utcnow(),
                              support_interaction_frames=_json.loads(_json.dumps(FRAMES)),
                              l1=v["l1"], l2=v["l2"], sub_theme="C. Ticket Delayed",
                              **dict(project_v4(v))))
            s.commit()
        finally:
            s.close()
    finally:
        eng.dispose()


def seed_script(url: str) -> str:
    """The seed program, as text, for whoever is standing the server up.

    A FUNCTION so conftest's session-scoped `ui_server` can reach it without
    conftest owning this module's fixture data. The RCA and FRAMES below are
    what these tests assert against, so they stay here beside the assertions.
    """
    return f"""
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
                  **dict(project_v4(v))))
s.commit(); s.close()
"""


@pytest.fixture(scope="module")
def page(ui_browser, ui_server):
    """A clean page per test module, on the shared browser and server.

    Module scope is the isolation these tests rely on — one module's clicks
    and inline edits must not reach the next. What it no longer means is a
    fresh Chromium and a fresh uvicorn: those are session-scoped in conftest,
    because this fixture is imported into 27 other modules and an imported
    fixture is a separate definition in each of them.
    """
    # THE ROWS GO BACK BEFORE THE PAGE OPENS. A fresh page was never the whole
    # of "one module's clicks must not reach the next" — the clicks that
    # matter (add a contact, edit an improvement point, confirm a candidate)
    # are writes to the shared database, and a new tab does not undo those.
    # It only looked sufficient while the browser modules were spread out
    # among the rest of the suite.
    ui_server.reseed()
    pg = ui_browser.new_page(viewport={"width": 1600, "height": 1200})
    # A HANG MUST NOT BE ABLE TO LOOK LIKE A SLOW RUN. Playwright's default is
    # 30s per operation, but a fixture that never resolves takes the other 463
    # tests down with it and reports nothing at all — the run simply stops at
    # 46% with no name attached. Bounded, the same fault becomes one named
    # failure in fifteen seconds and the rest of the suite still reports.
    pg.set_default_timeout(15000)
    pg.set_default_navigation_timeout(20000)
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    try:
        # `load`, NOT `networkidle`. The dashboard opens a poll on some paths,
        # and a page that never goes idle spends the full default timeout
        # before failing — thirty seconds per module, for nothing. Waiting for
        # the element the tests actually need is both faster and truthful
        # about what is being waited on.
        pg.goto(f"http://127.0.0.1:{ui_server.port}/", wait_until="load")
        pg.wait_for_selector(".inbox-row", timeout=15000)
        pg.locator(".inbox-row").first.click()
        # The RCA is a slide-over now, opened from the case header. Open it so
        # the controls below are on screen and interactable — almost every test
        # in this file and the ones that import this fixture drives the RCA.
        pg.wait_for_selector("#open-rca", timeout=15000)
        pg.click("#open-rca")
        pg.wait_for_selector(".case-body.rca-open #rca-col, .wwr-issue", timeout=15000)
        pg.wait_for_timeout(400)
        pg.errors = errs
        yield pg
    finally:
        pg.close()


def _rca_tab(page, tab):
    """Activate an RCA tab so its panel — and the controls inside it — are
    visible and clickable. The six panels all stay in the DOM (display:none
    when inactive), so a control in a non-active tab is present but not
    interactable until its tab is shown. Shared: other browser modules import
    this to reach sections outside the default Diagnosis tab."""
    page.click(f'[data-rca-tab="{tab}"]')
    page.wait_for_selector(f'.rca-tab-panel[data-tab="{tab}"].active', timeout=8000)
    page.wait_for_timeout(80)


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
    # tp_ui seeds two matched tickets, so the empty state reports the lookup
    # outcome: tickets found, none held a guest conversation.
    assert "nobody spoke to the guest here" in html, html


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


# ── §3: the issue-specific answers section is gone from the card ────────────

def test_the_answers_section_does_not_render(page):
    """The questions did not go away — they are checks the RCA writes against
    now, and what one surfaces is written as an operational failure or an SOP
    gap. Asserted in the DOM because the draft this page is built from HOLDS
    two answers: a section that still rendered would render them."""
    assert page.evaluate(
        "() => !!document.querySelector('#rca-issue-answers-section')") is False
    body = page.evaluate("() => document.querySelector('#rca-col').innerText")
    assert "Issue-specific answers" not in body
    assert "Delivered before the slot?" not in body, (
        "the stored answers are still being rendered somewhere")


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
      // OPENED. "How we built this match" is shut by default now — it is the
      // working-out, not the answer — and these tests are about what a FAILED
      // step says inside it. That it starts shut is pinned separately in
      // tests/test_match_trail_collapsed.py.
      state.trailSectionOpen = {a: true, b: true};
      renderReviewCol(); }""", RAW_ERR)


def _restore(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (r._keepTrail) r.confidenceTrail = r._keepTrail;
      state.rawErrOpen = {};
      state.trailSectionOpen = {};
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


def test_there_is_exactly_one_timeline_and_it_is_in_the_facts_column(page):
    """The card carried two lists of one story — booking_logs in the facts
    column, Zendesk events in the RCA column — each missing what the other
    had. They merged into the facts column, under the heading that column
    already carried."""
    assert _col(page, "#rca-booking-logs-section") == "facts"
    assert page.evaluate(
        "() => !document.querySelector('#rca-events-timeline-section')"), \
        "the RCA column renders a timeline again"


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


def test_the_post_carries_the_mandated_headings(page):
    """The what-went-wrong section follows the user's five-heading format, and
    the DASHBOARD shows the server's text — this is the browser end of the
    one-composer guarantee. The section is composed once, in
    server/services/wwr_post.py, and rendered here verbatim."""
    txt = _post(page)
    i = txt.find("*What went wrong*")
    assert i != -1, "the post has no what-went-wrong section at all"
    for h in ("1. Guest issue", "2. Is the guest's claim accurate?",
              "3. What actually happened?", "4. Fixes"):
        assert h in txt, f"missing mandated heading {h!r}\n{txt[i:i + 600]}"


def test_the_verdict_prints_in_the_users_vocabulary(page):
    """Yes / Partially True / No. The model's four-value enum stays on the
    card; the post speaks the words the user asked for."""
    txt = _post(page)
    i = txt.find("*What went wrong*")
    assert "2. Is the guest's claim accurate? Yes" in txt, txt[i:i + 600]
    assert "Partly accurate" not in txt, txt[i:i + 600]


def test_only_the_subpoints_that_issue_has_are_printed(page):
    """Sub-points a/b/c are INDICATIVE. A dash for every absent field turns a
    focused block into a form with blanks in it."""
    txt = _post(page)
    i = txt.find("3. What actually happened?")
    block = txt[i:txt.find("4. Fixes", i)]
    assert "Root cause:" in block, block
    assert "SOP/process gap:" not in block, "an absent sub-point was printed anyway"


def test_the_action_is_under_the_fixes_heading_without_the_handle(page):
    """THE TEAM TAG WAS REMOVED FROM HERE, BY REQUEST. This used to assert
    "@CONTENT" appeared under heading 4. The post opened each fix with a
    sub-point whose whole content was a handle; Actions Taken already groups
    these same fixes by the team that must do the work, and the post carries
    that section.

    Driven through the rendered post, so it cannot pass against a composer
    whose output never reaches the page."""
    txt = _post(page)
    i = txt.find("4. Fixes")
    block = txt[i:i + 300]
    assert "Add the two-hour delivery window to the page" in block, block
    assert "@CONTENT" not in block, block


def test_the_owner_chip_no_longer_rides_the_issue_title(page):
    txt = _post(page)
    assert "\u00b7  Accurate" not in txt
    assert "*1. Delivery window not disclosed*" not in txt


def test_evidence_and_the_guest_quote_come_out_of_the_post(page):
    """They stay on the CARD. The post carries the five headings and nothing
    else — checked in a real browser because the dashboard is the half that
    used to compose this section itself."""
    txt = _post(page)
    assert "[exp-page] No timeline on the page." not in txt
    assert "https://www.headout.com/tour/22238" not in txt


def test_the_fix_object_is_written_out_rather_than_stringified(page):
    """`fix` is an object. The client's old composer left it in a generic
    label loop where it stringified to "[object Object]" and went out on a
    real post — the action, the owner and the gap it closes all replaced by
    seven characters of nothing, while the server's copy of the same section
    was correct. That is what one composer removes; this pins it from the end
    where it actually broke."""
    txt = _post(page)
    i = txt.find("*What went wrong*")
    assert "[object Object]" not in txt, txt[i:i + 600]
    assert "Add the two-hour delivery window to the page" in txt, txt[i:i + 600]


def test_an_issue_carries_one_fix_line(page):
    """Two fix lines for one fix is the same defect wearing a valid string:
    the object path and a fallback path both firing, so the post says the fix
    twice and the reader cannot tell which one the pipeline believes."""
    txt = _post(page)
    i = txt.find("4. Fixes")
    block = txt[i:txt.find("____", i)]
    assert block.count("Add the two-hour delivery window to the page") == 1, block


def test_the_dashboard_post_is_byte_identical_to_the_servers_section(page):
    """The one-composer guarantee, driven in the browser: whatever the server
    put in `wwr_slack_text` is what the preview shows. If the client ever
    rebuilds the section, this diverges."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const post = (document.querySelector('[data-slack-edit]') || {}).value || '';
      return {server: r.rca.wwrSlackText || '', inPost: post}; }""")
    assert got["server"], "the draft carried no server-composed section at all"
    assert got["server"] in got["inPost"], (
        "the preview is not showing the server's text verbatim\n"
        f"--- server ---\n{got['server']}\n--- post ---\n{got['inPost']}")


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
    _rca_tab(page, "slack")
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
    _rca_tab(page, "slack")
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
      // The merged section, in the facts column. `booking_logs` is emptied
      // too: this helper is about the EVENT half, and a model row left in
      // place would make an "empty" timeline render a row.
      const keepV = r.rca && r.rca.v3 ? r.rca.v3.booking_logs : undefined;
      if (r.rca && r.rca.v3) r.rca.v3.booking_logs = [];
      r.events = events; r.zendeskTicketIds = tids || [];
      renderReviewCol();
      const s = document.querySelector('#rca-booking-logs-section');
      const out = {text: s.innerText,
                   rows: s.querySelectorAll('.tl-row').length,
                   toggle: !!s.querySelector('[data-tl-toggle]')};
      r.events = keepE; r.zendeskTicketIds = keepT;
      if (r.rca && r.rca.v3) r.rca.v3.booking_logs = keepV;
      renderReviewCol();
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
      r._keepE = r.events; r.events = events;
      r._keepV = r.rca.v3.booking_logs; r.rca.v3.booking_logs = [];
      renderReviewCol(); }""", INTERNAL)
    page.click("#rca-booking-logs-section [data-tl-toggle]")
    page.wait_for_timeout(250)
    shown = page.evaluate(
        "() => document.querySelectorAll('#rca-booking-logs-section .tl-row').length")
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r.events = r._keepE; r.rca.v3.booking_logs = r._keepV;
      state.tlShowInternal = false; renderReviewCol(); }""")
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
    assert "No Zendesk events were found" in got["text"], got["text"][:300]
    assert "confidence trail" in got["text"], \
        "an empty timeline still asserts the lookup ran"


def test_an_empty_stated_issue_beside_a_full_rca_does_not_blame_the_review(page):
    """"Nothing was extracted" is a claim about the review. With issues on the
    same screen, drawn from the same text, it is provably false."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      // BOTH stores. The box renders from rca.v3.stated_issue when that key
      // is present and falls back to the load-time snapshot, so emptying only
      // the snapshot leaves the blob's value on screen and this would assert
      // against a box that is not empty at all.
      const keep = r.statedIssue, keepV = r.rca.v3.stated_issue;
      r.statedIssue = ''; r.rca.v3.stated_issue = ''; renderRcaCol();
      const e = document.querySelector('.stated-issue .rca-empty');
      const out = {text: e ? e.innerText : null,
                   issues: ((r.rca.v3.what_went_wrong||{}).guest_issues||[]).length};
      r.statedIssue = keep; r.rca.v3.stated_issue = keepV;
      renderRcaCol(); return out; }""")
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
      const kV = r.rca.v3.stated_issue;
      // Both stores — see the sibling test above.
      r.statedIssue = ''; r.rca.v3.stated_issue = '';
      r.rca.v3.what_went_wrong = {guest_issues: []};
      renderRcaCol();
      const e = document.querySelector('.stated-issue .rca-empty');
      const out = e ? e.innerText : null;
      r.statedIssue = kS; r.rca.v3.what_went_wrong = kW;
      r.rca.v3.stated_issue = kV; renderRcaCol();
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
      r._keepV = r.rca.v3.booking_logs; r.rca.v3.booking_logs = [];
      state.tlShowInternal = false; renderReviewCol(); }""", MIXED)
    before = page.evaluate(
        "() => document.querySelectorAll('#rca-booking-logs-section .tl-row').length")
    page.click("#rca-booking-logs-section [data-tl-toggle]")
    page.wait_for_timeout(250)
    after = page.evaluate(
        "() => document.querySelectorAll('#rca-booking-logs-section .tl-row').length")
    note = page.evaluate(
        "() => document.querySelector('#rca-booking-logs-section [data-tl-toggle]').innerText")
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      r.events = r._keepE; r.rca.v3.booking_logs = r._keepV;
      state.tlShowInternal = false; renderReviewCol(); }""")
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
        page.reload(wait_until="load")
        # WAIT FOR THE CONDITION, not for a fixed 1200 ms. The page fixture is
        # module-scoped, so this test hands the page to the next one — and on a
        # loaded box the /api/version round trip had not landed inside the
        # sleep, so `test_a_current_build_shows_no_notice` ran against a page
        # still showing the stale bar and failed. A red that only appears under
        # load, in a test that is not the one at fault, is the worst kind: it
        # sends the reader to the wrong file.
        page.wait_for_selector(".stale-bar", timeout=15000)
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
        page.reload(wait_until="load")
        # Same again on the way out: the NEXT test asserts the bar is gone, so
        # leaving that to a timer makes its result depend on how busy the
        # machine is rather than on the build.
        page.wait_for_function(
            "() => document.body.dataset.stale === 'no'"
            " && !document.querySelector('.stale-bar')", timeout=15000)
        page.locator(".inbox-row").first.click()
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


# ── §1 the nine teams, and the AND that fills them ──────────────────────────
#
# The state is set in the page and the real renderer is called, because the
# thing being checked is what a reader SEES: which tabs exist, which rows
# survive the gate, and what an empty tab says about why it is empty.

def _render(page, patch):
    """Apply a patch to the selected review's rca and re-render the column."""
    page.evaluate("""(p) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (!window.__keep) window.__keep = {
        actionsTaken: JSON.parse(JSON.stringify(r.rca.actionsTaken || {})),
        v3flags: JSON.parse(JSON.stringify((r.rca.v3 || {}).flags || [])),
        frames: JSON.parse(JSON.stringify(r.rca.supportFrames || [])),
        notes: JSON.parse(JSON.stringify(r.rca.supportNotes || [])),
        aoi: JSON.parse(JSON.stringify(r.rca.areaOfImproving || []))};
      if (p.actionsTaken) r.rca.actionsTaken = p.actionsTaken;
      if (p.flags) (r.rca.v3 = r.rca.v3 || {}).flags = p.flags;
      if (p.frames) r.rca.supportFrames = p.frames;
      if (p.notes !== undefined) r.rca.supportNotes = p.notes;
      if (p.aoi !== undefined) r.rca.areaOfImproving = p.aoi;
      if (p.tab) state.actionTab = p.tab;
      renderRcaCol(); }""", patch)


def _restore(page):
    page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      if (!window.__keep) return;
      r.rca.actionsTaken = window.__keep.actionsTaken;
      (r.rca.v3 = r.rca.v3 || {}).flags = window.__keep.v3flags;
      r.rca.supportFrames = window.__keep.frames;
      r.rca.supportNotes = window.__keep.notes;
      r.rca.areaOfImproving = window.__keep.aoi;
      state.actionTab = 'sp';
      window.__keep = undefined; renderRcaCol(); }""")


def _tabs(page):
    return page.evaluate("""() => [...document.querySelectorAll('.action-tab')].map(
        t => ({key: t.dataset.tab,
               label: t.firstChild.textContent.trim(),
               count: t.querySelector('.count').textContent.trim()}))""")


def test_the_action_tabs_are_unrouted_plus_the_nine_teams(page):
    """UNROUTED IS FIRST and is not a tenth team. The server routes a fix with
    no owner to `unrouted`; this strip was the nine, so those rows landed on a
    tab that was never drawn — and the ingest, which built its buckets from the
    same nine, discarded them before the strip even saw them. Both had to
    change, and the tab alone still read zero, which is how the ingest turned
    out to be the one eating them."""
    got = _tabs(page)
    assert [t["key"] for t in got] == ["unrouted", "guest", "sp", "content",
                                       "co", "tech", "inventory", "product",
                                       "biz", "finance"]
    assert [t["label"] for t in got] == [
        "Unrouted", "NA/Guest error", "Supply Partner",
        "Content/Catalog/Media team", "CO team", "Tech team", "Inventory Team",
        "Product team", "Biz team", "Finance team"]


def test_a_row_the_server_raised_renders_under_its_team(page):
    try:
        _render(page, {"actionsTaken": {"content": [
                    {"context": "Add the delivery window to the page",
                     "with": "", "handle": "", "time": "", "where": "#"}]},
                "flags": [{"team": "CONTENT", "flag": "Page states no window",
                           "evidence": "exp-page"}],
                "tab": "content"})
        counts = {t["key"]: t["count"] for t in _tabs(page)}
        assert counts["content"] == "1", counts
        body = page.evaluate("() => document.querySelector('.action-content').innerText")
        assert "Add the delivery window to the page" in body
    finally:
        _restore(page)


def test_an_empty_tab_says_which_kind_of_empty_it_is(page):
    """The AND has two ways of producing a blank tab and they are opposite
    facts: nothing was flagged against this team, or the team is flagged and
    the guidelines prescribe nothing for the routed scenario. One blank space
    for both is the failure this card is built to avoid."""
    try:
        _render(page, {"actionsTaken": {},
                       "flags": [{"team": "CONTENT", "flag": "x", "evidence": "y"}],
                       "tab": "content"})
        flagged = page.evaluate("() => document.querySelector('.action-empty').innerText")
        _render(page, {"tab": "finance"})
        unflagged = page.evaluate("() => document.querySelector('.action-empty').innerText")
    finally:
        _restore(page)
    assert "prescribe no step" in flagged, flagged
    assert "Nothing is flagged against Finance team" in unflagged, unflagged
    assert flagged != unflagged


def test_a_draft_written_under_the_old_five_tabs_keeps_its_rows(page):
    """CE and Customer were both the support desk; Business was commercial. An
    old card whose rows silently vanished would read as a card with nothing
    raised, which is the one thing an empty tab must never mean by accident.

    Saved through the API and reloaded, because the fold happens where the
    payload becomes state — setting the state directly would test the
    renderer and leave the mapping, which is the thing that can drop them,
    unexercised."""
    old = {"ce":       ["CE retrained on the macro"],
           "business": ["Raised with the BDM"],
           "sp":       ["Raised the redemption failure with the SP"]}
    try:
        page.evaluate("""async (old) => {
          const r = REVIEWS.find(x => x.id === state.selected);
          window.__oldAt = JSON.parse(JSON.stringify(r.rca.actionsTaken));
          await fetch(`/api/reviews/${r.id}/draft-v2`, {
            method: 'PATCH', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({actions_taken: old})});
          await loadDraftOverlays();
          state.actionTab = 'co';
          renderRcaCol(); }""", old)
        page.wait_for_timeout(600)
        counts = {t["key"]: t["count"] for t in _tabs(page)}
        co = page.evaluate("() => document.querySelector('.action-content').innerText")
        _render(page, {"tab": "biz"})
        biz = page.evaluate("() => document.querySelector('.action-content').innerText")
    finally:
        page.evaluate("""async () => {
          const r = REVIEWS.find(x => x.id === state.selected);
          await fetch(`/api/reviews/${r.id}/draft-v2`, {
            method: 'PATCH', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({actions_taken: {}})});
          r.rca.actionsTaken = window.__oldAt; state.actionTab = 'sp';
          renderRcaCol(); }""")
        page.wait_for_timeout(400)
        _restore(page)
    assert counts["co"] == "1" and counts["biz"] == "1" and counts["sp"] == "1", counts
    assert "CE retrained on the macro" in co
    assert "Raised with the BDM" in biz


# ── §2 flags use the same nine ──────────────────────────────────────────────

def test_the_flag_team_control_offers_the_nine_and_nothing_else(page):
    opts = page.evaluate("""() => [...document.querySelectorAll(
        '#rca-flags-section select.chip-team-sel')].map(s => [...s.options].map(o => o.text))""")
    assert opts, "no flag team control rendered"
    assert opts[0] == ["GUEST", "SP", "CONTENT", "CO", "TECH", "INVENTORY",
                       "PRODUCT", "BIZ", "FINANCE", "OTHER"]


def test_a_flag_from_the_old_vocabulary_lands_on_the_team_that_owns_it_now(page):
    """An unrecognised value leaves a <select> showing its first option, which
    would file a CE flag against NA/Guest error — a real flag, quietly
    reassigned to the one bucket that means nobody has work."""
    try:
        _render(page, {"flags": [{"team": "CE", "flag": "First reply after SLA",
                                  "evidence": "40 minutes"}]})
        got = page.evaluate("""() => {
          const s = document.querySelector('#rca-flags-section select.chip-team-sel');
          const g = document.querySelector('#rca-flags-section .chk-group-head');
          return {selected: s.value, group: g.innerText}; }""")
    finally:
        _restore(page)
    assert got["selected"] == "CO", got
    assert "CO" in got["group"]


# ── §4 conversations only, and the count says what moved ────────────────────

def _contact_section(page):
    return page.evaluate("""() => {
      const sec = [...document.querySelectorAll('#rca-col .section')].find(
        s => /guest . support/i.test(s.querySelector('.section-label')?.innerText || ''));
      return {hint: sec.querySelector('.hint').innerText,
              rows: sec.querySelectorAll('.convo-frame').length,
              empty: (sec.querySelector('.interactions-empty') || {}).innerText || ''}; }""")


MACHINERY = [
    {"thread": "booking", "time": "22 Jul 15:22", "time_sort": "2026-07-22T15:22:00",
     "guestSaid": "Booking created", "actor": "creation"},
    {"thread": "api", "time": "22 Jul 15:23", "time_sort": "2026-07-22T15:23:00",
     "guestSaid": "Booking details posted", "is_internal": True},
    {"thread": "review", "time": "24 Jul", "time_sort": "2026-07-24T00:00:00",
     "guestSaid": "Review posted", "actor": "review"},
]
CHAT = [{"ticket_id": "34011401", "thread": "chat", "time": "22 Jul 15:41",
         "time_sort": "2026-07-22T15:41:00", "guestSaid": "Where are my tickets?"}]


def test_machinery_is_not_counted_as_a_contact(page):
    try:
        _render(page, {"frames": CHAT + MACHINERY, "notes": []})
        got = _contact_section(page)
    finally:
        _restore(page)
    assert got["rows"] == 1, "the booking, API and review rows are still contacts"
    assert got["hint"].startswith("1 contact"), got["hint"]
    assert "3 system events moved to the timeline" in got["hint"], got["hint"]


# The server stamps `is_contact` on every frame (api.py::_marked_frames) from
# the same split Slack composes with. These rows are what it produces for an
# agent-only ticket: the frames pass the THREAD test — "web" is not machinery
# — and are excluded anyway, because no guest is in the exchange.
AGENT_ONLY = [
    {"ticket_id": "33978941", "thread": "web", "actor": "co",
     "time": "02 Aug 15:28", "time_sort": "2026-08-02T15:28:00",
     "weDid": "Agent marked NAR", "guestSaid": "", "is_contact": False},
    {"ticket_id": "33978941", "thread": "web", "actor": "co",
     "time": "03 Aug 12:45", "time_sort": "2026-08-03T12:45:00",
     "weDid": "ORM escalation; 25% credit", "guestSaid": "", "is_contact": False},
]
GUEST_CHAT = [dict(CHAT[0], is_contact=True)]


def test_an_agent_only_ticket_does_not_render_as_a_contact(page):
    """THE ROW THAT KEPT COMING BACK. An agent's internal NAR note rendered as
    "contact 01" through three rounds of the Python being fixed, because this
    page held its own weaker copy of the rule — the thread list and
    `is_internal`, and nothing else. "web" is not a machinery thread and the
    row is not marked internal, so its copy let both frames through.

    The verdict is the server's now. This drives the RENDERED panel with the
    frames exactly as `_marked_frames` ships them, so a page that ignores
    `is_contact` and re-derives fails here."""
    try:
        _render(page, {"frames": GUEST_CHAT + AGENT_ONLY, "notes": []})
        got = _contact_section(page)
    finally:
        _restore(page)
    assert got["rows"] == 1, \
        f"the agent-only ticket rendered as a contact: {got}"
    assert got["hint"].startswith("1 contact"), got["hint"]


def _contact_empty(page, frames, tids):
    """Render the guest↔support section with the given frames and ticket ids,
    read its empty state + subtitle, and restore. Self-contained because the
    determinant of the empty-state claim is now `zendeskTicketIds`, which the
    shared _render does not control."""
    return page.evaluate("""({frames, tids}) => {
      const r = REVIEWS.find(x => x.id === state.selected);
      const keep = [r.rca.supportFrames, r.rca.supportNotes, r.zendeskTicketIds];
      r.rca.supportFrames = frames; r.rca.supportNotes = []; r.zendeskTicketIds = tids;
      renderRcaCol();
      const sec = [...document.querySelectorAll('#rca-col .section')].find(
        s => /guest . support/i.test(s.querySelector('.section-label')?.innerText || ''));
      const out = {rows: sec.querySelectorAll('.convo-frame').length,
                   hint: sec.querySelector('.hint').innerText,
                   empty: (sec.querySelector('.interactions-empty')||{}).innerText||''};
      [r.rca.supportFrames, r.rca.supportNotes, r.zendeskTicketIds] = keep;
      renderRcaCol();
      return out; }""", {"frames": frames, "tids": tids})


def test_machinery_without_a_ticket_match_does_not_read_as_a_silent_guest(page):
    """The determinant is the ticket lookup, not the machinery. A booking whose
    only events are machinery AND where no Zendesk ticket matched must not claim
    nobody spoke — the card cannot tell "none matched" from "lookup never ran".
    The subtitle still reports the machinery moved to the timeline."""
    got = _contact_empty(page, MACHINERY, [])
    assert got["rows"] == 0
    assert "moved to the timeline" in got["hint"], got["hint"]
    assert "nobody spoke" not in got["empty"], got["empty"]
    assert "never reached support" not in got["empty"], got["empty"]
    assert "did not run" in got["empty"], got["empty"]


def test_nothing_at_all_makes_no_claim_about_guest_contact(page):
    """No events and no ticket match: the card must NOT assert the guest never
    reached support — a claim it cannot establish — and it says as much."""
    got = _contact_empty(page, [], [])
    assert got["rows"] == 0
    assert "moved to the timeline" not in got["hint"], got["hint"]
    assert "never reached support" not in got["empty"], got["empty"]
    assert "nobody spoke" not in got["empty"], got["empty"]
    assert "did not run" in got["empty"], got["empty"]


def test_tickets_found_but_no_conversation_does_say_nobody_spoke(page):
    """The one case where the claim is earned: tickets matched and none held a
    guest conversation. The ticket ids are linked so the reader can open them."""
    got = _contact_empty(page, MACHINERY, ["34125496"])
    assert got["rows"] == 0
    assert "nobody spoke to the guest here" in got["empty"], got["empty"]


def test_the_moved_events_have_a_timeline_to_be_moved_to(page):
    """Moved, not dropped. The events timeline renders from the draft's own
    timeline — the same events the frames were built from — so the section
    exists and carries them whether or not this fixture seeded any."""
    got = page.evaluate("""() => {
      const sec = document.querySelector('#rca-booking-logs-section');
      return sec ? {rows: sec.querySelectorAll('.tl-row').length,
                    text: sec.innerText} : null; }""")
    assert got, "there is no timeline for a moved event to live on"
    assert got["rows"] > 0 or "No Zendesk events were found" in got["text"], got


# ── §5 the improvement pointers carry where they came from ──────────────────

def _aoi(page):
    return page.evaluate("""() => [...document.querySelectorAll('#rca-col .rca-point')].map(
        p => ({text: p.querySelector('[data-aoi-idx]').innerText.trim(),
               marker: (p.querySelector('.aoi-src') || {}).textContent || '',
               title: (p.querySelector('.aoi-src') || {}).title || ''}))""")


# ── the guest-name note reaches the card it was written for ─────────────────

def test_the_reason_there_is_no_guest_name_is_the_servers_reason(page):
    """Three different situations, three different next actions.

    `_draft_dict` works out WHICH source came up empty — the warehouse holds a
    PII hash (open the ticket), the linked ticket carries no requester name
    (open the ticket), or no ticket was ever matched to this booking (there is
    nothing to open). It ships that as a top-level `guest_name_note`.

    The client read it off `draft.booking`, which has never carried it, so the
    read was `undefined` on every card and all three collapsed into one generic
    fallback. The server's whole distinction reached nobody, and the sentence
    the reader got was true of a case it might not be.

    This fixture's booking has no guest name and two linked tickets, so the
    server's answer is specific and the fallback is not.
    """
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      return {note: r.booking.guestNameNote,
              inDom: document.body.innerText.includes(
                'no requester name on the linked Zendesk ticket')}; }""")
    assert got["note"] == "no requester name on the linked Zendesk ticket", \
        (f"the payload carries {got['note']!r} — that is the generic fallback, "
         f"not the reason the server worked out")
    # THE ROW IS NO LONGER RENDERED, by request, until the hash problem is
    # solved — see the comment where it used to be in client/index.html. What
    # this test still guards is the half that matters for bringing it back:
    # the server works out a SPECIFIC reason and puts it on the payload. The
    # generic fallback reaching the client was the original bug, and it would
    # return silently if only the template were checked.
    assert not got["inDom"], (
        "the Primary guest row is rendering again — it was removed "
        "deliberately; if that is intended, this test needs rewriting rather "
        "than the assertion flipping")


def test_the_generic_fallback_is_not_what_a_specific_answer_renders_as(page):
    """The fallback still has to exist for a payload with no note at all — but
    it must not be what a card with a real answer shows."""
    got = page.evaluate("""() => {
      const r = REVIEWS.find(x => x.id === state.selected);
      return r.booking.guestNameNote; }""")
    assert got != "no guest name on the booking or on any linked Zendesk ticket", \
        "a specific server-side reason is rendering as the catch-all"


# ── Area of improvement: the card is gone, the data is not ─────────────────

def test_the_improvement_card_is_no_longer_rendered(page):
    """Six tests here drove that card and were REMOVED with it, not left to
    rot green against a section nobody draws. The card was removed by request,
    to come back later; the derivation, the provenance check and the Slack
    section all still run — see
    test_wwr_post_shapes.py::test_area_of_improvement_is_off_the_card_but_still_in_the_post.
    """
    got = page.evaluate("""() => ({
        card: [...document.querySelectorAll('#rca-col .section-label span')]
                .some(e => e.textContent.trim() === 'Area of improvement'),
        rows: document.querySelectorAll('[data-aoi-idx]').length})""")
    assert not got["card"], "the Area of improvement card is back"
    assert got["rows"] == 0, got


def test_case_findings_carry_the_merged_evidence(page):
    """The merge is back ON and §1 is the ONLY place a claim-backing fact
    renders. It was switched off for duplicating, and switching it off turned
    out to hide the facts completely: `evRow`, the per-issue renderer, had no
    callers, so §2 never drew them either.

    A ZD ref on a §1 row is the visible sign a merged fact arrived — that ref
    is what turns a ticket id into something the reader can open.
    """
    got = page.evaluate("""() => ({
        section: !!document.querySelector('#rca-casefindings-section'),
        rows:    document.querySelectorAll('#rca-casefindings-section .cf-row').length,
        dels:    document.querySelectorAll('#rca-casefindings-section [data-cf-del]').length,
        times:   document.querySelectorAll('#rca-casefindings-section .cf-time').length,
        inIssue: document.querySelectorAll('.wwr-issue .ev-row').length})""")
    assert got["section"], "the case findings section stopped rendering"
    assert got["rows"], "§1 rendered with no findings at all"
    assert got["dels"] == got["rows"], \
        f"{got['rows']} findings and {got['dels']} removers — a row cannot be deleted"
    assert got["times"] == 0, "§1 is showing clock times; that is the timeline's job"
    assert got["inIssue"] == 0, \
        "the evidence is rendering under the issue AS WELL as in §1 — the "\
        "duplication is back, from the other side"
