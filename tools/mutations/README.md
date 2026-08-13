# Mutation specs

Every spec run against this tree in the 11–12 Aug 2026 session, kept because
they are the record of *which guarantees were checked* — a passing suite does
not tell you that, and re-deriving these costs an afternoon.

    python3 tools/mutate.py tools/mutations/<spec>.json \
      -k "not test_an_unopenable_database_is_a_sentence_not_a_traceback" \
      --fail-fast

The `-k` is not optional in a sandbox. `test_db_migration.py::test_an_unopenable_database_is_a_sentence_not_a_traceback`
cannot reach `127.0.0.1:1`, fails identically on unmodified `b5c22ad`, and a
red baseline aborts the run with `BASELINE IS RED`.

## Validate anchors before you run

`find` must appear **exactly once**. An anchor that does not match is reported
as SKIP, and **a SKIP is not a pass** — it is a mutation that never happened.
One spec here shipped with a zero-match anchor and would have reported a clean
run. Cheap check:

```python
import json
spec = json.load(open("tools/mutations/mut_csv.json"))
for m in spec:
    src = open(m["file"], encoding="utf-8").read()
    assert src.count(m["find"]) == 1, m["name"]
```

## What each covers

| spec | subject |
|---|---|
| `mut_absent.json`, `mut_absent_ticket.json` | case findings: absent-source rows counted, never deleted; `ticket` deliberately not a source noun |
| `mut_actor.json`, `mut_actor_ctr.json` | the summariser does not decide who acted; the instrumentation counter cannot fail a run |
| `mut_shape_entry.json` | the timeline-shaping trail line, incl. its call site in `process_review` |
| `mut_digest.json` | one digest rule; all three predicates delegate; the candidate picker scrubs |
| `mut_lang.json` | reply language: `en` is not English, `skipped_english` stays dead, the five outcomes stay distinct |
| `mut_callsite.json` | `ensure_zendesk_guest_name` is actually *reached* by the pipeline |
| `mut_csv.json`, `mut_csv2.json` | vendor, insights, the parallel lists, resolved v3, formula injection |
| `mut_final.json`, `mut_final2.json` | re-runs of survivors from earlier passes |

## What the runs actually found

**8 runs, 56 distinct mutants, 56 eventually killed.** Nine survived a first
pass. Seven were real test gaps; two were genuine equivalent mutants; one was a
mutant *written wrongly* (`return "" or (f"...")` evaluates to the f-string, so
it changed nothing and proved nothing).

**The pattern in the survivors, which recurred three times in one day:**
thorough tests on a function, **zero tests on the line that calls it**.
`shape_counts_entry`, `ensure_zendesk_guest_name`, and the resolved-v3 read
each had 6–13 passing tests, and disabling the call site passed the entire
3500-test suite. **Always mutate the call site, not just the function.**

Two equivalent mutants are recorded rather than "fixed", because inventing a
test for them would be inventing a guarantee:

- `looks_like_digest`'s `" " in s` clause — neither alphabet contains a space,
  so a spaced value falls out at the encoding-alphabet guard regardless. The
  clause was **deleted**.
- `review.language = found or "English"` — `found` is guaranteed truthy at that
  line because the empty case returns earlier.
