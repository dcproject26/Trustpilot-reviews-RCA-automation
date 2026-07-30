// Sections that can legitimately come back empty must still render a heading,
// a reason, and (where the content is editable) the button that lets a human
// fill them in.
//
//   node tests/rca_empty_sections.test.js
//
// The bug this guards: "What Happened" was built as
//     const logsHtml = logRows.length ? `…<button data-log-add>…` : '';
// so a draft with no events rendered no heading AT ALL - and the one case
// where someone most needs to type the timeline by hand was the one case with
// no "+ Add event" button to do it with. The RCA just looked truncated.
//
// Asserted against the source that ships, so it cannot drift from the app.

const fs = require('fs');
const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '..', 'client', 'index.html'), 'utf8');

let failed = 0;
function check(name, cond, detail) {
  if (cond) { console.log(`  ok    ${name}`); return; }
  failed++;
  console.log(`  FAIL  ${name}${detail ? '\n        ' + detail : ''}`);
}

// Slice one `const <name> = …` block, up to the declaration that follows it.
function block(startMarker, endMarker) {
  const a = html.indexOf(startMarker);
  if (a === -1) return null;
  const b = html.indexOf(endMarker, a);
  return b === -1 ? null : html.slice(a, b);
}

console.log('\nRCA sections must not vanish when empty\n');

// ── What Happened ─────────────────────────────────────────────────────────
const logs = block('const logsHtml =', 'const flags =');
check('What Happened block found', logs !== null);
if (logs) {
  check('not gated on logRows.length',
    !/const logsHtml = logRows\.length \?/.test(logs),
    'the whole section collapses to "" when there are no events');
  check('renders the "+ Add event" button unconditionally',
    logs.includes('data-log-add'),
    'a human cannot write the timeline by hand');
  check('explains why it is empty',
    logs.includes('rca-empty') && /logsEmptyWhy/.test(logs),
    'an empty stepper with no reason reads as a rendering bug');
  check('does not end with the empty-string fallback',
    !/\}\` : '';\s*$/.test(logs.trimEnd()),
    'still has a ": \'\'" tail');
}

// ── TL;DR ─────────────────────────────────────────────────────────────────
const tldr = block('const tldrHtml =', 'const w = v3d.what_went_wrong');
check('TL;DR block found', tldr !== null);
if (tldr) {
  check('TL;DR renders its heading even with no tldr object',
    !/const tldrHtml = t \? \`/.test(tldr),
    'a missing headline that leaves no gap reads as "no mistake was made"');
  check('TL;DR says why it is empty',
    tldr.includes('rca-empty'));
}

// ── sections that already had empty states must keep them ─────────────────
const flags = block('const flagsHtml =', 'const sop =');
if (flags) {
  check('Flags keeps its empty state',
    flags.includes('rca-empty') && flags.includes('data-flag-add'));
}

// ── collapse state must not be keyed on live text ─────────────────────────
// Section labels carry counts that change with the data ("What Happened AI
// 6 events"). Keying collapse state on that text meant the key moved when
// the data moved - and because the state is shared across reviews, a section
// collapsed on one review stayed collapsed on all of them, so a populated
// timeline rendered as a bare heading.
const collapse = block('col.querySelectorAll(\'.section\').forEach', '// Issue-answer evidence');
check('collapse block found', collapse !== null);
if (collapse) {
  check('collapse key is not raw label text',
    !/const key = \(label\.textContent/.test(collapse),
    'the key changes whenever a count in the heading changes');
  check('collapse key prefers the section id',
    /sec\.id/.test(collapse));
  check('fallback key strips live counts and the AI mark',
    collapse.includes('.sec-count') && collapse.includes('.ai-tag-inline'));
}

console.log(failed ? `\n=== ${failed} FAILED ===\n` : '\n=== ALL CHECKS PASSED ===\n');
process.exit(failed ? 1 : 0);
