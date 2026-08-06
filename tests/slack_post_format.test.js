// Runs the dashboard's REAL Slack-post generator, extracted from
// client/index.html at run time, and asserts what it does with the
// what-went-wrong section.
//
//   node tests/slack_post_format.test.js
//
// Driven by tests/test_client_slack_post_js.py so it cannot rot unnoticed.
// It had rotted: this file asserted the five mandated headings for months
// against a client that never produced them, crashed on a missing helper
// before it reached the assertion, and nothing ran it — a harness that is
// never executed looks exactly like one that passes, which is the failure
// CLAUDE.md §1 is written about.
//
// WHAT IT GUARDS NOW. There is ONE composer for the what-went-wrong section
// and it is server-side (server/services/wwr_post.py). The client must render
// that server text VERBATIM and must not rebuild the section from rca.v3 —
// the two-composer arrangement is how "Fix: [object Object]" reached a real
// post from this half while the server's half was correct.

const fs = require('fs');
const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '..', 'client', 'index.html'), 'utf8');

const fails = [];
function check(cond, msg) { if (!cond) fails.push(msg); }

const fnStart = html.indexOf('  function _genSlackText() {');
const fnEnd = html.indexOf('  const _slackText =', fnStart);
if (fnStart === -1 || fnEnd === -1) {
  console.error('FAIL: _genSlackText not found in client/index.html');
  process.exit(1);
}
const genSource = html.slice(fnStart, fnEnd);

// The module-scope helpers the generator calls. In the browser these are in
// scope; extracting the generator alone made this fail on changes that were
// correct, and a false alarm is as costly as a missed one.
function grab(sig, end) {
  const a = html.indexOf(sig);
  if (a === -1) return '';
  const b = html.indexOf(end, a);
  return b === -1 ? '' : html.slice(a, b + end.length);
}
// `const` declared inside eval() is scoped to that eval and is invisible to
// the next one, so the generator could not see the helpers and this harness
// died on a ReferenceError before reaching a single assertion. `var` leaks to
// the enclosing function scope, which is what both evals share.
const helperSource = [
  grab('function _insightsWindowLabel(r) {', '\n}'),
  grab('const aoiRows = v =>', '.filter(x => x.point);'),
].join('\n').replace(/^const /gm, 'var ');

const nl = '\n', div = '_'.repeat(61);
const asPoints = v => (Array.isArray(v) ? v : (v ? [v] : [])).filter(Boolean);
const state = {slackSections: {}, insightsWindow: '90d'};

// The exact string the SERVER composed. The client's only correct behaviour
// is to reproduce it unchanged.
const SERVER_WWR = [
  '1. Guest issue',
  '   a. Tickets never arrived',
  "2. Is the guest's claim accurate? Partially True",
  '3. What actually happened?',
  '   a. Root cause: Selenium fulfilment ran without disclosure',
  '4. Supply Partner escalation',
  '   a. Did CE escalate to SP? N/A',
  '5. Fixes',
  '   a. @CONTENT',
  '   b. Audit the delivery window on TGID 22238',
].join(nl);

const v3d = {
  what_went_wrong: {
    guest_issues: [{issue: 'Tickets never arrived', claim_accuracy: 'Partly accurate',
                    claim: 'I waited all day', pattern: 'one-off',
                    claim_accuracy_note: 'the page states no window',
                    evidence: [{source: 'zendesk', text: 'mail promised 2h', ref: 'ZD-1'}],
                    fix: {action: 'Audit the delivery window on TGID 22238',
                          owner: 'CONTENT', because: 'no window is stated'}}],
    sp_escalation: {escalated: 'N/A'},
  },
  booking_logs: [
    {time: '22 Jul 15:22', what: 'Booking-in-progress email sent', detail: 'tickets promised in 2h'},
    {time: '22 Jul 15:50', what: 'Tickets issued', detail: 'ref 1022394558263'}],
  flags: [{team: 'content', flag: 'Delivery window not on page', evidence: 'redemption null', zd_ref: 'ZD-34011333'}],
  support_interaction: [{time: '15:41', channel: 'chat', summary: 'guest asked for tickets',
                         ce_miss: 'no agent reply for 9 min', zd_ref: 'ZD-34011401'}],
  sp_interaction: {raised: 'Yes', records: [
    {time: '11 Mar 09:20', summary: 'Asked the SP to reissue the tickets.', zd_ref: 'ZD-30994882'}]},
  takedown: {verdict: 'No'},
  area_of_improving: ['Surface delivery window at checkout'],
};
const spV3 = v3d.sp_interaction;
const r = {id: 'tp_1', rating: 1, author: 'David',
           insights: {tgidRating: {value: '4.2', sub: '90d'},
                      completion: {value: '57%', sub: 'vendor'}},
           insightsRaw: {_window_days: 90}};
const b = {bid: '32908218'};
const rca = {v3: v3d, issueL1: 'Operations Issue', issueL2: 'Ticket Issues',
  subTheme: 'C. Ticket Delayed', primaryScenario: 'Tickets sent late',
  overlayScenarios: ['Refund issues'], wwrScenarios: [], wwrChain: [],
  supportInteraction: [], spInteraction: [], areaOfImproving: v3d.area_of_improving,
  actionsTaken: {sp:[],customer:[],business:[],ce:[],product:[]}, resolution: 'Full refund',
  tldr: '', checklistAnswers: [],
  wwrSlackText: SERVER_WWR};

eval(helperSource);
eval(genSource);
const out = _genSlackText();

// ── The server's section arrives unchanged ────────────────────────────────
check(out.indexOf(SERVER_WWR) !== -1,
  'the client did not render the server-composed what-went-wrong verbatim');

// ── The client did not rebuild the section from rca.v3 ────────────────────
// Every one of these is a field the OLD client composer read. If any reaches
// the post, this page is composing the section again.
const mustNotAppear = {
  'the guest quote': 'I waited all day',
  'the accuracy note': 'the page states no window',
  'the pattern': 'one-off',
  'an evidence row': 'mail promised 2h',
  'an evidence ref': 'ZD-1',
  'a stringified fix object': '[object Object]',
};
for (const [what, needle] of Object.entries(mustNotAppear)) {
  check(out.indexOf(needle) === -1,
    `${what} ("${needle}") reached the post — the client is composing the ` +
    `what-went-wrong section itself again`);
}

// ── An empty server section must not silently vanish the heading ──────────
// The composer skips a section whose body is empty, which is correct; what
// must NOT happen is the client inventing a body for it.
const rcaEmpty = Object.assign({}, rca, {wwrSlackText: ''});
const outEmpty = (() => { const rca = rcaEmpty; eval(genSource); return _genSlackText(); })();
check(outEmpty.indexOf('*What went wrong*') === -1,
  'with no server text the client still emitted a What went wrong heading — ' +
  'it is fabricating a section the server did not compose');
check(outEmpty.indexOf('Tickets never arrived') === -1,
  'with no server text the client still printed the issue — it is reading ' +
  'rca.v3 to build the section');

// ── The rest of the post still works ──────────────────────────────────────
const must = ['1. 22 Jul 15:22 — Booking-in-progress email sent',
  '*Booking logs*', '*Flags*', '*Experience insights*', '*Review takedown*',
  '*What went wrong*'];
for (const m of must) {
  check(out.indexOf(m) !== -1, `missing from the post: ${m}`);
}

// Pointer arrays must never print as "a,b,c" comma runs.
check(out.indexOf('Surface delivery window at checkout,') === -1,
  'an array printed as a comma run');

if (fails.length) {
  console.error(out);
  console.error('\n' + fails.length + ' FAILURE(S):');
  for (const f of fails) console.error('  - ' + f);
  process.exit(1);
}
console.log('OK: the client renders the server-composed what-went-wrong verbatim '
          + 'and composes none of it itself');
