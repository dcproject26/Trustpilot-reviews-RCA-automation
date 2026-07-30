// Renders the dashboard's Slack-post generator against a realistic RCA v3
// draft and asserts the posted text carries the 5 mandated WWR headings, the
// numbered event log, and every section - and that pointer ARRAYS never print
// as "a,b,c" comma runs, which is what happens the moment someone joins one
// into a string.
//
//   node tests/slack_post_format.test.js
//
// The function under test is extracted from client/index.html at run time, so
// this cannot drift from what ships: if the generator is edited, this runs the
// edited copy.

const fs = require('fs');
const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '..', 'client', 'index.html'), 'utf8');
const fnStart = html.indexOf('  function _genSlackText() {');
const fnEnd = html.indexOf('  const _slackText =', fnStart);
if (fnStart === -1 || fnEnd === -1) {
  console.error('FAIL: _genSlackText not found in client/index.html');
  process.exit(1);
}
const genSource = html.slice(fnStart, fnEnd);

const nl = '\n', div = '_'.repeat(61);
const asPoints = v => (Array.isArray(v) ? v : (v ? [v] : [])).filter(Boolean);
const state = {slackSections: {}, insightsWindow: '90d'};
const v3d = {
  tldr: {our_mistake: 'No delivery window on the page', our_fix: 'Content audit raised'},
  what_went_wrong: {
    guest_issues: [{issue: 'Tickets delayed', claim_accuracy: 'Partially True',
                    evidence: ['[experience-page] no window stated', '[zendesk] mail promised 2h']}],
    what_happened: {root_causes: [{issue: 'Delay', cause: 'Selenium FF, no disclosure',
                                   classification: 'Operational + HO'}],
                    operational_failure: ['Chat not picked up for 9 min'],
                    sop_gap: ['No checkout warning for same-day Selenium'],
                    pattern: 'one-off - 0 similar in 90d'},
    sp_escalation: {escalated: 'N/A', detail: ['Vendor not partnered', 'No escalation email on file']},
    fixes: {teams: ['Content','Product'], owner: 'Content',
            actions: ['Audit TGID 22238 delivery window'],
            prevention: ['Add checkout callout for Selenium same-day']}
  },
  booking_logs: [
    {time: '22 Jul 15:22', what: 'Booking-in-progress email sent', detail: 'tickets promised in 2h'},
    {time: '22 Jul 15:41', what: 'Guest opened chat', detail: 'asked for tickets immediately'},
    {time: '22 Jul 15:50', what: 'Tickets issued', detail: 'ref 1022394558263'}],
  flags: [{team: 'content', flag: 'Delivery window not on page', evidence: 'redemption null', zd_ref: 'ZD-34011333'}],
  support_interaction: [{time: '15:41', channel: 'chat', summary: 'guest asked for tickets',
                         ce_miss: 'no agent reply for 9 min', zd_ref: 'ZD-34011401'}],
  sp_interaction: {possible: false, reason_if_not: 'not partnered', raised: 'N/A', detail: [], zd_ref: ''},
  sop_compliance: {dss_available: true, verdict: 'followed', expected: 'resend or refund',
                   actual: 'refund issued', detail: 'denial then persistence then refund', zd_ref: 'ZD-34011333'},
  takedown: {recommended: false, reason: 'claim partially accurate'},
  area_of_improving: ['Surface delivery window at checkout']
};
const spV3 = v3d.sp_interaction;
const r = {rating: 1, author: 'David', insights: {tgidRating: {value: '4.2', sub: '90d'},
           completion: {value: '57%', sub: 'vendor'}}};
const b = {bid: '32908218'};
const rca = {v3: v3d, issueL1: 'Operations Issue', issueL2: 'Ticket Issues',
  subTheme: 'C. Ticket Delayed', primaryScenario: 'Tickets sent late',
  overlayScenarios: ['Refund issues'], wwrScenarios: [], wwrChain: [],
  supportInteraction: [], spInteraction: [], areaOfImproving: v3d.area_of_improving,
  actionsTaken: {sp:[],customer:[],business:[],ce:[],product:[]}, resolution: 'Full refund',
  tldr: '', checklistAnswers: []};

eval(genSource);
const out = _genSlackText();
console.log(out);
const must = ['1. Guest issue', "2. Is the guest's claim accurate?", '3. What actually happened?',
  '4. Supply Partner escalation', '5. Fixes', '1. 22 Jul 15:22 — Booking-in-progress email sent',
  '*Booking logs*', '*Flags*', '*SOP compliance*', '*Experience insights*', '*Review takedown*'];
const missing = must.filter(m => out.indexOf(m) === -1);
if (missing.length) { console.error('\nMISSING: ' + JSON.stringify(missing)); process.exit(1); }
if (out.indexOf('[experience-page] no window stated,') !== -1) {
  console.error('\nFAIL: array printed as comma run'); process.exit(1); }
console.log('\n=== ALL 5 HEADINGS + NUMBERED EVENTS PRESENT, no comma runs ===');
