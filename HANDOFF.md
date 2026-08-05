# What was built, and why

**This file is a RECORD, not a queue.** Everything below has been built, or is
a decision not to build something with the reasoning attached. There are no
tasks in here waiting for someone. Where a judgement was made and could
reasonably have gone the other way, it says so and says why — a reader
disagreeing with one of these should reopen it deliberately, not discover it
by tripping over a half-finished thing.

Branch: `claude/vectorshift-pipeline-review-coj74p`, remote `trustpilot`
(`https://github.com/dcproject26/trustpilot-reviews-rca-automation`).

---

## 1. Actions Taken — new team vocabulary

The five tabs (SP / Customer / Business / CE / Product) are replaced by NINE:

    NA/Guest error · Supply Partner · Content/Catalog/Media team · CO team
    Tech team · Inventory Team · Product team · Biz team · Finance team

Rules, verbatim in intent:

- A row appears because **the DSS guidelines say it must be raised** AND
  **it has been flagged**. Both, not either.
- **No checks.** "Has the refund been done", "tags added" and every other
  verification prompt stays out. `server/checklist.py::is_check` separates
  these — a question mark, or a leading check-verb with nothing naming who to
  do it with. That guard caught a false positive already ("Check inventory
  with IO" is an action, not a check).
- **Nothing invented.** If no flag supports it, it does not appear.

**BUILT.**

- The nine are `ACTION_TABS` in `server/taxonomy.py` (not prompts.py — that is
  where the constant actually lived) and `ACTION_TEAMS` in
  `server/checklist.py`, which is the one list everything else derives from:
  the flag codes are the tab keys upper-cased.
- `_OWNER_RULES` is rewritten over the nine. The order IS the routing and the
  comment says why each rule sits where it does — inventory before tech,
  finance before CO, content before product, product before SP.
- **The AND lives in `checklist.actions_raised(scenarios, flags)`**, and
  `rca_v4_validate.validate()` calls it: that is the one place holding both
  halves — the routed scenarios and the model's flags. It is projected to the
  `actions_taken` column through `V4_PROJECTION`, so the pipeline and
  regenerate-rca get it from the same code path and neither needed editing.
- What it could NOT raise is counted and named in the notes `validate` returns,
  which the pipeline writes onto the confidence trail. Three different
  sentences for three different kinds of nothing (no scenario routed / no
  guideline action / nothing flagged), and silence when a run is clean.
- `is_check` is untouched, as instructed.
- Tests: `tests/test_actions_routing.py`, `tests/test_nine_team_actions.py`,
  and the chip row in the browser (`tests/test_rca_ui_rendered.py`).

## 2. Flags — routed by the same rules

Flags use the same team vocabulary as Actions Taken. No separate list.

**Decided: Content/Catalog/Media.** The flag *"No Baby/Infant (<1.00 m, free)
pax type exists in the guest-facing booking flow for TGID 20842"* is a catalog
problem — the pax structure on the product does not match the vendor's, and
that is the Content/Catalog/Media team's to correct. It is NOT Product: the
booking flow renders whatever pax types the catalog defines, so the missing
option is an artefact of the configuration, not of the flow.

The general rule that follows: a missing or wrong VARIANT, PAX TYPE, INCLUSION
or PAGE STATEMENT is Content/Catalog/Media. Product is for the flow, app or
site failing to do its job with a correct catalog.

**BUILT.** `FLAG_TEAMS` in `server/services/rca_v4_validate.py` is the nine
plus `OTHER` — which is *not* a tenth team but the marker for a flag whose
team could not be read, and it raises nothing. Flags written under the old
vocabulary are translated (`CE`/`RO` → `CO`, `BUSINESS` → `BIZ`) and the
translation is reported to the trail; the client folds them the same way so an
old draft's flag does not land on the first option in the select. The routing
rule itself — pax type / variant / inclusion / page statement is CONTENT,
the flow with a correct catalog is PRODUCT — is prompt rule `10-teams`.

## 3. Issue-specific answers — remove the section

- Delete the section from the dashboard.
- The questions stay alive server-side and feed the RCA prompt as **checks to
  write against**: a verdict, root cause and SOP gap must be consistent with
  them.
- **When a check surfaces something missed, assess whether it is an
  operational failure or an SOP gap and write it THERE.**

**BUILT.** The section and its handlers are gone from `client/index.html`, and
`issue_specific_answers` is out of the RCA output schema — a section removed
from the card but still asked for comes back on every run and is stored where
nobody reads it. The questions still route (`issue_questions_for`) and still
reach the prompt, under a header saying they are checks to write against.
Prompt rules 12 / 12a / 12b say: answer them from the backend silently, let
them constrain the verdict, and when one surfaces something missed, write it as
that issue's `operational_failure` or its `sop_gap` — deciding which, because
they go to different teams. The validator still coerces the field for drafts
that already hold answers.

## 4. Guest ↔ Support — conversations only

**Chat, call, email, web-form and in-app messages** are contacts. Everything
else routes elsewhere: `BOOKING` to the booking timeline, `API` to the events
timeline marked internal, `REVIEW` to the events timeline as a bookend.

**BUILT.** `zendesk.split_contact_frames()` is the rule, as a DENYLIST
(`NON_CONTACT_THREADS = booking, review, api, sp`, plus anything marked
`is_internal`) rather than the obvious allowlist: an allowlist drops a channel
nobody has classified yet, silently, and a new Zendesk channel name would make
real contacts disappear. Slack filters through it; the client mirrors the same
list, named in a comment on both sides.

The count says what moved — `1 contact · 3 system events moved to the
timeline` — and the empty state distinguishes the two silences: a booking whose
only events are machinery says so, a booking with nothing at all says the guest
never reached support. Same in the Slack post.

## 5. Area of improvement — pointers, not a paragraph

- **One short pointer per line.**
- **Every point traces to an operational failure, the SOP gap, or a flag.**
- **Nothing invented** — the correction to a documented gap, not an opinion
  about a better world.
- **Empty when nothing was found**, not padded with generic advice.

**Decided: yes, provenance — but as a CONSTRAINT ON THE MODEL, not as
decoration.** Each point carries the failure, gap or flag it derives from, and
the value is that it forces the derivation: a point that cannot name its source
is invented and is dropped before it is written. On screen it stays quiet: a
small marker on the point, not a second column.

**BUILT.** The model returns `area_of_improving` as `[{point, from, source}]`,
and `rca_v4_validate._improvements()` CHECKS the source against the operational
failures, SOP gaps and flags on that card — by kind, so a point claiming the
SOP gap and matching only a flag is dropped. Unsourced and unmatched are
counted separately and reported separately: one is a point that never derived
anything, the other claims a derivation it does not have. On screen the marker
is a small grey `op failure` / `sop gap` / `flag` tag with the source in its
title; a point somebody typed says `by hand`, so the marker keeps meaning
"checked against a finding".

**Legacy drafts keep their old-shaped `area_of_improving` strings, and this is
a decision rather than an omission.** They render, marked `by hand`. Provenance
is a constraint on the MODEL — it exists so the next run cannot invent — and
applying it retroactively would delete work somebody already signed off, to
enforce a rule that was not in force when they wrote it. They are deliberately
not retro-derived: a derivation invented after the fact is exactly the thing
the marker is supposed to rule out.

## 6. Classification — website messaging is a Product Issue

The review *"after you buy tickets the website offers a discount on your next
purchase; you only get it if you create an account first; Headout will not
honour it"* → **L1 = Product Issue**. A website that advertises an offer
without stating its precondition is a Product Issue, not Operations /
*Content - Instructions not clear*.

**BUILT, in two places, because one was not enough.** The clause under
`[PRODUCT ISSUE]` is only reached if the model gets there — the priority rule
puts Operations first and says stop at the first match — so the handover is
also written into the Operations `Content - Instructions not clear / Misleading
Info` rule, where the model actually stops. Both halves are asserted in
`tests/test_rca_finding_rules.py`.

## 7. The two mutation survivors — CLOSED

From the 68-mutation pass (65 caught · 3 survived · 0 skipped):

- **`content_match` never reaching the payload.** Mutating
  `"content_match": _content_match(d)` to `None` in `server/api.py` left the
  whole suite green — the mismatch row would silently stop rendering.
- **The `body_original` fallback.** Dropping it meant a review written in
  another language read as unchecked rather than being checked.

Both were closed by testing the WIRE rather than the checker
(`tests/test_content_match_reaches_the_card.py`), and both anchors are in
`mutations.json`. The third, pricing/commercial routing, was fixed at the time.

Both anchors were RE-ANCHORED this session: adding `_indicator_match` beside
`_content_match` in `server/api.py` gave each of them a second identical match,
and an anchor matching twice reports SKIP — which is not a pass. They now carry
enough surrounding context to be unique.

## 8. Runs stopping mid-flight — CLOSED

Both halves had the same shape: nothing anywhere could say "this stopped".

**Why the runs stopped.** Every ingest path handed each review to Starlette as
its own BackgroundTask. Starlette runs those as `for task in self.tasks: await
task()` with no try/except (`starlette/background.py`). Two ways that loses
reviews, both silent: the FIRST task to raise drops every task behind it, and
they run strictly one at a time, so ONE wedged run holds every review behind
it. The Anthropic SDK client had its default 600s read timeout and two
retries — up to half an hour of blocking inside a single synchronous call, with
no await point at which any timeout above could be delivered.

Nothing recorded that the thirteen queued reviews had been queued, so a review
that was queued and never started carried the same evidence as a review nobody
had ever asked for.

**Why `stalled` did not fire.** `processing_state` read `if p:` — the presence
of a progress entry WAS the definition of running. The entry is written at
step 1 and removed in the run's `finally`, so the only run that could ever
reach `stalled` was one that had already finished dying tidily.

**What now exists.** `server/pipeline.py::run_batch` — one supervised task per
ingest, every review marked queued before the first one starts, each run
isolated and bounded by `RUN_TIMEOUT_S` (12 min), and a counted account at the
end. `server/tiers.py::liveness` — one judgement, shared by the inbox row and
the re-run button's poll, turning on a heartbeat (`updated_at`) rather than on
an entry existing. Three states on the card, not two: queued / running /
stopped. `STALL_AFTER_S` is 10 minutes of no stage progress,
`QUEUE_STALL_AFTER_S` is 30 minutes unstarted, and both are ANNOUNCED in the
sentence the reader sees, because deciding a run is dead from elapsed time is a
judgement and nothing ever reports it.

**`process_review` now opens its session inside its own try — CLOSED.** It used
to open it one line above, so a pool timeout or an unreachable database raised
straight out of the function: past its own handler, past the failure it
records, past the finally that pops the progress entry. `run_batch` catches
that, so it could no longer take the queue down, but the function was not
self-consistent — the one failure it could not report was the one that stopped
it reporting anything.

The session moves inside. `db` is then `None` in the handler, and
`record_run_failure` opens its own session, which is a real second chance
rather than a formality: a pool exhausted a moment ago may not be now. Three
endings get three sentences — recorded, no draft row to record it on, and the
database unreachable for the recording too. The finally closes the session only
if there is one; closing unconditionally would raise `AttributeError` out of
the finally and REPLACE the real exception with a misleading one.
Tests: `tests/test_pipeline_session_is_inside_its_try.py`, six of them, all
driven with a `SessionLocal` that fails the way a dead pool fails.

---

## 9. What §1–§6 left behind — all closed

- **`fix.owner` was a third team vocabulary** (`Content | CE | SP | RO |
  Product | Biz | Ops`) while flags and Actions Taken moved to the nine. A card
  could say `owner: RO` beside a `CO` flag about the same failure, and two of
  its values named no chip on the card at all. **CLOSED**: it is the same nine
  now, with the same aliases flags use, so a draft written under the old
  vocabulary is TRANSLATED rather than failed — dropping it to null would lose
  an owner the model got right. `tests/test_owner_and_hand_rows.py`.

- **A hand-added Actions Taken row did not survive a re-run.** The column is
  recomputed from the guidelines and the flags every time, so the work of
  deciding a row mattered was discarded with nothing on screen to say so.
  **CLOSED**: `validate()` takes `keep_actions`, and anything the guideline
  CORPUS does not contain was put there by a person and is carried forward and
  counted. The corpus, not the routed subset, is what decides — against the
  routed rows alone, a row the AND deliberately withheld reads as hand-typed
  and returns through the back door, at which point the AND has quietly stopped
  meaning anything.

- **`pipeline.py` computed an ungated `actions_taken` and assigned it at
  save**, four lines before `project_v4` overwrote it with the gated one.
  Correct output, misleading code: a reader following `draft.actions_taken =`
  landed on the version that does not survive, and the AND §1 is built on
  appeared not to be applied at all. **CLOSED**: the assignment is gone, and
  with it the `actions_for()` call that fed it, which was computed for nobody.
  A second dead line went with it — the `_prev_actions` read just above the
  projection, assigned and never used. The one that matters is at step 12c,
  where `validate()` is handed `keep_actions`, because that is the call that
  decides which typed rows survive.

- **Legacy `area_of_improving` strings** — a stated decision, not an omission.
  See §5.

---

## 10. Confirming a candidate, indicators on a verified BID, a route to Sent,
## and a refund denial that outranked its cause — BUILT

### 10.1 Confirming a candidate appeared to do nothing — CLIENT half

The server half was already fixed by `073ed6a`: `select-candidate` queues
`run_batch_sync`, which supervises, bounds and accounts for the run.

The CLIENT half had two faults, either of which reproduces the report exactly:

- **The poll waited for something that had already happened.** It polled for
  `!draft.candidate_state`, and the confirm request clears and commits that
  flag before it returns. The condition was true when the first poll fired
  three seconds later, so the poll stopped while the pipeline was at step 1.
- **The refresh read the store the renderer does not use.** It called
  `loadDraftOverlays()`, which never touches `r.type` — and `r.type` decides
  whether the RCA column draws the analysis or its gate.

Now: `generated_at` is the completion signal, `/progress` names the stage and
ends the poll on a stalled run, the budget is the runner's own ten minutes, and
`reloadFromServer()` does BOTH halves. `fetchInbox()` returns whether it
actually reached the server, so a refresh that kept stale rows can no longer
report itself as a refresh.

Tests: `tests/test_confirm_candidate_refreshes.py`, and
`tests/test_recent_changes_rendered.py` which additionally watches the Booking
details panel and the RCA fill — a column that unlocks onto nothing reproduces
the original report just as well as one that stays locked.

### 10.2 A verified BID must also match the review's indicators

`server/bid_indicator_check.py`, mirroring `booking_match_check.py`'s
three-state design (match / mismatch / unchecked, never a bool). Four signals —
venue, city, date, guest name — each with its own three states.

**The guest name is reported but never decisive on its own.** People book
under a partner's name, a maiden name, a company name; a name disagreement
alone would flag a large share of correct matches. Venue, city and date decide;
the name corroborates. That asymmetry is the design.

Every signal has an ambiguity guard: two cities in a review is a transfer, two
venues on a booking is a combo, "may"/"march" without a day or year are English
words rather than months, a bare month with no review date cannot be resolved
to a year at all. Anything less than a clear contradiction is `unchecked`.

Wired in ONE place in the pipeline, after the whole match cascade, so every
path is checked identically. It writes one of three trail lines (agree /
disagree / could not be compared) and **never unmatches**. `_indicator_match`
in `server/api.py` recomputes it live for the card, so existing drafts get it
with no migration.

**The two checks on one card are deliberate.** `booking_match_check` and
`bid_indicator_check` answer different questions — same product family vs same
trip — and a reader can now see two warnings that fire independently. That was
weighed and kept: merging them would force one verdict out of two unrelated
comparisons, and the failure they each catch is one the other cannot. They are
drawn as separate lines with separate wording so it is clear they are not two
renderings of one fact.

**The city and landmark vocabularies are deliberately small, and stay that
way.** A missing entry costs an `unchecked`, which is the safe direction; a
sloppy entry costs a false flag on a correct match. They are to be extended
from real misses, not from imagination — that is a standing rule for whoever
touches them, not an outstanding task.

Tests: `tests/test_bid_indicator_check.py` (mostly about NOT firing),
`tests/test_indicator_match_reaches_the_card.py` (the wire, plus the pipeline
trail, driven), `tests/test_indicator_and_close_ui.py` and
`tests/test_recent_changes_rendered.py` (the card draws only on a mismatch,
names only the contradicting signals, and says the match was not undone).

### 10.3 Every bucket now has a route to Sent

`POST /api/reviews/{id}/close` — its own action, not a flag on `/send`.
Sending means the RCA and the reply have gone; closing out means there was
nothing to send. It needs no draft (so the processing bucket can reach Sent),
posts nothing to Slack, records `Review.closed_at` / `Review.close_reason`
(new columns, self-healing via `_ensure_columns`, which now covers both
tables), and writes the close onto the confidence trail.

`/send` no longer posts an RCA that does not exist — `has_rca_to_post()` is a
driveable predicate, and the response says whether it posted and why not,
instead of `{"ok": true, "ts": null}` meaning three different things.

Buttons: the untraceable panel, the candidate picker, and a
nothing-analysed-yet gate for the processing bucket (which previously fell
through to the full RCA layout and offered a Send button that could only 404).
Two clicks to arm, because it moves the card out of every working tab.
"Closed out" is its own inbox chip — a closed review and a replied one are two
different pieces of work and the Sent count must not merge them.

Tests: `tests/test_route_to_sent.py`, `tests/test_indicator_and_close_ui.py`,
and `tests/test_recent_changes_rendered.py`, which clicks through both panels
and asserts the armed click actually POSTs `/close` — a button that renders and
does nothing looks identical until it is clicked.

### 10.4 A refund denial does not outrank the failure that caused it

The Zoomarine review — charged for a child ticket that should have been free,
refund then denied — came back Customer Support Issues (the symptom) instead of
Content/Misleading Info (the cause). The within-Operations order was already
right; nothing said that a denial ARISING FROM another failure is classified as
that failure.

Written in TWO places, for the same reason §6 was: the priority block is read
once, and the "Refund denied" examples under Customer Support Issues are where
the model actually stops. Both carry the Zoomarine worked example, and the rule
comes with a mechanical test — remove the denial; if a complaint remains, that
complaint is the L2. Force majeure is explicitly excluded, so the existing FM
boundary (a refusal after a flight cancellation IS Customer Support) still
holds.

### 10.5 The trail said things the card contradicted — CLOSED

Raised against a real card (BID 33204378, Pena Palace):

- **A PARTIAL BOOKING ROW reached the card.** `_make_candidate()` builds what
  the picker needs; the auto-promote paths hand that same narrow dict through
  as the matched booking. `_get_booking_extra()` is called there — hence
  booking_status and tid_name — but `verify_bid()`, the only query selecting
  `created_at` and `fulfilment_type`, is never called on those paths.
  Fulfilment type, booking date, partnered vendor and lead time were not empty
  in the warehouse; nobody asked for them. `complete_booking_row()` does the
  same merge `select-candidate` already did, in one place every path passes.
  **This is a behaviour change** — presentation alone cannot fill a field that
  was never fetched. Now also watched rendering, end to end: the real function
  is driven, its output goes through the real client mapping, and the four
  fields are read off the panel.
- `last='None'` — Python's None in a sentence a person reads. Gone, and the
  card is now swept for it.
- A green tick on `venue='—' city='—' visit≈'—'`; an empty extraction is a
  finding, not a success.
- "Indicators" meaning the extraction on one line and the search on the next;
  the extraction line is headed "Extracted from review", which is what the card
  calls that panel.
- "Weak BID — number found in text" read as "the id went nowhere". BigQuery
  RETURNED a booking; we scored it and it disagreed. Reworded to say so.
- The floor line and the "not verified" tier label were already dead at HEAD
  (the floor cannot fire while candidates exist); asserted so they stay dead.
- **The PII hash printed as a guest name** (`FjpJxbSfpb65bny/…`) — DECIDED and
  built (`7495014`): it is dropped, and the row says WHICH source came up empty
  instead. A hash is not something a reader can check, and it made three
  different situations identical — the warehouse holds a hash, the linked
  ticket carries no requester, and no ticket was ever matched. The first two
  are worth opening Zendesk for; the third is not. The card now says which.

---

## 11. This session

### 11.1 Two stores for one fact — the takedown chip

The takedown verdict chip renders from `rca.v3.takedown` — the client's only
copy, lifted straight out of the `rca_v3` blob — while everything else reads
the top-level `takedown`, which `_v4()` resolves by preferring rca_v3 and
falling back to the `takedown` COLUMN. Populate the column and leave rca_v3
alone and the two disagree: the payload, the Slack post and the sheet export
say "Yes" and the chip on the card says "No". A test that set the column got a
chip showing the other store.

It was never only takedown. All six v4 sections are read by the client out of
the blob and by everything else through `_v4()`, so all six could diverge the
same way — the same family as the four copies of the team vocabulary behind the
owner bug. So the fix is over the whole map, not the one field that was
noticed.

**ONE store: `rca_v3`. The column follows it.** `project_v4()` already makes
the column follow rca_v3 on the write path; `_resolve_v3_sections()` in
`server/api.py` is the read path's half, folding the column into the blob where
rca_v3 has no such section. `_draft_dict` serves that one blob to the client
AND reads its own top-level fields out of it, so the two are one read rather
than two reads implementing the same rule.

Presence, not truthiness, exactly as `_v4()` does it: a section deliberately
emptied to `[]` still beats a populated column, or a delete would undo itself
on the next load. What was folded is reported as `v4_sections_from_column` — an
empty list rather than a missing key, so a resolver that ran and found nothing
is not silence.

Tests: `tests/test_one_store_per_v4_section.py`, written over the whole
`_V4_SECTIONS` map so a seventh section is covered the day it is added, and
failing loudly if a section is added with no sample value rather than skipping
it.

### 11.2 The five recent changes, watched rendering

`tests/test_recent_changes_rendered.py`, on the shared `page` fixture. Every
check proves its subject exists before it passes and says NOT BUILT rather than
passing over nothing.

Two things the sweep taught us about itself, both recorded because they will
catch the next person too:

- A `<select>`'s `innerText` in Chromium is EVERY option, selected or not, so a
  raw read reports choices nobody is looking at. Selects are collapsed to the
  option actually on screen.
- The compensation-type control offers a literal `None` — one of four
  hand-authored labels meaning "no compensation was given". It is a domain
  value a person chose, not a Python value that escaped into a sentence. It is
  excluded BY NAME, and a test renders a real `None` into the stated issue and
  fails if the sweep no longer catches it. An exclusion that quietly disarms
  the check is worse than no check.

### 11.3 A booking that outlived its match

Found by the harness above rather than reported. Every line of the client's
booking mapping keeps what is on screen when the incoming value is blank
(`db.x || r.booking.x`). That is right for a refresh of the SAME booking, where
a partial payload must not blank the panel, and wrong the moment a re-run or a
confirmed candidate matches a DIFFERENT booking: the old booking's fulfilment
type, vendor and lead time then sit under the new booking's id. Worse than a
dash — the dash says we do not know, the stale value asserts something false.

Cleared only on a real change of id. Clearing whenever the id is absent would
blank the panel on every partial refresh, which is the bug the fallback was
written to prevent. Both halves are pinned by a test, so the fix cannot be
traded for the defect it replaced.

### 11.4 DSS is what a gap is checked against, not what the reader is shown

Two things reached a card and neither should have. An EVIDENCE ROW sourced
`dss` — *"DSS matched row is for 'Tour started late / guide arrived late at
MP'; no row covers a system-initiated vendor reassignment"* — which is a remark
about our own decision sheet's coverage sitting in a list of records of what
happened to this booking. And `sop_gap` / `fix` written as DSS paths: *"No DSS
path governs a system-initiated vendor reassignment…"* and *"Define a DSS path
for system-initiated vendor reassignments…"*. The reader of those fields owns
an operation, not a spreadsheet.

**THE TENSION, RECORDED RATHER THAN BURIED.** Prompt rule 2f said `sop_gap`
COMES FROM DSS, and that rule came from the written what_went_wrong spec
earlier in the same session. This narrows that rule; it does not delete the
concept. The DSS lookup stays — it is how the model knows whether a control
existed and whether it was followed. What changed is what gets WRITTEN: "no row
covers this" stops being the gap and *"nobody was required to contact the guest
before the window closed"* becomes it; "define a DSS path" stops being the fix
and *"require proactive notification when a reassignment compresses the
rescheduling window"* becomes it. Rule 2f now carries both worked examples,
wrong and right, and forbids naming DSS in `sop_gap`, `fix.action` or
`fix.because`.

**The removal is deliberately narrow.** `dss` stays in `SOURCES`, because
`fix.source` records where a gap was READ — a field that never renders on the
card or in the Slack post — and `issue_specific_answers` still uses it. Only
the evidence path is closed, through a separate `EVIDENCE_SOURCES`. Deleting
the value globally would have taken two working things with it.

A row citing DSS keeps its TEXT and loses its source: dropping it would delete
a sentence the model wrote, and the finding may still be worth reading — it is
the SOURCE that was wrong. The demotion is counted and reported, because a
source that quietly became null looks exactly like one the model never
supplied; a clean evidence list produces no note at all, or every healthy run
would carry a warning. The legacy `[dss] …` string prefix is still RECOGNISED
and stripped before being demoted, because leaving it unmatched would render
the bracket inline — the defect the structured fields exist to remove.

The gap and fix wording is **reported, not rewritten**. There is no mechanical
way to restate an analysis correctly: deleting it loses a real finding, and
paraphrasing puts words in the model's mouth. So the sentence stands and the
trail says the field was written about the sheet rather than about the process,
which is something a reader can act on.

Tests: `tests/test_dss_is_not_a_finding.py`.

### 11.5 The mutation pass

MUTATION_SUMMARY_PLACEHOLDER

`tools/mutate.py` gained `--fail-fast`. The verdict is unchanged — CAUGHT is a
non-zero exit either way, and a SURVIVOR still runs the whole suite because
nothing failed to stop it — but a caught mutation stops paying for the rest of
a suite whose answer is already known. It cut a caught run from ~9 minutes to
~90 seconds. The BASELINE is always a complete run.

---

## Working rules that cost time when ignored

- `CLAUDE.md` governs. Mutation-run the diff before every push; run the WHOLE
  suite before committing.
- **Never `pkill -f`.** It matched a session's own shell three times
  (exit 144). Identify by PID via `ps`, kill by number.
- **Never commit while a suite is running.** HEAD moving under a running test
  server made `/api/version` correctly report the process as stale, and a
  build-banner test failed for a reason that had nothing to do with the code.
  Mutation shards are the exception and it is worth knowing why: `tools/mutate.py`
  runs against a COPY in a temp directory with no `.git`, so `git rev-parse`
  there fails and reports `unknown` regardless of what this repo's HEAD does.
  Verify that (`cd /tmp/mutate-*; git rev-parse HEAD`) rather than assuming it.
- **Do not put comments containing backticks inside JS template literals.** The
  backtick ends the string and breaks the whole client.
- **Assert an anchor matches before any `str.replace`.** A replace that
  silently matches nothing has cost this project real time. The same applies to
  `mutations.json`: an anchor matching 0 or 2 times reports SKIP, and a SKIP is
  not a pass.
- The mutation runner runs the full suite per mutation. Shard
  `mutations.json` across workers (`ms[i::N]`) — on this 4-core box eight
  shards with `--fail-fast` is roughly the throughput ceiling; the suite is
  ~2 minutes of CPU inside ~7 minutes of wall time, so it parallelises well
  past the core count but not indefinitely.

## Recovering from a container rewind

The container has rewound and lost all uncommitted work more than once, and it
reverts the git remote to a dead local proxy when it does. Commit and push
often. If `git ls-remote trustpilot` fails:

    git remote set-url trustpilot https://github.com/dcproject26/trustpilot-reviews-rca-automation
    git fetch trustpilot

Push with `git push -u trustpilot claude/vectorshift-pipeline-review-coj74p`.
