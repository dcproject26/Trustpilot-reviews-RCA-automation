# Outstanding work — specified, not built

Written at the end of a session that ran out of context. Every item below was
decided by the user in conversation. Nothing here needs re-litigating; build it
as written. Where a judgement is still open it says so explicitly.

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

## 3. Issue-specific answers — remove the section

- Delete the section from the dashboard.
- The questions stay alive server-side and feed the RCA prompt as **checks to
  write against**: a verdict, root cause and SOP gap must be consistent with
  them.
- **When a check surfaces something missed, assess whether it is an
  operational failure or an SOP gap and write it THERE.** The user was
  explicit about this. It does not become a trail line or a count.

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

## 6. Classification — website messaging is a Product Issue

Confirmed by the user against the review *"after you buy tickets the website
offers a discount on your next purchase; you only get it if you create an
account first; Headout will not honour it"* → **L1 = Product Issue**.

It currently produces Operations Issue / *Content - Instructions not clear /
Misleading Info*, because `App and Website Issues` under Product is scoped to
"didn't load or function". A website that advertises an offer without stating
its precondition is a Product Issue. Prompt rule change in
`server/prompts.py`, around the `[PRODUCT ISSUE …]` block.

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
