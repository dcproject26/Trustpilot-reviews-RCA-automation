# Outstanding work — specified, then built

Written at the end of a session that ran out of context. Every item below was
decided by the user in conversation. Nothing here needs re-litigating; build it
as written. Where a judgement is still open it says so explicitly.

**§1-§6 are BUILT.** Each section now ends with a BUILT note saying where the
decision lives in code and which test would fail if it were undone. What is
left open is named in §9.

Branch: `claude/vectorshift-pipeline-review-coj74p`, remote `trustpilot`
(`https://github.com/dcproject26/trustpilot-reviews-rca-automation`).

---

## 1. Actions Taken — new team vocabulary

The five tabs (SP / Customer / Business / CE / Product) are replaced by NINE,
given by the user as the authoritative list:

    NA/Guest error
    Supply Partner
    Content/Catalog/Media team
    CO team
    Tech team
    Inventory Team
    Product team
    Biz team
    Finance team

Rules, verbatim in intent:

- A row appears because **the DSS guidelines say it must be raised** AND
  **it has been flagged**. Both, not either.
- **No checks.** "Has the refund been done", "tags added" and every other
  verification prompt stays out. `server/checklist.py::is_check` already
  separates these — a question mark, or a leading check-verb with nothing
  naming who to do it with. Keep that guard; it caught a false positive
  already ("Check inventory with IO" is an action, not a check).
- **Nothing invented.** If no flag supports it, it does not appear.

Touches: `ACTION_TABS` in `server/prompts.py`, `_OWNER_RULES` and
`actions_for()` in `server/checklist.py`, the chip row in `client/index.html`,
and every test naming the old five (`tests/test_actions_routing.py`,
`tests/test_actions_are_not_checks.py`).

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
  which the pipeline already writes onto the confidence trail. Three different
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

**BUILT.** `FLAG_TEAMS` in `server/services/rca_v4_validate.py` is now the
nine plus `OTHER` — which is *not* a tenth team but the marker for a flag whose
team could not be read, and it raises nothing. Flags written under the old
vocabulary are translated (`CE`/`RO` → `CO`, `BUSINESS` → `BIZ`) and the
translation is reported to the trail; the client folds them the same way so an
old draft's flag does not land on the first option in the select. The routing
rule itself — pax type / variant / inclusion / page statement is CONTENT,
the flow with a correct catalog is PRODUCT — is prompt rule `10-teams`.

One thing deliberately NOT changed: `fix.owner` still uses its own older
vocabulary (`Content | CE | SP | RO | Product | Biz | Ops`). It is a different
field with different downstream logic and was not in scope; aligning it is the
obvious next tidy-up.

## 3. Issue-specific answers — remove the section

- Delete the section from the dashboard.
- The questions stay alive server-side and feed the RCA prompt as **checks to
  write against**: a verdict, root cause and SOP gap must be consistent with
  them.
- **When a check surfaces something missed, assess whether it is an
  operational failure or an SOP gap and write it THERE.** The user was
  explicit about this. It does not become a trail line or a count.

**BUILT.** The section and its handlers are gone from `client/index.html`, and
`issue_specific_answers` is out of the RCA output schema — a section removed
from the card but still asked for comes back on every run and is stored where
nobody reads it. The questions still route (`issue_questions_for`) and still
reach the prompt, under a header saying they are checks to write against.
Prompt rules 12 / 12a / 12b now say: answer them from the backend silently, let
them constrain the verdict, and when one surfaces something missed, write it as
that issue's `operational_failure` or its `sop_gap` — deciding which, because
they go to different teams. The validator still coerces the field for drafts
that already hold answers.

## 4. Guest ↔ Support — conversations only

Confirmed by the user: **chat, call, email, web-form and in-app messages** are
contacts. Everything else routes elsewhere:

| Currently in contacts | Belongs in |
|---|---|
| `BOOKING` — booking created | Booking timeline (facts column) |
| `API` — booking details posted, tickets sent, system/bot notes | Events timeline, marked internal |
| `REVIEW` — review posted | Events timeline bookend |

The count must say how many moved — `1 contact · 3 system events moved to the
timeline`. A filtered list and a guest who never wrote in must not look the
same.

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

It currently emits one paragraph welding five recommendations together, with
material that appears in no finding on the card.

- **One short pointer per line.**
- **Every point traces to an operational failure, the SOP gap, or a flag.**
  Flags are a guide for what the failures missed, not licence to add material.
- **Nothing invented** — same rule as `fix`: the correction to a documented
  gap, not an opinion about a better world.
- **Empty when nothing was found**, not padded with generic advice.

**Decided: yes, provenance — but as a CONSTRAINT ON THE MODEL, not as
decoration.** The user's words: *"okay add, only if it is helping ai"*.

So each point carries the failure, gap or flag it derives from, and the value
is that it forces the derivation: a point that cannot name its source is
invented and is dropped before it is written, the same way `fix` is null when
no evidence entry shows a gap. Validate it server-side — a point whose source
does not match an operational_failure, sop_gap or flag on that card does not
render.

On screen it stays quiet: a small marker on the point, not a second column.
It exists so the model cannot invent, and so a reader who doubts a point can
check it — not to be read on every pass.

**BUILT.** The model returns `area_of_improving` as
`[{point, from, source}]`, and `rca_v4_validate._improvements()` CHECKS the
source against the operational failures, SOP gaps and flags on that card — by
kind, so a point claiming the SOP gap and matching only a flag is dropped.
Unsourced and unmatched are counted separately and reported separately: one is
a point that never derived anything, the other claims a derivation it does not
have. On screen the marker is a small grey `op failure` / `sop gap` / `flag`
tag with the source in its title; a point somebody typed says `by hand`, so the
marker keeps meaning "checked against a finding".

## 6. Classification — website messaging is a Product Issue

Confirmed by the user against the review *"after you buy tickets the website
offers a discount on your next purchase; you only get it if you create an
account first; Headout will not honour it"* → **L1 = Product Issue**.

It currently produces Operations Issue / *Content - Instructions not clear /
Misleading Info*, because `App and Website Issues` under Product is scoped to
"didn't load or function". A website that advertises an offer without stating
its precondition is a Product Issue. Prompt rule change in
`server/prompts.py`, around the `[PRODUCT ISSUE …]` block.

**BUILT, in two places, because one was not enough.** The clause under
`[PRODUCT ISSUE]` is only reached if the model gets there — the priority rule
puts Operations first and says stop at the first match — so the handover is
also written into the Operations `Content - Instructions not clear / Misleading
Info` rule, where the model actually stops. Both halves are asserted in
`tests/test_rca_finding_rules.py`.

## 7. Two mutation survivors — test gaps, not defects

From the 68-mutation pass (65 caught · 3 survived · 0 skipped; the third,
pricing/commercial routing, is fixed):

- **`content_match` never reaching the payload.** Mutating
  `"content_match": _content_match(d)` to `None` in `server/api.py` leaves the
  whole suite green — the mismatch row would silently stop rendering.
- **The `body_original` fallback.** Dropping it means a review written in
  another language reads as unchecked rather than being checked.

Both anchors are already in `mutations.json`.

## 8. Runs stopping mid-flight — DIAGNOSED AND FIXED

Both halves had the same shape: nothing anywhere could say "this stopped".

**Why the runs stopped.** Every ingest path handed each review to Starlette as
its own BackgroundTask:

    for rid in ingested:
        background_tasks.add_task(lambda x: asyncio.run(_pipeline(x)), rid)

Starlette runs those as `for task in self.tasks: await task()` with no
try/except (`starlette/background.py`). Two ways that loses reviews, both
silent:

- the FIRST task to raise drops every task behind it. `process_review` opens
  its session OUTSIDE its own try, so a pool timeout or an unreachable
  database raises straight out of the run and takes the rest of the ingest
  with it;
- they run strictly one at a time, so ONE wedged run holds every review behind
  it. The Anthropic SDK client was constructed with its defaults — a 600s read
  timeout and two retries, up to half an hour of blocking inside a single
  call — and the client is synchronous, so awaiting it blocked the loop with
  no await point at which any timeout above could be delivered. That is
  exactly the fifteen-review ingest: one run pinned at step 1 (the first model
  call is `translate`, immediately after `_progress(rid, 1, …)`), thirteen
  never started, one stopped.

Nothing recorded that the thirteen had been queued, so a review that was
queued and never started carried the same evidence as a review nobody had ever
asked for: no draft row, no progress entry, no log line.

**Why `stalled` did not fire.** `processing_state` read `if p:` — the presence
of a progress entry WAS the definition of running. The entry is written at
step 1 and removed in the run's `finally`, so the only run that could ever
reach `stalled` was one that had already finished dying tidily. A wedged run
never reaches its `finally` and reported "Step 1 of 8" for as long as the
server stayed up.

**What now exists.** `server/pipeline.py::run_batch` — one supervised task per
ingest, every review marked queued before the first one starts, each run
isolated and bounded by `RUN_TIMEOUT_S` (12 min), and a counted account at the
end. `server/tiers.py::liveness` — one judgement, shared by the inbox row and
the re-run button's poll, turning on a heartbeat (`updated_at`) rather than on
an entry existing. Three states on the card, not two: queued / running /
stopped. `STALL_AFTER_S` is 10 minutes of no stage progress, `QUEUE_STALL_AFTER_S`
is 30 minutes unstarted, and both are ANNOUNCED in the sentence the reader
sees, because deciding a run is dead from elapsed time is a judgement and
nothing ever reports it.

Still open: `process_review` opens its session before its own `try`. The batch
runner catches it now, but the function is not self-consistent.

---

## 10. Confirming a candidate, indicators on a verified BID, a route to Sent,
## and a refund denial that outranked its cause — BUILT

Four items the user specified, plus a trail-honesty pass raised mid-session.

### 10.1 Confirming a candidate appeared to do nothing — CLIENT half

The server half was already fixed by `073ed6a`: `select-candidate` queues
`run_batch_sync`, which supervises, bounds and accounts for the run. Verified,
not assumed.

The CLIENT half had two faults, either of which reproduces the report exactly:

- **The poll waited for something that had already happened.** It polled for
  `!draft.candidate_state`, and the confirm request clears and commits that
  flag before it returns. The condition was true when the first poll fired
  three seconds later, so the poll stopped while the pipeline was at step 1.
- **The refresh read the store the renderer does not use.** It called
  `loadDraftOverlays()`, which never touches `r.type` — and `r.type` decides
  whether the RCA column draws the analysis or its gate. Only `fetchInbox()`
  recomputes it. So a confirmed review stayed typed `candidates` and kept
  drawing "Locked until a booking is confirmed" for the rest of the session.

Now: `generated_at` is the completion signal (the same one the Re-run button
waits on), `/progress` names the stage and ends the poll on a stalled run, the
budget is the runner's own ten minutes rather than sixty seconds, and
`reloadFromServer()` does BOTH halves. `fetchInbox()` returns whether it
actually reached the server, so a refresh that kept stale rows can no longer
report itself as a refresh.

Tests: `tests/test_confirm_candidate_refreshes.py`, driven in Chromium with
fetch stubbed so the run's timing is controllable.

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
path is checked identically — attachment, manual, regex, auto-promoted tier 2
and associate-confirmed. It writes one of three trail lines (agree / disagree /
could not be compared) and **never unmatches**. `_indicator_match` in
`server/api.py` recomputes it live for the card, so existing drafts get it with
no migration.

Tests: `tests/test_bid_indicator_check.py` (mostly about NOT firing),
`tests/test_indicator_match_reaches_the_card.py` (the wire, plus the pipeline
trail, driven), `tests/test_indicator_and_close_ui.py` (the card draws only on
a mismatch).

### 10.3 Every bucket now has a route to Sent

`POST /api/reviews/{id}/close` — its own action, not a flag on `/send`.
Sending means the RCA and the reply have gone; closing out means there was
nothing to send. It needs no draft (so the processing bucket can reach Sent),
posts nothing to Slack, records `Review.closed_at` / `Review.close_reason`
(new columns, self-healing via `_ensure_columns`, which now covers both
tables), and writes the close onto the confidence trail.

`/send` no longer posts an RCA that does not exist — `has_rca_to_post()` is a
driveable predicate, and the response now says whether it posted and why not,
instead of `{"ok": true, "ts": null}` meaning three different things.

Buttons: the untraceable panel, the candidate picker, and a new
nothing-analysed-yet gate for the processing bucket (which previously fell
through to the full RCA layout and offered a Send button that could only
404). Two clicks to arm, because it moves the card out of every working tab.
"Closed out" is its own inbox chip — a closed review and a replied one are two
different pieces of work and the Sent count must not merge them.

Tests: `tests/test_route_to_sent.py`, `tests/test_indicator_and_close_ui.py`.

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

Tests: six new cases in `tests/test_rca_finding_rules.py`.

### 10.5 The trail said things the card contradicted

Raised mid-session against a real card (BID 33204378, Pena Palace):

- **A PARTIAL BOOKING ROW reached the card.** `_make_candidate()` builds what
  the picker needs; the auto-promote paths hand that same narrow dict through
  as the matched booking. `_get_booking_extra()` is called there — hence
  booking_status and tid_name — but `verify_bid()`, the only query selecting
  `created_at` and `fulfilment_type`, is never called on those paths.
  Fulfilment type, booking date, partnered vendor and lead time were not empty
  in the warehouse; nobody asked for them. `complete_booking_row()` now does
  the same merge `select-candidate` already did, in one place every path
  passes. **This is a behaviour change** — presentation alone cannot fill a
  field that was never fetched.
- `last='None'` — Python's None in a sentence a person reads.
- A green tick on `venue='—' city='—' visit≈'—'`; an empty extraction is a
  finding, not a success.
- "Indicators" meaning the extraction on one line and the search on the next;
  the extraction line is now headed "Extracted from review", which is what the
  card calls that panel.
- "Weak BID — number found in text" read as "the id went nowhere". BigQuery
  RETURNED a booking; we scored it and it disagreed. Reworded to say so.
- The floor line and the "not verified" tier label were already dead at HEAD
  (the floor cannot fire while candidates exist); asserted so they stay dead.

**LEFT ALONE, needs the user's decision:** the PII hash printed as a guest name
(`FjpJxbSfpb65bny/…`). Dropping it reads better; keeping it lets someone
confirm the comparison actually ran.

### Still open from this session

- `fix.owner` is still a third team vocabulary (see §9).
- The indicator check's city/landmark vocabularies are deliberately small.
  A missing entry costs an "unchecked", which is the safe direction; a sloppy
  entry costs a false flag. Extend them from real misses, not from imagination.
- `booking_match_check` and `bid_indicator_check` are two checks on one card.
  They answer different questions (same product family vs same trip) and both
  are needed, but a reader now has two warnings that can fire independently.

---

## 9. Still open after §1-§6

- **`fix.owner` is a third team vocabulary.** `Content | CE | SP | RO |
  Product | Biz | Ops`, unchanged, while flags and Actions Taken moved to the
  nine. Nothing joins on it, so nothing is broken — but a card can now say
  `owner: RO` beside a `CO` flag about the same failure.
- **The `actions_taken` column is written by the projection and edited in
  place.** A re-run recomputes it, which is right, and a hand-added row is
  therefore lost on re-run — the same as every other v4 section, and worth
  knowing before someone reports it as a bug.
- **`pipeline.py` still computes an ungated `actions_taken` at step 11e and
  assigns it at save.** `project_v4` overwrites it four lines later with the
  gated one, so the earlier computation is dead weight — correct output,
  misleading code. Left alone deliberately: another session was editing that
  file at the time.
- **Legacy drafts keep their old-shaped `area_of_improving` strings.** They
  render, marked `by hand`, because provenance is a constraint on the model and
  not a reason to delete work somebody already signed. They are not
  retro-derived.

---

## Working rules that cost time when ignored

- `CLAUDE.md` governs. Mutation-run the diff before every push; run the WHOLE
  suite before committing.
- **Never `pkill -f`.** It matched this session's own shell three times
  (exit 144). Identify by PID via `ps`, kill by number.
- **Never commit while a suite is running.** HEAD moving under a running test
  server made `/api/version` correctly report the process as stale, and a
  build-banner test failed for a reason that had nothing to do with the code.
- The mutation runner runs the full suite per mutation. Shard
  `mutations.json` across four workers (`ms[i::4]`) or a 68-mutation pass takes
  five hours instead of ninety minutes.
