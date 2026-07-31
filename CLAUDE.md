# Working rules for this codebase

Two rules earned the hard way. Both describe failures that shipped here, passed
review, and sat green in a test suite.

## 1. "I ran and found nothing" must not look like "I did not run"

Any lookup, guard, repair or join must be able to say it ran and found nothing,
in words distinguishable from having not run at all. A broken mechanism and an
empty result producing identical output is the single most repeated bug in this
project. Three in one week:

- `validate()` was written, tested, and called by nothing. A validator wired
  into no path looks exactly like one that works — every test green, raw model
  tokens still reaching the screen.
- The contact-note join compared `"ZD-4491"` against `ticket_id "4491"` and
  matched nothing. Indistinguishable from a model that returned no notes.
- `show_draft --bid` keyed on `bookingId`; the warehouse writes `id`. It
  answered `no draft found` — the same sentence a genuinely absent row gets.

In practice:

- Count what you could not do and say so. `"3 model note(s) could not be joined
  to a Zendesk frame (ZD-9999) — rendered as unmatched, not dropped"` beats a
  silent zero.
- Say it where the reader is. Coercions go to the confidence trail as `warn`,
  not `pass` — a repair is "we changed the model's answer", not "a step
  succeeded".
- An error should name what would work. `try --review tp_o` is the useful
  version of "not found".
- Separate a failure from a legitimate empty. A note with no `zd_ref` is the
  model complying with a rule; a note with an unmatched `zd_ref` is a broken
  join. Merging them makes a healthy run look faulty, which is the inverse bug
  and just as bad.
- Announce a judgement. Grouping events by a 30-minute window is a guess; the
  trail says one was made.

## 2. A test that asserts text exists in source is a spelling check

`assert "draft.flags " in PIPE` passes just as happily against a build where
the line it names is unreachable. Two guarantees in `test_rca_v4_persist.py`
were exactly this, written the same week the failure mode was flagged, and were
caught only by mutation testing.

The general answer is to move the logic into something that can be driven —
`project_v4()`, `contact_join_notes()` — and test the behaviour. Source
assertions are acceptable only for:

- **Negative** assertions (`assert "draft_response_v2(" not in PIPE`).
  Unreachability cannot defeat "this string appears nowhere".
- Client-side JavaScript, which has no test harness here. Say so in the
  docstring when you do it.

## Mutation testing

**Standing order: mutation-run the diff before every push.** Not the suite
periodically — the diff, every time.

New work is exactly where tests are weakest, because the thing is fresh enough
that it obviously works. It obviously works *today*, which is not what a test
is for. Two runs in one week make the point: the first found 3 of 10, the
second 3 of 10 again, and on both occasions **every survivor was code added in
that same sitting**. Not one was old.

Run every fix through `tools/mutate.py`, which works on a **copy** of the tree:

    python3 tools/mutate.py mutations.json

A killed run previously left a deliberate bug in `slack.py`; a subset test run
passed with it in place. Never mutate the tree you are about to commit. An
unapplied mutation reports as SKIP, not as a pass — a mutation that never
applied is not evidence of anything.

## Before committing

Run the whole suite, not a subset. Cherry-pick to `tmp-main` off
`trustpilot/main` and run it again there before pushing to `main`.
