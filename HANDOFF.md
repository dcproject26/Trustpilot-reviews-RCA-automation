# Handoff — ORM RCA automation (Headout / Trustpilot)

Written 12 Aug 2026 to move this work to a fresh Claude session with nothing
lost. Everything below is verified against the repository, not recalled.

---

## 0. Read this first

**`CLAUDE.md` in the repo root is the contract.** Read it before touching
anything. Its two rules are not style advice — they describe failures that
shipped here, passed review, and sat green in the test suite:

1. **"I ran and found nothing" must not look like "I did not run."** A broken
   lookup and an empty result must never produce the same output.
2. **A test that asserts text exists in source is a spelling check.** Source
   assertions are allowed only as *negative* assertions, or for client-side
   JavaScript (say so in the docstring).

Plus the standing order: **mutation-run the diff before every push** —
`python3 tools/mutate.py <spec>.json`, which works on a *copy* of the tree.

### The user's own process rules, learned over this session

- **No subagents unless asked.** They will say "do this with the agent" when
  they want one.
- **"Plan first, then code"** on anything non-trivial.
- **"Don't commit unless you're sure it's working yourself."** Verify agent
  reports by re-running the claim; several were subtly wrong.
- **"Verify and then tell me it's done. No mistakes allowed."**
- They dislike over-engineering. When they say *keep it simple*, delete the
  extra machinery — on the reply-language work their design was simpler than
  the proposed one and it was right.

---

## 1. Where the work lives

| | |
|---|---|
| Branch | `claude/vectorshift-pipeline-review-coj74p` |
| Remote | **`trustpilot`** → `https://github.com/dcproject26/Trustpilot-reviews-RCA-automation.git` (see the warning below) |
| Head | `88157d6` |
| State | tree clean, 0 ahead of the remote |

**PUSH TO `trustpilot`.** `origin` points at `dcproject26/Claude`, an
unrelated repo that has never held this branch — pushing there fails with 403.
It was repointed at the canonical URL during this session and **the environment
reset it back**, so check `git remote -v` before every push rather than trusting
it. The stale `origin` is also why the stop hook reports ~586 phantom "unpushed
commits": a local tracking ref pinned at `56754f3`, divergent from this history.

    git push -u trustpilot claude/vectorshift-pipeline-review-coj74p

### Test suite

**3576 passed, 2 skipped.** 184 test files.

**One test fails environmentally and always will in a sandbox:**
`tests/test_db_migration.py::test_an_unopenable_database_is_a_sentence_not_a_traceback`
— it cannot reach `127.0.0.1:1`, so the migrator refuses on "identity unknown"
before reaching the path under test. **It fails identically on unmodified
`b5c22ad`.** It blocks mutation runs with `BASELINE IS RED`, so every run in
this session used:

```
python3 tools/mutate.py <spec>.json \
  -k "not test_an_unopenable_database_is_a_sentence_not_a_traceback" --fail-fast
```

---

## 2. What shipped, and what it fixes

Newest first. Every commit below is on the remote.

| commit | what it fixes |
|---|---|
| `b202e7c` | two CSV guarantees a mutation run found untested |
| `cd2e633` | guest name asked on every match path; four CSV columns that said nothing |
| `59aba08` | one digest rule; reply language established rather than defaulted; §1 no longer told to restate the timeline |
| `250aa54` | the timeline-shaping trail line made testable rather than spell-checked |
| `8fa1bfc` | case findings: stop writing one negative per unreadable source |
| `1c6b02e` | "Not saved — Failed to fetch": retry a request the server never received, never one it refused |
| `a8b6a10` | stop rendering Primary guest until the hash problem is solved |
| `693cc8d` | sort the Zendesk timeline by its own key; render the guest's reply at all |
| `595af9c` | the summariser does not get to decide who acted |
| `6c7dbb0` | say when Zendesk was never searched instead of showing an empty timeline |

### The chain that caused most of the visible symptoms

`if bid_for_zd:` with **no `else`** → an empty Zendesk record → prompt rule 10
narrating the guest's review as if it were events → plus `N/A` commentary, the
guest's reply never rendered, the timeline never sorted, actors invented, and a
`KeyError` in an instrumentation counter crashing the run and discarding every
validation guard. Seven bugs, one root. All fixed and verified on live data at
the time: `ghost rows: 0`, `N/A fields: 0`, `Sorted. 36 of 37`.

---

## 3. Detail on the recent work

### 3.1 Reply language (`59aba08`) — the user's own spec

Their words: *"the user at the end of the day is being able to edit the response
in English and copy it in the language of the review"*, and — critically —
*"we can show one box but make sure this model doesn't break then and show one
box even for other languages."*

**The rule: one box ONLY on positive evidence of English. Everything else,
including "we could not tell", draws two boxes.**

**The bug.** `slack.parse_review()` hard-coded `language: "en"` on every review
and nothing ever updated it, so `"en"` meant *nobody looked* while reading
exactly like *this is English*. On top of that, `resolve_language()` returned
`skipped_english` — "it is English" — whenever `body_english` was empty, which
is equally what a **crashed** translate call leaves behind. An Italian review
whose translation failed drew one English box, offered no route to the guest's
language, and sent English to a guest who did not write in English.

**The fix, with no new column:**

- Ingest writes `language = None`. Live rows still carrying `"en"` are treated
  as unestablished.
- A *detected* language is a **name** — `"Italian"`, `"English"`. A name present
  means somebody established it; the two-letter code is the old default.
  **Nothing may ever write `"en"` back into that column.**
- The `skipped_english` shortcut is deleted. Nothing reports English except the
  detector saying so.

**Five outcomes, kept distinct** (`server/services/reply_language.py`):

| outcome | meaning | card |
|---|---|---|
| `skipped_known` | already recorded | uses it |
| `detected` | the detector named it | one box if English, two otherwise |
| `undetected` | read the review, could not place it | two boxes, says it looked |
| `unavailable` | **could not run** — Anthropic offline, or no stored text | two boxes, says it is a deployment fault |
| `failed` | the call raised | two boxes, says the lookup broke |

`is_live("anthropic")` is consulted only to *explain* an empty answer, never to
gate the call — a gate sits in front of the detector and cannot be driven by a
test that stubs it.

**A contradiction guard.** If the column says English but `body_english` differs
from `body_original`, a translation demonstrably happened and that wins → two
boxes. One spare box costs nothing; an English reply to a non-English guest
costs a lot.

**Three bugs surfaced while doing this, all found by tests:**

1. Once detection could answer "English", the endpoint went on to translate the
   reply **English → English** — a paid call that paraphrased the associate's
   wording. Guarded in `api.py`.
2. The contradiction above had no resolution.
3. The 409 said *"Name the guest's language on the card"* — that input was
   **deleted** when detection replaced it. Verified: the client only ever posts
   `{english}`, and the sole match for a language field is a comment recording
   its removal. The message now names what works (type in the top box), with a
   negative source assertion so it is revisited if the field returns.

**No client changes were needed.** The English box already auto-applies on blur
(only when the text changed), and Copy already takes the outgoing box.

### 3.2 One digest rule (`59aba08`)

Three predicates implemented "is this a hash or a name" and **disagreed in
opposite directions on the same values**:

```
value                              api    pipeline  bigquery
FjpJxbSfpb65bnyQwErTyUiOpAsDfGhJ   False  True      True     <- a real digest
ab24TSVenneb4T3CkHFUFaGM           False  True      True     <- a real digest
Papadopoulopoulos                  False  True      True     <- a real NAME
```

`api._looks_like_hash` required one of `+ / = _`, so a plain **alphanumeric**
digest walked through — and `_scrub_candidate_names` uses that predicate, so the
digest reached the **candidate picker**, the one field an associate recognises
the right booking by. The first value is `a8b6a10`'s own fixture for "a PII
hash".

Now one implementation, `names.looks_like_digest()`; the other three delegate.
A fourth copy, `bigquery.NOT_A_HASH_SQL`, was **interpolated into no query** —
defined and referenced by nothing — and was deleted.

**Two decisions worth preserving:**

- A "4+ case flips means machine-generated" rule was written and then **measured
  away**: real names flip 7–9 times (`VanDerBergVanHouten`,
  `JeanPierreDeLaCroix`), and sampling 200,000 base64 digests produced **eight**
  that were letters-only — 0.004%. It would have blanked real names to catch
  four digests in a hundred thousand.
- The explicit `" " in s` test was deleted as a **true equivalent mutant**:
  neither alphabet contains a space, so a spaced value falls out at the
  alphabet guard regardless.

**Still unverified:** the rule was tuned against the real values in the codebase
plus sampled base64, **not against production data**. Run
`tools/check_support_search.py::_hashed_name_share` with live BigQuery to see
the real shape of the digests.

### 3.3 Guest name on every path (`cd2e633`)

`zendesk.guest_name_for_bid()` existed, worked, returned a typed reason, and had
**one call site** — inside the Tier-1 gate. Tier-2 auto-promote, associate
confirmation, manual entry and the attachment path never called it, so on four
of the five ways a booking is confirmed the card printed *"check the Zendesk
ticket"*: telling the reader to do a lookup the system can do and had not
attempted. That also makes `a8b6a10`'s judgement unsafe — *"the fallbacks
resolve it rarely"* could only ever have been measured over Tier-1 traffic, and
no measurement of it exists anywhere in the tree.

`ensure_zendesk_guest_name()` now runs where all five paths converge. It returns
`asked` **separately from** `name`, because "we asked and Zendesk had none" and
"we never asked" are the two things it exists to keep apart.

### 3.4 CSV export (`cd2e633`, `b202e7c`)

Four defects, all fixed and tested:

- **`vendor` was always empty** on the common path. `bigquery._row_to_dict`
  emits `partner`; the export read `vendorName`, which only `verify_bid` writes.
  `experienceName` beside it matched, which is why the column looked healthy.
- **`insights` was empty on every real draft.** Five camelCase keys
  (`tgidRating`, `completion`, `similarReviews`…) against a snake_case payload —
  **not one matched**. And the test covering it **built the camelCase shape
  itself**, so export and test agreed about a payload the system has never
  produced. A payload that exists and matches nothing now *says so*.
- **`issues` / `owners` / `claim_accuracy` misaligned.** The last two filtered
  blanks while the first kept every row → 3, 2, 2 entries, shifting every
  pairing. Missing values are now `(none)`.
- **The export read the raw `rca_v3` blob**, so a draft carrying the v4
  projection columns exported `issue_count 0` while the card and Slack showed
  them. Now goes through `_resolve_v3_sections`.

**Formula injection defused.** `author` is the guest's own display name;
`=cmd|' /C calc'!A0` came out byte-identical and Excel/Sheets execute it on
open. Prefixed with an apostrophe — the spreadsheet's own "this is text" marker,
consumed on display — rather than stripped, because stripping silently edits
what the guest wrote.

**The CSV key was never a code problem.** Driven in eight configurations: with
`RCA_EXPORT_KEY` unset the endpoint returns 200 with `X-Export-Auth: open`, and
no path demands a key when it is unset. The user has now **deleted the secret**;
it needs a restart, because env vars are read into the process at start.

Verify: `curl -sD- -o /dev/null https://HOST/api/export.csv` → 200 +
`X-Export-Auth: open`.

---

## 4. Open work

### Needs no input

1. **`requester_name_reason` column on `RcaDraft`.** `collect_tickets` now
   records *why* the requester name is empty (a raised `users()` call reads
   differently from a ticket with no requester) but nothing carries it to the
   card, so `api.py` still prints the unconditional *"no requester name on the
   linked Zendesk ticket"*. There is a comment at that line saying exactly this.
   `server/db.py::_ensure_columns` self-heals new columns on deploy, so this is
   cheap.
2. **Decide the Primary guest row.** Only restore it once (1) makes "we did not
   look" impossible, and show the source — "Zendesk ticket guest-name field" and
   "Zendesk requester" mean different things (the requester may be an assistant
   or a parent).
3. **A validator count for §1-vs-timeline duplication.** The prompt now forbids
   restating the events timeline in case findings, but nothing counts it.
   `_case_findings()` is not passed the timeline, so it needs plumbing. **Count,
   never delete** — see §5.
4. **The wider sweep** (run this session, unaudited):
   - **24 swallowed failures** — `except Exception: pass` in `claude.py`,
     `zendesk.py`, `bigquery.py`, `api.py`, `pipeline.py`, `db.py`. Each is a
     place where a broken lookup and an empty result are indistinguishable.
   - **~54 positive source assertions in tests** — `assert "..." in src` /
     `in PIPE`. Some are legitimately client-side JS; many assert Python source
     and would pass against a build where the named line is unreachable.
   - **~34 definitions referenced nowhere else.** Most are FastAPI route
     handlers (false positives — the decorator is the reference). Genuine
     candidates: `WHAT_WENT_WRONG_OBJECTIVE`, `_group_of`,
     `_team_of_improvement` (`checklist.py`), `_search_name` (`bigquery.py`),
     `_rows_via_service_account` (`canned.py`), `CONTACT_THREADS`
     (`zendesk.py`), `checks_for`, `l2_options_for` (`taxonomy.py`),
     `MOCK_CANNED`.

### Needs the user

5. **Regenerate** — the two prompt changes only alter existing cards on re-run.
6. **Redeploy** — everything since ~6 Aug is on the branch, not on the
   deployment. The dev workspace and the published deployment have **separate
   databases and separate builds**; a card the user screenshots may not exist in
   the dev DB at all.
7. **Sheet export to Google Sheets** — still blocked on a credential, not code.
   This project has **no service-account key at all**; BigQuery and Zendesk
   authenticate via Replit connectors (`bq_connector.py`, `zd_connector.py`).
   Needs `GCP_SERVICE_ACCOUNT_JSON` or the `google-sheet` connector (name is
   singular). `.env` is gitignored so a pasted key cannot be staged. **Never
   echo the private key; print only `client_email`.** The CSV export is the
   key-free alternative and works.
8. **Live BigQuery** to tune the digest rule against production data.

---

## 5. Judgement calls made deliberately — do not silently reverse

- **Absent-source case findings are COUNTED, NEVER DELETED.** They are matched
  by wording and a real finding sits a hair away: *"No response was provided to
  the guest for six days"* (real) vs *"No support contact record exists for this
  booking"* (absent). This file already has the scar — its own comment reads
  *"folding REMOVES the row, so a false positive does not mis-attribute a ref,
  it DELETES A REAL FINDING."* The user was told this explicitly and did not
  ask for deletion.
- Reported at **two or more**, not one — a single absence beside real findings
  is worth stating; the pile-up is what reads as repetition.
- `ticket` is deliberately **not** a source noun: *"no ticket was available at
  the gate"* is the guest turned away.
- The digest rule accepts a **0.004% gap** (letters-only base64 reads as a
  name), because the inverse error blanks the field an associate picks a booking
  by.

---

## 6. How this went wrong before — the failure modes that recur here

Worth internalising; each cost real time this session.

1. **Thorough tests on a function, none on the line that calls it.** Hit
   **three times in one day**: `shape_counts_entry`, `ensure_zendesk_guest_name`,
   and the resolved-v3 read. Each had 6–13 passing tests and disabling the call
   site passed the entire 3500-test suite. **Always mutate the call site.**
2. **A test that constructs its own version of the thing under test.** Hit four
   times: `.match()` vs `.search()`, `_Counter(eval(...))`, `trace_contacts`
   keeping its own `guestSaid` read, and the camelCase insights fixture. The
   test and the code agree with each other and both are wrong.
3. **Two implementations of one rule.** Python vs JS `isConversation`, two Slack
   composers, ten `guestSaid` readers, three digest predicates, four copies of
   the hash rule. They always agree at the time — that is how the second copy
   starts.
4. **Inventing a field or method name.** `Z._client()` (real name `_get_client`),
   `Z._merge_shaped`, `d.zendesk_meta`, `booking_logs`. **Check the symbol
   exists before writing code that reads it.**
5. **A scan that did not run looks like a scan that found nothing.** The
   dead-definition scanner in this session reported "0 found" because of a path
   mismatch hidden by a bare `except: continue` — the exact bug it was hunting.
   Assert loudly that a scan actually ran.
6. **Validate mutation anchors before running.** An unmatched anchor reports
   SKIP, which is *not* a pass. One spec here had zero matches.
7. **Backticks inside a JS template literal end the string** — this broke the
   page parse once, directly beneath a comment warning about it.
8. **Duplicate keys in a Python dict literal**: the second silently wins. Nearly
   undid a CSV fix.

---

## 7. Mutation testing record for this session

**8 runs, 56 mutants, 56 eventually killed.** Nine survived at first pass: seven
were real test gaps (now covered), two were genuine equivalent mutants, and one
was a mutant written wrongly (`return "" or (f"...")` evaluates to the f-string,
so it changed nothing and proved nothing).

Specs are in the scratchpad and are worth reusing as templates:
`mut_digest.json`, `mut_lang.json`, `mut_csv.json`, `mut_callsite.json`.

---

## 8. Environment notes

- **Replit.** Dev workspace and published deployment have separate Postgres
  databases and separate builds.
- **Connectors, not keys.** BigQuery and Zendesk authenticate via Replit
  connectors. In a sandbox they are offline — `is_live()` returns `False` for
  `bigquery`, `zendesk`, `anthropic`, `slack`. Anything needing them cannot be
  verified locally; **say so rather than inferring.**
- The deployment URL was **unreachable from the sandbox** (`curl` → `http=000`),
  so which build it serves could not be confirmed from here.
- `client/index.html` is the only live dashboard. `attached_assets/` are
  uploads; `exports/` is a generated report.
- The six inbox tabs (All / Matched / Possible matches / Processing /
  Untraceable / Sent) are **filters over one list feeding one RCA column** — a
  fix to the card reaches all of them.
