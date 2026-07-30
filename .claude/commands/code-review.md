---
description: Review the working diff for bugs, correctness and risk before it ships
---

Review the changes in this repository's working diff — the code that is about
to ship, not the whole codebase.

Scope, in this order:
1. `git diff` and `git diff --staged` (uncommitted work)
2. If both are empty, review the commits on this branch that are not yet on
   `trustpilot/main` (`git log --oneline trustpilot/main..HEAD` then
   `git diff trustpilot/main...HEAD`)

For each file in the diff, look for, in priority order:

- **Correctness**: a case where the new code produces a wrong result or throws.
  Name the concrete input or state that triggers it. This project has been bitten
  repeatedly by silent wrongness — a value quietly defaulted, an exception
  swallowed, a field written over — so weight those heavily.
- **Data loss**: anything that overwrites or discards stored work, especially
  human edits (`final_response`, `slack_thread_override`, `resolution`,
  `sent_at`) or a match that took an expensive pipeline run to compute.
- **Contract drift**: a field the client reads that the API no longer sends, a
  route the client calls that no longer exists, a DB column the model declares
  that the table lacks, a prompt key the renderer expects.
- **Failure visibility**: a new `except: pass`, a default that hides an outage,
  a log line where a raised error belongs.
- **Test honesty**: a test that would pass with the fix reverted. Say so.

Rules for the review itself:
- Verify each finding against the actual code before reporting it. A plausible
  bug that does not reproduce costs more than silence.
- Report findings most severe first, with `file:line`, the failure scenario, and
  what to change.
- If nothing survives verification, say that plainly rather than padding.
