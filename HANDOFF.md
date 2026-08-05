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

**Open question for the user:** the flag *"No Baby/Infant (<1.00 m, free) pax
type exists in the guest-facing booking flow for TGID 20842"* — is that
Content/Catalog/Media (the page promises a free tier that cannot be selected)
or Product (the booking flow is missing an option)? It currently renders as
CONTENT. Ask before assuming.

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

## 8. Still undiagnosed

- **Runs stopping mid-flight.** A fresh ingest of 15 reviews left 13 in
  Processing and 1 stopped. Nobody has looked at why.
- **A dead run reporting itself as still searching.** The card showed *"Still
  running — nothing searched yet · Step 1 of 8"* over a run that had died; the
  user had to press Re-run. `processing_state === 'stalled'` exists and did
  not fire.

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
