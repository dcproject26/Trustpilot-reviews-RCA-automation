#!/usr/bin/env python3
"""Audit the RCA column against the ten defects observed in the deployed build.

The handoff lists them as binding specs, so they are acceptance criteria rather
than context. Each check drives the real page: defect 4 only exists when the
contact section is empty and defect 6 only when the review is unclassified, so
those states are produced in-page and restored afterwards - a fixture with data
cannot see either.

    python3 -m uvicorn server.main:app --port 8099   # in one shell
    python3 tools/audit_rca_ui.py                    # in another

Defect 7 (empty reply behind Send) is not covered here: it belongs to the reply
card, not the RCA column. Defects 1-6 and 8-10 are.
"""
import json
from playwright.sync_api import sync_playwright
R = {}
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    pg = b.new_page(viewport={"width": 1600, "height": 1300})
    errs=[]; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:8099/", wait_until="networkidle"); pg.wait_for_timeout(900)
    pg.locator(".review-item").first.click(); pg.wait_for_timeout(1500)
    R["js errors"] = errs or "none"
    # §1 is not "no bullet nodes" - it is that the bullet and its text are ONE
    # flex row. A bullet-only span is correct; a bullet on its own LINE is the
    # defect, so the check is geometric.
    R["1 orphaned bullets"] = pg.evaluate("""() => {
        const bad = [...document.querySelectorAll('#rca-col *')].filter(e =>
          e.children.length===0 && /^[•\\u2013-]$/.test((e.textContent||'').trim()))
          .filter(e => { const sib = e.nextElementSibling; if (!sib) return false;
            return sib.getBoundingClientRect().top - e.getBoundingClientRect().top > 4; });
        return bad.length ? bad.length + ' bullets on their own line' : 'none'; }""")
    R["2 orphan quote marks"] = pg.evaluate("""() => {
        const q=[...document.querySelectorAll('.wwr-claim-q')];
        return q.every(x => { const ed=x.querySelector('[contenteditable]');
          return ed && ed.getBoundingClientRect().top - x.getBoundingClientRect().top < 4; })
          ? 'inline' : 'ORPHANED'; }""")
    R["3 sentence verdicts"] = pg.evaluate("""() => {
        const v=[...document.querySelectorAll('#rca-col select.chip-acc-sel')];
        const opts=new Set(v.flatMap(s=>[...s.options].map(o=>o.text)));
        return v.length + ' chips, vocabulary: ' + [...opts].join('|'); }""")
    R["4 empty contact as a row"] = pg.evaluate("""() => {
        const r=REVIEWS.find(x=>x.id===state.selected);
        const k=[r.rca.supportFrames,r.rca.supportNotes,r.rca.v3.support_interaction_notes];
        r.rca.supportFrames=[];r.rca.supportNotes=[];r.rca.v3.support_interaction_notes=[];
        renderRcaCol();
        const el=[...document.querySelectorAll('.interactions')].find(e=>e.querySelector(':scope > .interactions-empty'));
        const h=el?el.innerHTML:'';
        [r.rca.supportFrames,r.rca.supportNotes,r.rca.v3.support_interaction_notes]=k; renderRcaCol();
        return /convo-num|convo-type-pill|convo-time/.test(h) ? 'DRESSED AS A ROW' : 'neutral empty state'; }""")
    R["5 raw exception inline"] = pg.evaluate("""() => {
        const r=REVIEWS.find(x=>x.id===state.selected);
        r.confidenceTrail=[...(r.confidenceTrail||[]),{mark:'fail',
          text:'<strong>Run failed</strong> — the database connection dropped mid-run.',
          raw:'(psycopg2.OperationalError) [SQL: SELECT rca_drafts.id FROM rca_drafts]'}];
        renderReviewCol();
        const s=[...document.querySelectorAll('.conf-step.fail')].pop();
        return s.innerText.includes('SELECT') ? 'INLINE'
             : (s.querySelector('[data-raw-err]') ? 'sentence + toggle' : 'NO TOGGLE'); }""")
    R["6 dash-only Slack line"] = pg.evaluate("""() => {
        const r=REVIEWS.find(x=>x.id===state.selected);
        const k=[r.rca.issueL1,r.rca.issueL2,r.rca.subTheme,r.rca.slackThreadOverride];
        r.rca.issueL1='';r.rca.issueL2='';r.rca.subTheme='';r.rca.slackThreadOverride='';
        renderRcaCol();
        const t=(document.querySelector('[data-slack-edit]')||{}).value||'';
        [r.rca.issueL1,r.rca.issueL2,r.rca.subTheme,r.rca.slackThreadOverride]=k; renderRcaCol();
        return /Issue:.*—\\s*\\/\\s*—/.test(t) ? 'DASHES POSTED' : 'line omitted'; }""")
    R["8 ? tokens"] = pg.evaluate("""() => {
        const t=document.querySelector('#rca-col').innerText;
        return /no support-tag mapping for \\?/.test(t) ? 'PRESENT' : 'none'; }""")
    R["9 answer pill"] = pg.evaluate("""() => {
        const s=document.querySelector('#rca-issue-answers-section');
        if (!s) return 'section missing';
        const sels=[...s.querySelectorAll('select')];
        return s.innerText.toLowerCase().split(/\\s+/).includes('answer') ? 'PILL PRESENT'
             : sels.length + ' chip-selects, all ' + [...new Set(sels.map(x=>Math.round(x.getBoundingClientRect().width)))].join('/') + 'px'; }""")
    R["10 Unknown as a time"] = pg.evaluate("""() => {
        const t=[...document.querySelectorAll('#rca-col .tl-time,#rca-col .convo-time')]
          .map(e=>e.textContent.trim());
        return t.some(x=>/^unknown$/i.test(x)) ? 'PRESENT' : 'none'; }""")
    R["13 booking timeline"] = pg.evaluate(
        "() => { const e=document.querySelector('#rca-booking-logs-section'); return e ? (e.closest('#rca-col')?'RCA COLUMN':'facts column') : 'missing'; }")
    R["13 events timeline"] = pg.evaluate(
        "() => { const e=document.querySelector('#rca-events-timeline-section'); return e ? (e.closest('#rca-col')?'RCA column':'FACTS COLUMN') : 'missing'; }")
    R["14 header"] = pg.evaluate("""() => {
        const h=document.querySelector('#rca-col .rca-head');
        const t=h.querySelector('.rca-title'), s=h.querySelector('.rca-sub');
        return Math.abs(t.getBoundingClientRect().top-s.getBoundingClientRect().top)<4
          ? 'one row, '+Math.round(h.getBoundingClientRect().height)+'px' : 'STACKED'; }""")
    R["add paths"] = pg.evaluate("""() => [...document.querySelectorAll('#rca-col button, #review-col button, .facts-col button')]
        .map(b=>(b.innerText||'').replace(/\\s+/g,' ').trim()).filter(t=>/^\\+/.test(t))""")
    R["x colours"] = pg.evaluate("""() => { const m={};
        document.querySelectorAll('.x-del,.row-del').forEach(e=>{
          const c=getComputedStyle(e).color; m[c]=(m[c]||0)+1;}); return m; }""")
    R["overflow-wrap coverage"] = pg.evaluate("""() => ({
        anywhere: document.querySelectorAll('#rca-col [style*="anywhere"], #rca-col .ev-text, #rca-col .wwr-aline-val').length,
        body_scrolls: document.body.scrollWidth > document.body.clientWidth }) """)
    pg.screenshot(path="/tmp/claude-0/-home-user-Claude/e0dc4d78-1dc8-5c9c-a1a2-10ab4a0c2749/scratchpad/audit.png", full_page=True)
    b.close()
print(json.dumps(R, indent=1))
