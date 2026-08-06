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

### 11.5 DSS again — a lookup, not a subject (supersedes the first pass)

The refinement: *"you have to use dss to figure out what would have been the
next step in the escalation and not use it as an anchor or define or comment
like you have done here."*

So DSS informs the answer and is never what the answer is about. The model
consults it to work out what the correct next escalation step would have been
and writes THAT STEP.

    NO   sop_gap  No DSS path governs a system-initiated vendor reassignment
         fix      Define a DSS path for system-initiated vendor reassignments
    YES  sop_gap  Nobody was required to contact the guest before the
                  rescheduling window closed
         fix      Notify the guest and reopen the window when a reassignment
                  moves the slot inside the cutoff

Rule 2f now leads with that instruction, and the no-row branch says to REASON
the next escalation step from the playbook we do have rather than report the
absence — the absence of a row is an internal fact about our tooling. DSS may
not be named in `root_cause`, `operational_failure`, `sop_gap`, `fix.action`,
`fix.because` or any `evidence[].text`, and `_flag_dss_wording` reports every
field that does. Reported, not rewritten: there is no mechanical way to restate
an analysis correctly, so the sentence stands and the trail says what it is.

### 11.6 Actions Taken — the AND was necessary, not sufficient

A booking reassigned to a new operator without consent, remedied with a partial
wallet credit, showed three rows on the Supply Partner tab: *Verify meeting
point with SP if reported*, *BMS refund error → raise with Leads*, and *Share
ARN number for delayed refunds*. No meeting point, no BMS refund error, no
delayed refund. All three satisfied the AND — routed scenario, flag naming
SP — and all three were still wrong.

The third condition: a row must BEAR ON WHAT THIS CASE FOUND. Relevance is
subject-matter overlap with the root cause, operational failure, SOP gap, fix
and flags, and a row also earns its place from the flag that routed its team —
the flag IS a finding, and its wording is often closer to the guideline row
than the prose of the root cause.

**Deliberately crude**, because the two directions cost different things: a
loose match leaves a row on the card, a tight one silently empties a tab. A
card with NO findings withholds nothing — that would be the filter deciding a
question it has no evidence for, and it would empty every tab on a card whose
RCA failed. `findings` is optional and its ABSENCE IS REPORTED, so a filter
that never ran cannot be read as one that ran and withheld nothing. Three empty
tabs now carry three sentences: nothing flagged, no guideline row, nothing
bearing on this case.

**A hand-typed row is NOT relevance-filtered**, and that is deliberate. The
carry-forward runs after the three conditions, so a row an associate wrote is
appended whatever it says. A person decided it mattered; second-guessing that
with a word-overlap heuristic would discard exactly the work `keep_actions`
exists to preserve, and it would do so silently.

Tests: `tests/test_actions_bear_on_the_case.py`. Two existing "a clean run is
quiet" tests were updated rather than deleted — they now pass findings that
genuinely cover their rows, so "clean" means clean rather than "the filter is
silent about work it withheld".

### 11.7 The scenario chips — three stores for one fact

Reported as *the × does nothing* and *the same scenario three times*. Neither
was an unbound handler: the chip controls were already delegated at document
level, which is the fix this project reached for the last two times it hit a
dead control. This was the other shape.

`scenarios` is the whole ordered list and ALREADY contains the overlays. The
card sent `regenerate-rca` the concatenation `[...scenarios,
...overlayScenarios]`, so every scenario edit appended the overlays a second
time — and that endpoint writes the list it is given straight back over
`d.scenarios`. The chips multiplied on every edit, and the × looked dead
because the removal WAS saved and was then overwritten, one request later, by
a union that still held it. Separately, `patch_draft_v2` derived
`overlay_scenarios` from `patch.scenarios[1:]` AFTER the generic loop had
already assigned it, so a deleted overlay came back derived from a list that
still contained it.

**DECIDED: the primary is not its own overlay.** An overlay is an ADDITIONAL
scenario layered on the primary and a scenario cannot be additional to itself;
a primary sitting in the overlays is what put one chip on the card three times.
`settle_scenarios()` is the one place all three columns are decided —
`scenarios` deduplicated and ordered, `primary_scenario` its first element,
`overlay_scenarios` the rest — called from both endpoints and from
`_draft_dict`, so a row written by an older build renders clean on the next
load rather than only after someone edits it again. The card renders the
primary in one row and the tail in the other, so the list appears exactly once
and both × controls edit the one list at the right offset.

### 11.8 The DSS row is correctable

The lookup can match the wrong row — a delay/late-guide row against a vendor
reassignment. **Half of this was already built**: `state.dssEdit`, the
✎ Edit / ✓ Done toggle, and a `data-v3p` editable that the generic saver
persists into `rca_v3`. What was missing is what made it unusable.

The editable rendered only when `prescribes` was already non-empty, so a lookup
that matched NOTHING left an empty state with nothing to type into. It is
writable in both cases now, with a placeholder saying what to write.

A corrected row was indistinguishable from a matched one. `edSpan` takes an
optional mark path and the generic saver sets it, so `dss.by_hand` is written
by the same handler that writes the value — a field can never be rendered with
a marker nothing sets. It renders as a quiet `by hand` chip, the treatment
`area_of_improving` already uses, and deliberately NOT amber: provenance is not
a warning.

And the prompt was reading the store the person could not change.
`regenerate-rca` passed `d.dss_rec` — the LOOKUP — so a re-run discarded the
correction it was asked to act on. `_dss_for_prompt()` prefers the edited value,
and only when `by_hand` marks it as genuinely edited: the pipeline's own
projection of the same lookup must not replace the richer record with its own
summary, and marked-but-empty must not tell the model the playbook prescribes
nothing.

### 11.9 Resolution & takedown — one control treatment

The block had three. Inline chip selects at 10.5px with a transparent border
and a 5px radius; a full-width ground picker at 12px with an `--input-bd`
border and a 6px radius; and an Edit button carrying its own inline font-size
and padding — an inline style being a control opting out of the system by
definition. Plus two label greys six lines apart, `--dim` against `--muted-2`.

All of them share height, padding, radius and border from one rule now; only
what genuinely differs — a chip's weight and fill, a picker's flexible width —
is set separately. An explicit height rather than a min-height, because a
select sizes to its font and a button to its padding, so equal padding still
produced two different boxes.

The tests MEASURE the computed box off real elements rather than asserting a
class string appears in the file, and one checks the opposite direction:
uniformity must not flatten the takedown verdict's green / amber / neutral
roles, which still have to read apart.

### 11.10 The control census

"Make sure all buttons work" is not something one assertion can prove. What can
be proved is that no control reaches the card unaccounted for. The census
enumerates every `data-*` attribute rendered and requires each to be in one of
four sets: driven by a named test, not a control, undriven by design with the
reason, or a **known named gap**.

The gap is listed rather than folded into the driven set, and its size is
printed on every run: **26 driven, 21 rendered but not yet driven**, by name.
A coverage guard that only reports "nothing unknown" reads identically whether
it covers every control or three of them.

It found two things. `.send-btn` and `.candidate-confirm-btn` are their own
classes, not `.btn`, so they never picked up the pointer cursor the base rule
sets — the two most consequential buttons on the card presented as unclickable.
And the generic toggle driver reported `data-trail-toggle` dead, which it is
not: it re-renders the REVIEW column and the driver was watching only the RCA
column. That false alarm is this codebase's own failure mode one level up, so
the driver watches both columns now.

**The 21 undriven controls are a known gap, not a hidden one.** They are named
in `NOT_YET_DRIVEN` in `tests/test_controls_actually_work.py`. Closing it is
ordinary work: each needs a test that clicks it and asserts the change reaches
the server and survives a re-render.

### 11.11 Mark sent, beside the button people work from

`/post-rca` sets `rca_posted_at` and NOTHING else, so a matched review whose
RCA had gone to the Slack thread stayed in Matched. Only `/send` moves a review
to Sent, and Send ↑ lives in the RCA column header while the work happens in
the Slack-post block at the bottom.

**One endpoint, not two.** The control calls `/send` — the same endpoint Send ↑
calls. Two code paths to one outcome is how the RCA got posted into a thread
twice; `/send` already refuses to post when `rca_posted_at` is set and that
guard is reused rather than re-implemented. **Disabled until the RCA is in the
thread**, because enabled earlier it would call `/send` with nothing posted and
`/send` posts in that case.

`sent_route` is DERIVED server-side from whether the RCA was already in the
thread. A route the client asserts is a route that can be wrong. Four values,
because four different pieces of work end in Sent: `reply`, `rca_posted`,
`no_rca`, `closed`.

**On "does this duplicate Send ↑":** in the state where it is enabled, yes —
both mark sent and neither posts. That is deliberate: two placements of ONE
action calling ONE endpoint. If one must go it should be Send ↑, because the
header control is the one that can still post an RCA as a side effect of
"sending", which is the ambiguity that caused the double post.

### 11.12 A guest name is not a verified match

A review with no booking id in its text came back **T1 · BID 33211960** over a
trail reading `venue='—' city='—' visit≈'—'`.

The id came off a Zendesk ticket found by searching the guest's name. The
auto-promote gate was `_conf >= 3.0`, and `_name_pts` returns `3.0 * max(...)`
— so a full name agreement scored exactly 3.0 and cleared the gate ALONE.
§10.2 already states the asymmetry for `bid_indicator_check`: venue, city and
date decide, the name corroborates. The promotion rule was the same claim from
the other direction, and the two live 300 lines apart.

`tier1_promotable(conf, corroboration)` requires the threshold AND agreement
from something other than the name. Its own function so the rule can be driven
— the only test of it asserted that `_conf >= 3.0` appeared within 300
characters of another string, which broke when a comment was added above the
line.

The two trail lines were both TRUE and one was badly worded: "No booking
matches these indicators" came from the indicator-shortlist step, which
genuinely found nothing, while the booking came from the later requester
lookup. It now names which search reported the miss.

### 11.13 A water park is not a Colosseum booking

A Colosseum review produced a German water park and a New York observatory as
possible matches. Four causes:

- **The hint is a phrase, not a venue.** The resolver matched the whole string
  against `experience_name` with `LIKE '%...%'`. Guests write sentences.
  Resolution is now on the significant WORDS inside the phrase, with generic
  travel vocabulary dropped and a token matching 100+ experiences discarded as
  non-discriminating.
- **Adjacent pairs, tried first.** "London Eye" tokenised to words of five
  characters or more leaves "london", a CITY. The pair is a far more specific
  probe than either half.
- **A bare place name is never a lone probe**, and two place names are not a
  pair. `Rome, Italy` yields NO probe, which is the honest answer. The place
  vocabulary READS `bid_indicator_check.CITIES` rather than copying it, with a
  test that fails if they diverge.
- **A narrow spelling tolerance.** Second pass only, tokens of 7+ characters
  only, never more than a quarter of the word to a maximum of two edits.
  "collosseum" qualifies; "rome", "paris", "italy" do not — at five characters
  an edit distance of two reaches rome/roma/rope/role.

**DECIDED: a date-only shortlist is withheld, not ranked.** Proximity ranking
with no venue resolved is noise with an ordering on it. Three unrelated
bookings cost three lookups and invite a confirmation, and a wrong booking
confirmed by a person becomes the foundation of the whole RCA. Withheld only
when NOTHING agrees except the date; any venue, name or ticket signal keeps the
list, and a candidate with no recorded sub-scores is left alone because it
cannot be SHOWN to be noise. The withholding is counted and stated.

**Not verified against live BigQuery:** the fuzzy SQL runs only in a real
warehouse, so the budget policy is tested and the query is not. It is guarded
and degrades to exact matching.

### 11.14 The five-heading Slack post, and the reply's language — BOTH BUILT

Both items §11.14 previously listed as specified-and-not-built are built. The
two open questions it recorded are decided below; each is a decision, not a
guess the next reader has to re-derive.

#### The what-went-wrong section: one composer, five headings

`server/services/wwr_post.py` is the ONLY thing that composes this section.
`services/slack.py` calls it for the post; `_draft_dict` serves the same
string as `wwr_slack_text`, and the dashboard renders that verbatim.

**THE TWO-COMPOSER PROBLEM IS RESOLVED BY DELETION, NOT BY AGREEMENT.** The
client no longer builds the section at all — `_genSlackText` reads
`rca.wwrSlackText` and nothing else, and `persistV3` takes the recomposed text
back off the PATCH response so an inline edit re-renders from the server. Two
implementations kept in step by a test would still have been two
implementations; there is now one, and the agreement is a property of the code.

Guarded from both ends:

- `test_wwr_one_composer.py` drives `format_rca_slack()` and `_draft_dict()`
  and asserts the served string is IN the posted string.
- `tests/slack_post_format.test.js` extracts the real `_genSlackText` from
  client/index.html and runs it under node, asserting the server text arrives
  verbatim and that none of the fields the old client composer read reach the
  post. `test_client_slack_post_js.py` runs it as part of the suite AND
  proves it can fail, by rewriting the client's render line in a COPY of the
  tree and requiring a non-zero exit.
- `test_rca_ui_rendered.py` checks the same thing in a real browser.

**That JS harness had rotted, and the rot is worth recording.** It sat in
`tests/`, asserted the five mandated headings, and had done so for months
against a client that never produced them — because nothing ran it, and it
crashed on a `const`-in-`eval` scoping error before reaching its first
assertion. A harness nobody executes looks exactly like one that passes. It is
wired into pytest now specifically so that cannot recur.

**Open question 1 — what heading 2 prints for `Unknown`.** DECIDED: never one
of the three. The validator's enum is four values (`CLAIM_ACCURACY`), the
user's vocabulary is three, and `Unknown` maps to neither. Printing "No" would
be a coercion that puts a verdict on a guest's claim nobody reached — "No" is
a finding, not an absence. It prints as `Not established (...)`, and the two
kinds of Unknown are told apart by whether `claim_accuracy_note` exists:

- note present → `Not established (checked; the record cannot settle it)`
- note absent  → `Not established (no reason was recorded for this verdict)`
- no verdict at all → `Not established (the RCA recorded no verdict for this claim)`
- outside the enum → the value is NAMED, never mapped

The note's PROSE stays off the post (the user removed it); only which of the
two cases this is reaches the reader.

**Open question 2 — several guest issues.** DECIDED: each issue REPEATS the
whole five-heading structure. Listing them under 1a was the alternative and it
is the flattening this project already fixed once — two complaints with
different root causes went into one list and the reader could not tell which
cause belonged to which. Blocks are labelled `*Guest issue n of N*`; a single
issue is not labelled `1 of 1`. Heading 4 is case-level and so repeats under
each block, deliberately: a block that skipped a mandatory heading because
another block already answered it would not follow the mandated format.

**Empty mandatory headings say WHICH kind of empty.** Sub-points a/b/c are
indicative and an absent one is simply omitted; a heading with NOTHING under
it says so in words ("No root cause recorded — nothing was written under this
heading", "Did CE escalate to SP? Not recorded", "No fix recorded for this
issue, and no team tagged"). A bare `No` under heading 4 says the DND question
went unanswered, because the user asked for that case by name.

**Out of the post, still on the card:** evidence rows, the verbatim guest
quote, `pattern`, `backs_claim`, the per-issue owner chip and the accuracy
note. `test_wwr_one_composer.py::test_the_dashboard_still_has_the_dropped_fields`
pins that this is a change to ONE renderer and not a data loss.

**Legacy drafts go through the same composer.** A pre-v4 draft keeps its
analysis under `what_happened` or in `wwr_scenarios`/`wwr_chain`;
`compose_legacy()` and `_happened_from_document()` fold both into the five
headings. Serving "" for them would make an old RCA look like a broken
composer. A draft whose issues name the complaint but keep the analysis at
document level gets it under heading 3 with a line saying it is case-level —
found by the existing `test_slack_v3_format.py` fixture, which is exactly that
shape.

#### The guest response goes out in the review's language

**ONE STORE, and it is the guest's language.** `final_response or
suggested_response` is the outgoing reply. `services/reply_language.py`
`outgoing()` is the only reader of record, and Copy, Send and the post all go
through it or through `[data-outgoing-reply]`, which is the box it renders.

It used to be the other way round: those columns held ENGLISH, the guest's
language existed only as `state.replyTranslation` in the browser — memory that
did not survive a reload and that nothing on the send path read — and the reply
that actually went out was the English one. That store is deleted.

**The English box is a projection.** `response_english` holds it and
`response_english_of` holds a DIGEST of the outgoing text it projects, so the
reply is not stored twice. When the outgoing box is edited directly the digest
stops matching and `english_view()` returns `stale`, which the card says out
loud rather than presenting a superseded translation as the current reply.
Four states, each distinguishable: `same` (English review, one box),
`current`, `stale`, `absent`.

**The failure contract.** `POST /api/reviews/{id}/apply-english-reply`
translates the English into the outgoing reply. On ANY failure it writes
NOTHING — not the English either, because the half-apply is what leaves a card
showing an edit that will never reach the guest — and the error says the edit
was not applied, why, and what would work ("edit the IT reply directly"). The
client shows `NOT APPLIED — the outgoing reply is unchanged` plus the reason,
and while the call is in flight it says `IN FLIGHT — the boxes disagree`.
Applied on blur or on the button, never per keystroke, and blur only when the
text actually changed.

**An English review draws ONE box.** No English panel, no apply control,
nothing implying a translation happened. **A review with NO language is not
English** — `language_state()` returns `unknown`, the endpoint refuses with a
409 that names what would work, and the pipeline warns on the trail rather
than letting an unlabelled English reply pass as the guest's language.

**A PREMISE IN THE SPEC WAS FALSE, AND IT MATTERS.** The task said
`Review.language` "already exists and is populated by the inbound
translation". It is not. `slack.parse_review()` hard-codes `"language": "en"`
on every ingested review, and step 1 of the pipeline writes `body_english` and
never touches the column. So `language == "en"` is a DEFAULT on most rows, not
a finding — and keying the feature on it alone would have made it dead code
for every Slack-ingested review while looking completely healthy: one box, no
warning, English going out on an Italian review.

Fixed WITHOUT adding a second detection path, which the task forbids and which
would have been the wrong answer anyway. The spec names three fields —
`Review.language`, `body_english`, `body_original` — and the other two already
carry the evidence: step 1 writes `body_english` only when the model returned
something other than `ENGLISH_ALREADY`, so `body_english` differing materially
from `body_original` is positive proof the review was not English. When the
column says `en` and the bodies say otherwise, `language_state()` returns
`unknown` with a why that says exactly that, and the card refuses rather than
sending English. `is_english()` defers to `language_state()` so the card and
the send path cannot each decide it their own way.

**The real fix is upstream and is NOT done:** nothing records the language the
inbound translation detected. Until `parse_review` or step 1 writes it,
non-English reviews land in `unknown` and the associate is told to set the
language or write the reply directly. That is honest, and it is not the
feature working.

**Both write paths apply the rule.** `translate_outgoing()` lives in
`reply_language.py` because the pipeline AND `regenerate-rca` ("↻ RCA only")
both write the reply; a rule applied in one is a reply that reverts to English
every time someone presses the other button. `regenerate-rca` also drops the
previous run's `Reply language` trail line, which would otherwise report the
old outcome over new text. The translation is written into
`rca_v3["suggested_response"]` as well as the column, because `_draft_dict`
reads it from the blob by presence.

**`final_response` protection is unchanged.** It still holds human edits, it
is still in `rerun_all.HUMAN_FIELDS`, and the pipeline still writes only
`suggested_response`. Applying an English edit writes `final_response`, which
is correct: it IS human work.

#### Not done / known

- **The pipeline now makes a translation call on every non-English review.**
  That is what "always" requires. It is guarded and degrades to English with a
  `warn` on the trail.
- `tools/rerun_all.py::_has_human_work` does not count `response_english` as
  human work. It does not need to — the projection only ever exists alongside
  a `final_response` that already marks the row.
- **A test fixture that reloaded `server.api` poisoned twenty-nine tests in
  another file.** `importlib.reload(server.api)` leaves module-level bindings
  to an engine whose temp file is deleted at teardown, and
  `test_one_store_per_v4_section.py` failed only when
  `test_apply_english_reply.py` ran first — green alone, red in the suite. The
  reload was never needed: the session handed to the endpoint is what decides
  which engine the queries reach. If a new test needs the throwaway DB, take
  `live_db` and pass its session; do not reload `server.api`.
- **This session shared the working tree with another agent**, which is why
  `client/index.html`, `server/api.py` and several test files carry changes
  this session did not make (the `_statedIssue` read/write unification, the
  guest-name note). Three failures that surfaced during this work were
  downstream of that change, not of the composer or the reply language:
  `test_the_placeholder_sweep_can_actually_fail` and the two empty-stated-issue
  tests all injected into `r.statedIssue` while the box had started rendering
  from `rca.v3.stated_issue`. They now set BOTH stores. `stated_issue` was
  also added to `_V4_SECTIONS` — it had become an rca_v3-first READ with a
  column-only PATCH, which is the `area_of_improving` bug exactly.

### 11.15 The mutation pass

**The spec is 252 mutations.** Every anchor was verified to match exactly once
against the final tree before launching, and every shard's baseline is green at
1935 tests.

**THAT 252-MUTATION PASS NEVER FINISHED AND HAS NO RESULT.** The container
rewound and took it with it: `/tmp/comb_*.json` and the shard logs are gone,
no worker was running when the next session opened, and 25-of-252 with the
rest unknown is not a result. It has not been re-run as a whole.

**The spec is 296 now** — 44 added for the five-heading composer and the reply
language (`wwr_post.py`, `reply_language.py`, the apply-english failure
contract, the one-composer wiring, the `en`-default detection). Every one of
the 44 was verified to match its anchor exactly once against the final tree
before launching; five had to be RE-ANCHORED after a refactor moved them, and
each would have reported SKIP, which is not a pass.

**THE 44-MUTATION PASS ON THIS DIFF WAS STILL RUNNING WHEN THE SESSION ENDED.**
That is not a result. Three shards in `.snap/mine/`, baselines all green at
2065 passed, and at hand-off **16 of 44 complete — 16 CAUGHT, 0 survived,
0 skipped**. The remaining 28 are UNKNOWN, not passing. Every heading-2
verdict mutation, the empty-heading sentences, the several-issues flattening,
the stringified fix object and the pre-v4 document-level fallback are among
the 16 that are caught; the reply-language and apply-english mutations are
mostly still queued.

It is slow because the older 252-mutation pass is running at the same time:
seven concurrent full suites on four cores, ~10 minutes per mutation. To read
it when it lands:

    grep -h "CAUGHT\|SURVIVED\|SKIP" .snap/mine/m*.log
    grep -c "CAUGHT" .snap/mine/m*.log        # should total 44

A SURVIVOR is a test gap: close it with a driven test and re-run that one
mutation to confirm CAUGHT. A SKIP is an anchor that no longer matches exactly
once — re-anchor it; a mutation that never applied is evidence of nothing.

To read it when it lands:

    grep -h "caught ·" /tmp/combined_*.log        # the four shard totals
    grep -h "SURVIVED\|SKIP" /tmp/combined_*.log  # anything to act on

A SURVIVOR is a test gap: close it with a driven test and re-run that one
mutation to confirm it is CAUGHT. A SKIP is an anchor that no longer matches
exactly once — not a pass, and re-anchoring it is the fix.

**This pass has been killed mid-run four times in this container**, which is
why `tools/mutate.py` now prints a heartbeat line before each mutation: a
worker that died at mutation 9 used to look exactly like one still grinding
through it, same log, same last line, for hours.

**Four shards, not eight.** Eight had them sharing four cores and each full
run stretched past eight minutes. `--fail-fast` is the other half of making
this finish: the verdict is unchanged — CAUGHT is a non-zero exit either way,
and a SURVIVOR still runs the whole suite because nothing failed to stop it —
but a caught mutation stops paying for the rest of a suite whose answer is
already known, which cut a caught run from ~9 minutes to ~90 seconds.

**The last complete pass was the 68-mutation one recorded in §7** (65 caught ·
3 survived · 0 skipped), and all three of its survivors were closed.

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
