# ORM RCA Workbench — session handoff

Everything a fresh Claude session needs to continue this work.

**Branch: `main`. There is no other line.** If this document ever names a
branch that is not `main`, it is out of date and so is whatever else it tells
you — check `git log --oneline -5` against what you are reading here before
trusting a word of it.

A stale head SHA in this header is not a small problem: a second session read
an earlier copy, took its branch name literally, and spent its first turns
concluding that four finished pieces of work were still open. So this header
does not carry a SHA any more — a SHA in a hand-written file is wrong the
moment the next commit lands, and wrong-with-authority is worse than absent.
`git log` is the source of truth for where the code is; this file is the source
of truth for WHY, and for what is not verified.

---

## 0. START HERE — you do not need to ask the user anything

This section exists so a fresh session can begin working immediately. Read it,
run the four commands, pick the top item. **Nothing below requires the user to
supply context first.**

### 1. Orient (60 seconds, all read-only)

```bash
git remote -v                    # THREE layouts exist — see §2 before pushing
git log --oneline -5             # where the code actually is
python3 tools/check_runs.py      # is anything stuck, and why
python3 tools/check_macros.py    # did the copy file parse; who is on the roster
```

### 2. Know what you CANNOT do here, before you waste a turn on it

In a Claude sandbox **every external service is dead**:

```python
is_live("anthropic")  is_live("zendesk")  is_live("bigquery")
is_live("slack")      is_live("sheet_export")        # ALL False
```

So every AI path runs through its **keyword fallback**. This is not a bug and
you cannot fix it. It means:

- **A prompt change cannot be verified here. Ever.** You can test that the text
  changed and that the deterministic checks around it fire. You cannot test
  whether the model complied. Say so plainly instead of implying it works.
- Anything needing a booking, a ticket or a Slack message is mocked.
- `MOCK_MODE` flips `_ai_down` — **pin it** in tests (§9).

The user runs the live checks on Replit. §7 lists exactly which, with the exact
steps, so you can hand back a short list rather than a conversation.

### 3. What to work on, ranked — pick from the top

| # | Item | Why it is here | Can you finish it alone? |
|---|---|---|---|
| 1 | **FR/EN reply swap** (§7) | The one open **correctness** bug. A guest can receive a reply in the wrong language. | No — prompt only. Code is done; needs one live re-run. |
| 2 | **Google Sheet export** (below) | Silently writing to a sheet nobody chose. | Yes — see the decision below. |
| 3 | **"Reporting" button** (below) | Dead control on a shipped dashboard. | Yes — backend already exists. |
| 4 | **DSS keyword-fallback precision** (§7) | On a model outage most reviews land on `cancelation` at score 1. | Yes — needs a minimum-score rule. |
| 5 | Dashboard work | See §3b for the map. | Yes. |

### 4. Two decisions already made, so you do not have to ask

**Google Sheet export → DISABLE IT unless the user gives you a sheet id.**
`RCA_EXPORT_SHEET_ID` currently falls back to a hardcoded id
(`19Im-BbgWq...`) that nobody in this project chose, `is_live("sheet_export")`
is False, and a failed write is logged at WARNING and never surfaces. Writing to
a stranger's spreadsheet is worse than not exporting. Make the absence of an
explicitly-configured id **refuse to export and say so on the dashboard**,
rather than fall back. If the user later supplies an id, the fallback stays
deleted.

**"Reporting" button → WIRE IT.** `/api/reporting` exists and returns full
metrics; the button has no click handler at all. The user said "leave it for
now" when the queue was longer — it is a small, self-contained job and a dead
control on a shipped dashboard is worse than a missing one. If you are short of
time, **hide the button** rather than leave it inert.

### 4b. CHECK YOUR SANDBOX CAN RUN THE TESTS — "43 skipped" is 648 tests

```bash
python3 -c "import playwright.sync_api" && echo OK || \
  echo "MISSING — 648 tests (15% of the suite, ALL UI coverage) will not run"
```

`pytest.importorskip("playwright.sync_api")` sits at MODULE level in 43 test
files. Without playwright, pytest skips each MODULE, so the summary says
**"43 skipped"** — and the **648 tests inside them are never collected at all.**
That number looks harmless and is not: it is every test that drives
`client/index.html`.

A session read `3605 passed / 43 skipped` as a clean run and went on to
consider a client-side task. Install it:

```bash
python3 -m pip install playwright && playwright install chromium
```

The suite now prints a loud **BROWSER TESTS DID NOT RUN** block when it is
missing. **If you see that block, you cannot verify a UI change** — say so
plainly instead of reporting the suite as passing.

### 5. Before you push

Whole suite in two chunks, then mutation-test the diff (§4). Both are
non-negotiable and both have caught real defects — including, twice, a test
that was passing for the wrong reason, and once a test that could only pass in
the timezone where its bug did not exist.

**Your session may carry a branch instruction from the harness** ("develop on
`claude/<something>`, never push elsewhere without explicit permission"). That
outranks this document — do not push to `main` against it. Instead: push to
your branch, say so, and tell the user the fast-forward into `main` is one
command. Leaving finished work parked on a branch nobody merges is how four
completed fixes came to be re-investigated as open.

---

## 1. What this project is

A dashboard for Headout's CX/ORM team. It ingests 1–3★ Trustpilot reviews from
Slack, matches each to a booking, gathers evidence from Zendesk and BigQuery,
and produces an **RCA** (root-cause analysis) plus a **suggested reply** the
associate reviews, edits and sends.

The flow, end to end:

```
Slack (#team-orm-trustpilot-social)
   → ingest (webhook, plus a 3-min reconcile poller)
   → match a booking (review BID → BigQuery; else candidate shortlist)
   → Zendesk timeline + support contacts
   → BigQuery insights (similar reviews / support ratios)
   → classify L1/L2/sub-theme
   → DSS lookup (the decision sheet: what remedy this case is entitled to)
   → RCA + suggested reply (approved macro, adapted)
   → associate edits → posts to Slack thread / closes out
```

---

## 2. Connecting to it

### The repository — ONE branch, and it is `main`

```bash
git clone https://github.com/dcproject26/Trustpilot-reviews-RCA-automation.git
# main is the line. Branch from it, merge back to it.
```

**Everything is on `main`.** It used to be split: 47 commits sat unmerged on
`claude/vectorshift-pipeline-review-coj74p` while `main` lagged behind, and a
second session opened a branch off `main`, checked the open items "against
current code", and correctly found the Zendesk timeline fix, the durable
Fix-incomplete jobs, the DSS/macro selection and the Slack poller all missing —
because they were on the other branch. All of them were finished. Nobody could
have known from `main`.

That is why this section is short now. Two lines of work that are each other's
blind spot cost more than any merge conflict, and the conflict here was zero:
`main` was a strict ancestor, so it fast-forwarded.

**If you take a branch, merge it back to `main` when you are done.** Do not
leave finished work parked on one.

### ⚠️ Remotes differ BY SANDBOX — check yours before believing any of this

A previous version of this document stated flatly that `origin` returns 403 and
that you must push to a remote called `trustpilot`. That is true in *some*
containers and false in others, and a second session read it, found a single
correct `origin`, and reasonably concluded the whole document was stale.

Look, do not assume:

```bash
git remote -v
```

- **One remote, `origin` → Trustpilot-reviews-RCA-automation.** Normal. Push to
  `origin`. Nothing below applies.
- **Two remotes, `origin` → `dcproject26/Claude`.** That one returns **403**
  ("Claude doesn't have GitHub access to dcproject26/Claude for your
  organization"). Push to the one pointing at
  `Trustpilot-reviews-RCA-automation` instead.

A stop-hook fires after most turns saying *"There are N unpushed commits"*. It
is hardcoded to `origin/$branch` and knows about no other remote, so in the
second case it is a **permanent false alarm**. Verify against the remote that
actually holds the code:

```bash
git rev-list --count <that-remote>/main..HEAD
# 0 = everything is pushed
```

**Do not "fix" `origin` by repointing it.** The Claude container re-asserts
`origin` to `dcproject26/Claude` on its own. I set it to the real repo and
removed the working remote; the reset restored `origin` and did NOT restore what
I had deleted, leaving no push path at all until I added it back. Add a second
remote, leave `origin` alone, and let the hook be wrong.

### The Replit workspace has DIFFERENT remotes again — a third layout

There is **no `origin`** there. `git remote -v` on the repl shows:

| Remote | Points at | Use |
|---|---|---|
| `subrepl-b48r782g` | `dcproject26/Trustpilot-reviews-RCA-automation` | ✅ **this is GitHub** |
| `subrepl-*` (others) | `ssh://…/home/runner/workspace` | the workspace itself |
| `gitsafe-backup` | `git://gitsafe:5418/backup.git` | Replit's own backup |

So `git push origin …` on the repl fails with *"origin: does not appear to be a
git repository"*, and `git status -sb` reports **ahead of the subrepl remote**,
which is the number that matters there. Check before assuming the repl is in
sync with GitHub:

```bash
git log --oneline subrepl-b48r782g/main..HEAD   # what the repl has and GitHub does not
```

Three layouts, one repo. **Always `git remote -v` first.**

### Where it runs

Replit. Two separate things:

- **Workspace** — the dev app, port 5000. Stop it with the Replit **Stop
  button** (■). `pkill` does *not* hold: the supervisor respawns it.
- **Deployment** — the live app the team uses, separate lifecycle, Deployments
  tab. Stopping the workspace does **not** stop it.

Deployment is **autoscale** (stateless, fresh container per instance/deploy).
This matters enormously — see §4.

---

## 3. Repo map

| Path | Lines | What |
|---|---|---|
| `server/pipeline.py` | 4532 | The whole run: `process_review()`. Every step try/except'd. |
| `server/api.py` | 3856 | FastAPI endpoints, inbox list, bulk re-run |
| `server/prompts.py` | 3487 | Every model prompt, incl. `RCA_V4_TEMPLATE` |
| `server/services/zendesk.py` | 3410 | Ticket search, timeline assembly, contacts |
| `server/services/rca_v4_validate.py` | 1816 | Validates/coerces the model's RCA |
| `server/services/slack.py` | 1537 | Ingest, parsing, posting, `sync_channel_to_db` |
| `server/services/insights.py` | 1330 | BigQuery similar-review / support ratios |
| `server/services/claude.py` | 871 | All model calls |
| `server/services/canned.py` | 736 | Approved macro lookup (the reply) |
| `server/services/bigquery.py` | 702 | Booking lookups |
| `server/services/dss.py` | 480 | Decision-sheet lookup (the remedy) |
| `server/services/reply_macro.py` | ~180 | **New.** DSS→macro remedy gate |
| `server/jobs.py` | 415 | Durable run rows (`run_jobs`) + batches |
| `server/tiers.py` | 301 | Which tab a review belongs in |
| `client/index.html` | 9164 | The entire dashboard. No build step. |
| `tests/` | 226 files | ~4096 tests |
| `tools/` | 51 files | Diagnostics — see §8 |
| `content/orm_macros.yaml` | — | Brand voice + hard reply policy (CX edits this) |
| `content/dss_unified.json` | — | Checked-in DSS export, 117 rows |
| `server/data/canned_macros.json` | — | Approved reply macros, 80 on the TP tab |

**Database:** Postgres. Tables: `reviews`, `rca_drafts` (the analysis),
`review_metrics`, `run_jobs`, `slack_events_seen`.

---

## 3b. The dashboard — `client/index.html`

One file, 9k lines, **no build step and no framework**. Edit it and reload.
That is deliberate and it is not going to change; what follows is the map,
because "9164 lines" is not one.

### Three surfaces, not three columns

`state.screen` is `'inbox'` or `'case'`; `state.rcaOpen` slides the RCA over the
case.

    inbox ──click a row──▶ case ──"RCA & response →"──▶ RCA slide-over
      ◀── "← Inbox" ───────┘                    ◀── (case stays underneath)

| Function | Renders |
|---|---|
| `renderInbox()` | the queue table, filtered by `state.filter` + `state.search` |
| `renderCaseHeader()` | guest, stage chip, **"Picked up by"** (`ownerControl`) |
| `renderReviewCol()` | the story column — review, translation, timeline, facts |
| `renderRcaCol()` | the whole RCA slide-over, all six tabs |

The RCA tabs are `_RCA_TABS` inside `renderRcaCol`: **diag, inter, actions, res,
reply, slack**, with a count badge each. `state.rcaTab` selects one.

### How data gets in

| Call | Gives you |
|---|---|
| `fetchInbox()` → `/api/reviews` | the list, into the global `REVIEWS` |
| `loadDraftOverlays()` → `/api/reviews/{id}` | the RCA draft onto `r.rca` |
| `loadSubThemeOptions()` → `/api/taxonomy` | L1/L2, scenarios, takedown reasons, **`REVIEWERS`** |

`REVIEWS` is rebuilt wholesale by `fetchInbox`. **Never hold a reference to a
record across an `await`** — re-find it by id afterwards. A poll landing
mid-request otherwise leaves your write on an orphaned object while the visible
one keeps a value that never saved. `saveOwner` carries the worked example.

### How an edit gets out

Every inline field is an `edSpan(path, text)` or a `data-v3sel` select, where
`path` is a **dotted path into the draft JSON** — e.g.
`what_went_wrong.guest_issues.0.root_cause`. On blur/change it goes to
`saveDraft(id, patch)` → `PATCH /api/reviews/{id}/draft-v2`.

`saveDraft` retries twice (400ms, 1200ms — a Replit restart is back inside two
seconds) and on failure **says so and does not look saved**. That is the rule
below.

### The UI invariant that keeps being broken

> **A bound control and an unbound one must not look alike.**

An edit that appears saved and was not is the worst failure this dashboard has,
because nobody goes back to check. Concretely:
- a failed save **reverts** the on-screen value (`saveOwner`, `saveDraft`);
- a `<select>` must never silently drop a stored value it has no option for —
  it renders it, marked (`ownerControl`, the "not on the roster" option);
- an empty state says WHICH empty it is (never set / cleared / lookup failed).

### Testing a UI change — there IS a harness

Playwright drives the real page against a real server. **Use it**; a source
assertion on this file passes just as happily against a build where the line it
names is unreachable (CLAUDE.md rule 2).

```python
from tests.test_rca_ui_rendered import page, CHROME, _rca_tab   # noqa

def test_thing(page):
    _rca_tab(page, "slack")
    assert page.locator(".spost-row").count() > 0
```

`tests/test_rca_ui_rendered.py` (65 tests) is the harness and the biggest
example; `test_rca_ui_contracts.py`, `test_case_header.py`,
`test_bulk_bar_text.py`, `test_slack_post_header_and_confirm.py` import its
`page` fixture. The fixture is **module-scoped** and reseeds the DB, so one
module's clicks do not reach the next — but a live 4-second UI timer DOES leak
between tests in the same module (see `_reset` in
`test_slack_post_header_and_confirm.py`).

**It runs in UTC.** A timezone bug cannot be caught by a test in the timezone
where the bug does not exist — open your own context
(`ui_browser.new_context(timezone_id="Asia/Kolkata")`) and assert the context is
not UTC. `test_a_bare_timestamp_is_utc_in_a_non_utc_browser` is the pattern, and
it exists because a mutation caught my *test*, not my code.

### The Slack post composer

`_genSlackText()` builds the post; `SECTIONS` is the ordered list of
`[key, label, body]` — **booking, wwr, support, sp, actions, aoi, resolution,
takedown, insights**. The header block (BID line + `Issue:` classification) is
`_genSlackText.header` and renders as its own read-only row.

Several bodies (`wwr`, `booking`, `support`) are **composed server-side** and
rendered verbatim. That is not an accident: two composers for one block is how
`Fix: [object Object]` reached a real Slack post from the client half while the
server's copy of the same section was correct. If you are tempted to build a
section in JavaScript, build it in Python and send it.

Editing a row recomposes the **whole** post from the rows
(`_recomposeSlack`) into a hidden `[data-slack-edit]` mirror, which is what the
Post button sends. Posting takes **two clicks** on every send.


---

## 4. Non-negotiable working rules

`CLAUDE.md` in the repo root is the source of truth. Read it first. The
headlines:

### Rule 1 — "I ran and found nothing" must not look like "I did not run"

The single most repeated bug here. A broken mechanism and an empty result must
never produce identical output. Count what you couldn't do and say so. Put it
where the reader is (the confidence trail on the card, not a log line).

### Rule 2 — a test asserting text exists in source is a spelling check

`assert "foo(" in PIPE` passes against a build where that line is unreachable.
Move logic into something drivable and test the behaviour. Source assertions are
acceptable only for **negative** assertions and for client-side JS (no harness).

### Rule 3 — the deployment DB must outlive a redeploy

Autoscale + a container-local sqlite = data loss on a timer.
`db.assert_durable_on_deploy()` refuses to boot a deployment on sqlite.

### Standing order — mutation-test the diff before every push

```bash
python3 tools/mutate.py mutations_<name>.json -k "<pytest -k filter>"
```

Works on a **copy** of the tree. Baseline must be green. `SKIP` ≠ pass — a
mutant that never applied proves nothing. 26 configs exist; add one per change.

**This is not ceremony. It caught two tests this session that were passing for
the wrong reason** (see §6).

### Running the test suite

The full suite **OOM-kills** (exit 137) in one process — the Playwright browser
tests. Run it in two chunks:

```bash
BROWSER=$(grep -rl "from tests.test_rca_ui_rendered import" tests/*.py | tr '\n' ' ')
IGN=""; for f in $BROWSER tests/test_rca_ui_rendered.py; do IGN="$IGN --ignore=$f"; done

python3 -m pytest -q -p no:cacheprovider $IGN                       # ~3485, ~2min
python3 -m pytest -q -p no:cacheprovider tests/test_rca_ui_rendered.py $BROWSER  # ~611, ~5min
```

---

## 5. What the first session changed (19 commits)

> **§6b below is the delta since this was written and wins where they
> disagree.** This section is kept as the record of its own moment rather than
> edited into a false present tense.


Newest first. Every one is full-suite green + mutation-tested.

| Commit | Change |
|---|---|
| `0a11f21` | Parse warehouse **epoch dates**; draft the reply in **English** (fixes the FR/EN swap) |
| `1732f23` | `tools/check_timeline.py` — why each ticket is in or out, and are re-runs running |
| `351b664` | Zendesk **rate limit** (429) handled instead of returning an empty timeline |
| `8a1ea78` | This handoff document |
| `b22334a` | Zendesk: catch a foreign digest whose booking field is **empty** (subject fallback) |
| `a80eead` | Zendesk: drop tickets whose own booking field names **another booking** |
| `879fd99` | **"Fix incomplete"** → durable job rows (was fire-and-forget + in-process progress) |
| `8e241a8` | A review closed/sent **mid-run** stays that way (stale-merge bug) |
| `794a59b` | Reply macros: **AI picks the scenario**, macro becomes the reply |
| `8e19152` | Reply macros: **gate on the remedy the DSS named** |
| `b40d4a6` | Drove enrichment + recovery through a real run; fixed what that found |
| `997ae63` | Slack ingest: **self-healing window** + 3-min poller (was fixed 72h) |
| `28a7a4b` | Actions taken → Slack one-row-per-section layout |
| `778b8c0` | DSS: enrich booking with `isPartnered`/`amountUSD` on **every** match path |
| `54c9387` | Zendesk prior-trip filter: read the booking date under **every key** |
| `550eccf` | Manual review: recover an empty classification from the warehouse |
| `452ce5d` | Zendesk: drop the guest's earlier-trip tickets, and say so |
| `b0a3b9f` | DSS: **AI reads the review** to pick the scenario; classifier miss-examples |
| `2c55e2a` | RCA cleanup: escalation email withheld, resolution blank, policy-miss guardrail |

### The three themes

**A. DSS and the reply were keyword-matched; they are now AI-matched.**
The decision sheet and the macro list both file *the same scenario several
times, differing only by remedy* ("Missed the tour — Offering HOC" /
"— partial refund" / "— 100% HOC"). The guest's review is identical across
them, and those rows share nearly every word — so keyword scoring was
structurally incapable of the job. Now: an AI selector matches by **meaning**
(mirroring `select_dss_scenario`), with the keyword scorer kept as the
**fallback on a model outage** so a transient failure never reads as "no match".

**B. The macro is the reply, not a tone reference.**
It used to be passed as *"voice only, never content to copy"* — which produced
the model writing its **own** reply in the approved register, exactly the
unapproved-reply risk that rule existed to prevent. Now the macro's approved
sentences are the backbone, adapted to what the guest actually raised.
`content/orm_macros.yaml` `brand_voice` is the tone, as hard policy.
**What makes this safe is structural:** `reply_macro.gate()` withholds any
macro promising a remedy the DSS didn't name, *before* the model sees it.

**C. Several fixes were wired but inert.** A recurring shape: the logic was
right and the value never reached it.
- `bookedOn` vs `date_of_booking` — the prior-trip filter silently never ran on
  the commonest match path.
- `isPartnered`/`amountUSD` — never merged on the direct-BID path, so the DSS
  partnered filter never fired and the value note was always empty.
- The warehouse L1/L2 recovery line was suppressed by the AI-down flag —
  precisely when it fires.

---

## 6. ⚠️ What is verified, and what is NOT

**This sandbox has no live services.** Confirmed:

```
anthropic  = False      zendesk = False      bigquery = False
slack_*    = False      sheet_export = False (no GCP creds)
```

So: **every "green" claim means the logic is correct and tested. None of it
means "works in production."** That distinction bit us twice this session.

### Verified LIVE, against real data ✅✅
Run on 2026-08-25 via `tools/check_timeline.py 33543686` on Replit:

- **Zendesk timeline filtering.** ZD-33535069 — a support-history digest for
  booking 32358051 and a *different guest*, pulled in by the free-text route —
  is dropped by its booking field. The 4 real tickets are kept and build 32
  events. No false drops: all 4 post-date the booking.
- **Epoch date parsing.** `booked_on` arrives as `1.787097364E9`; the
  prior-trip filter now runs instead of reporting that it could not.
- **The durable re-run lifecycle.** A queued job was observed moving
  `queued → running → done` (`run_jobs: {'done': 69}`).

### Verified deterministically (tests only) ✅
The DSS remedy gate (against the real 117-row sheet), macro promise
classification (against the real 80 macros), durable job batching, the
close-mid-run fix, ingest window sizing + pagination, rate-limit backoff, all
client UI behaviour (Playwright).

### NOT verified — needs a live run ❌
- **The FR/EN reply swap fix** (`0a11f21`). Output rule 16 contradicted itself
  ("write it in the guest's language ... the English draft goes in
  `suggested_response`"), so French landed in the field the translation step
  treats as its English source — and that step, told "translate from English
  into French" and handed French, returned ENGLISH. The guest got an English
  reply while "English working copy — not sent" held the French. The rule is
  fixed and the contradictory sentence is pinned as absent, but it is a PROMPT
  change: only a re-run of a non-English review proves the model now complies.
- **AI selection quality** (DSS scenario, macro scenario). Everything ran
  through the *keyword fallback* here.
- **Whether the adapted reply keeps the macro** while addressing specifics.
- **Warehouse L1/L2 recovery** (needs BigQuery).
- **DSS enrichment** on a direct-BID review (needs BigQuery).
- **Classification accuracy** — miss-examples were added; measuring the
  improvement needs re-running the 500 labelled reviews post-deploy.

### Two tests passed for the wrong reason (both caught by mutation testing)
1. A source assertion `"if _ai_down:\n  _cls_entry = None"` kept passing after
   the branch changed — because `"elif _ai_down:"` *contains* `"if _ai_down:"`.
   Replaced with a behavioural test.
2. A phone-number test used `+61 438 474 311` — no run of 7+ digits, so it
   passed against a bare-digit rule by accident. Adding the unspaced form then
   caught a **real bug**: `_BID_LABEL` accepts "order", so `"Order 4471234567"`
   read as a booking id and would have dropped a real contact.

**Do not trust "the tests pass" here without mutation-testing the diff.**

---

## 6b. What landed AFTER the sections above were written

The sections above were accurate when written and were not rewritten as the
work continued — this block is the delta. **Where §5 and this disagree, this
wins.**

| Commit | What |
|---|---|
| `351b664` | Zendesk 429s: back off and honour `Retry-After` instead of returning an empty timeline |
| `1732f23` | `tools/check_timeline.py` — why each ticket is in or out of a timeline |
| `0a11f21` | Warehouse epoch dates parsed; reply drafted in English (output rule 16) |
| `958ea73` | CI fix: the timeline tool died on a database with no tables |
| `d140de4` | Bulk-bar liveness; "Picked up by" roster; plain RCA prose + jargon check |
| `45d25a5` | **Zendesk tickets found and not read were invisible**; RCA relevance rules |
| `59d0112` | Two clicks to post to Slack; the classification is shown before you send it |
| `819bef8` | `tools/check_runs.py` — five causes wear one "re-running 0/1" |
| `96b62ca` | The bulk bar would not go away because the CLIENT would not take it down |

### The ones worth knowing about

**A ticket we FOUND and could not READ was invisible** (`45d25a5`). A failed
comments fetch was `log.warning(); continue` — the ticket stayed in
`ticket_ids` so the card counted it as found, and not one word of it reached
the timeline. "We could not open this conversation" and "this conversation is
empty" produced identical output, and the RCA is written FROM that timeline: a
guest wrote *"I reached out to the help chat and they confirmed the tickets
were available"*, the chat ticket's fetch failed, and the card reported the
contact as not being on record. An absence asserted on a lookup that failed.
There are now three absences with three different words — prior trip and
another-booking are DECISIONS (`pass`), unreadable is a FAILURE (`fail`).

**"Picked up by" is a roster**, in `content/orm_macros.yaml` under `reviewers:`.
A name NOT on the roster still renders (marked) and stays reassignable — a
select that silently drops a value it has no option for would show "unassigned"
over a review that IS assigned and write that lie on the next save.
`tools/check_macros.py` reports the roster on every run and catches a
`reviewer:` typo, which is valid YAML that would otherwise revert the dropdown
to a text box in silence.

**The bulk bar had THREE separate causes** and I fixed the wrong one twice
before `tools/check_runs.py` settled it:
1. `batch_status` picked `running[0]` from an unordered list, so a claim
   abandoned by a dead container was displayed instead of the live run.
2. A row that is `running`, lease-lapsed and OUT of attempts matched neither
   branch of `claim_next` — it could never be claimed, never fail, never
   finish, and pinned `running: true` for ever. `reap_abandoned()` ends it as
   **failed**, not done.
3. The one that was actually biting: the CLIENT. `bulk-status` always answers
   about the newest batch and keeps its `finished_at` for ever, so every page
   load painted "finished 1/1" and left it. The 20-second auto-hide lived
   inside `else if (_bulkTimer)` — it could not fire on a reload, which is
   exactly what someone does when a bar will not go away.

   Underneath all three: the server sends `utcnow().isoformat()` with **no
   zone**, and `new Date(bare)` reads it as LOCAL. At IST+5:30 the age comes
   out negative, every finished batch reads as fresh, and the bar never hides.
   Fine in UTC. **The team works in Asia/Kolkata.** `_bulkFinishedAgeS`
   appends a `Z` only when no zone is present.

**Mutation testing caught a TEST, not the code, on that last one.** The first
UTC test passed with the fix deleted, because the browser under test runs in
UTC where local *is* UTC — a test that can only pass in the one timezone where
the bug does not exist. It now opens a Playwright context in `Asia/Kolkata` and
fails loudly if that context ever comes back as UTC.

---

## 7. Open items

### Needs live verification — HAND THIS LIST OVER, do not discuss it

You cannot do any of these in a sandbox (§0.2). Copy the block below to the
user when you need a live answer, and get on with something else meanwhile. Each
line names the ONE observation that settles it, so the reply is a yes or a no
rather than a conversation.

> **On the Replit app, after `git pull` + Stop/Run:**
>
> 1. Open a **non-English** review (a French one) and press Re-run. In the reply
>    box that gets SENT — is the text **French**? And does "English working copy
>    — not sent" hold **English**? (If they are swapped, that is the open bug.)
> 2. Open a review with **several guest issues**. Under "Analysis — what
>    actually happened?", do **"SOP / process gap"** and **"Closes"** say the
>    same thing in different words? (They should not.)
> 3. On any RCA card, does the analysis contain raw field names like
>    `ticket_mail_seen` or bare codes like `vid 6057` / `TGID`? (They should not
>    — and if they slip through, they should be listed on the confidence trail.)
> 4. On a review with a **rich Zendesk history**: does the confidence trail
>    carry a RED line saying a ticket could not be read? (Only if one genuinely
>    failed — but if no review EVER shows one, tell me and I will check it is
>    reachable.)
> 5. Do the **DSS remedy** and the **macro** picked look right for the review,
>    or generic? (Everything I tested ran through the keyword fallback.)

1. **The FR/EN reply swap** — the one open **correctness** bug. Output rule 16
   contradicted itself, so French landed in the field the translation step
   treats as its English source. Code is fixed and the contradictory sentence is
   pinned as absent; only a live re-run proves the model complies.
2. **DSS + macro AI selection** — do the picks make sense on real reviews?
   Everything tested here ran through the keyword fallback.
3. **The RCA relevance rules** (`45d25a5`) — prompt rules 2h/2i/2j/2k tell the
   model to keep every field on the guest's stated issue, to settle the verdict
   from the Zendesk record, and to say Unknown rather than compensate. The two
   mechanical halves (`jargon_hits`, `fix_scope_hits`) ARE tested; the model's
   compliance is not and cannot be here. Re-run an issue-heavy review and check
   that "SOP / process gap" and "Closes" no longer say the same thing twice.
4. **The unreadable-ticket line** (`45d25a5`) — re-run a review whose Zendesk
   history is rich. If a ticket cannot be read you should now get a RED line on
   the confidence trail naming it. If no such line ever appears on any review,
   check it is reachable rather than assuming every fetch succeeds.
5. **Slack ingest** — the 3-min poller should keep the DB current with no
   clicking. (`slack-poll` rows are appearing in `run_jobs`, so it is running.)

**Do not block on any of these.** They are observations someone else has to
make; the work in §0.3 is yours and needs nobody.

~~"Fix incomplete"~~ has now been exercised live: `run_jobs` carried
`fix-incomplete:` rows and the batch drained to `done: 83, dead: 1`.

~~Zendesk timeline~~ and ~~the durable re-run~~ are **done** — verified live,
see §6.

### Known-broken / not fixed
- **Google Sheet export is not live.** `is_live("sheet_export") = False`, no GCP
  creds. `RCA_EXPORT_SHEET_ID` falls back to a **hardcoded sheet id
  (`19Im-BbgWq...`) that nobody in this project chose**. A failed write is
  caught and logged at WARNING and never surfaces on the dashboard.
  **DECIDED (§0.4): disable the fallback** — refuse to export without an
  explicitly-configured id and say so on the card. Do not ask; do it.
- **"Reporting" button is dead** — no click handler at all. The backend
  `/api/reporting` exists and returns full metrics. **DECIDED (§0.4): wire it**,
  or hide the button. A dead control on a shipped dashboard is worse than a
  missing one.
- **DSS keyword fallback is low precision** — when the model is down, most
  reviews land on `cancelation` at score 1. Open question whether it should
  require a minimum score.
- **DSS AI gets ~117 candidates per review.** Prompt is bounded (action text
  truncated) but it's worth watching for cost/latency.

### Deliberate decisions (don't undo without asking)
- Value (`amountUSD`) is **context, not a hard gate** — the associate decides,
  told via `value_note` in the RCA.
- L1/L2 are **hints, not keys** — a missing L2 caused the manual-review cascade.
- With **no DSS row**, only remedy-free macros may be sent. Never guess between
  refund variants.
- An **empty** Zendesk booking field is **not evidence** — those tickets fall
  through to the date test rather than being dropped.
- Money placeholders (`<$X>`, `<X%>`) are **never invented**.

---

## 8. Diagnostics that work

```bash
# Slack ingest chain — names the broken link
python3 tools/check_slack_ingestion.py --hours 168
python3 tools/check_slack_ingestion.py --replay     # re-ingest what was missed

# Delete reviews (dry-run by default, safe FK order, 18 tests)
python3 tools/purge_reviews.py                      # counts only
python3 tools/purge_reviews.py --before tp_123456   # bounded
python3 tools/purge_reviews.py --apply              # actually delete

# Which tickets reach a booking's timeline, and why — per-ticket verdict,
# plus whether re-runs are actually running. Delegates to the REAL pipeline
# functions; a diagnostic that reimplements what it checks can agree with
# itself while the pipeline does something else.
python3 tools/check_timeline.py 33543686              # by booking id
python3 tools/check_timeline.py tp_1787370328_197709  # by review id
python3 tools/check_timeline.py 33543686 --rerun      # ...and queue a re-run

# WHY a run is stuck. Five causes wear one "re-running 0/1", and two of them
# are opposites: "wait, it comes back" vs "nothing will ever claim it again".
python3 tools/check_runs.py                 # verdict per unfinished row
python3 tools/check_runs.py --reap          # end the ones nothing will claim
python3 tools/check_runs.py --close         # ALSO end the queued ones

# The "Picked up by" roster, and whether the copy file actually parsed
python3 tools/check_macros.py
python3 tools/check_macros.py --file /tmp/draft.yaml   # check a draft first

# Force a full Slack backfill (self-sizing window; no ?hours needed)
curl -sS -X POST "https://$REPLIT_DEV_DOMAIN/api/reviews/refresh-slack" | python3 -m json.tool
```

---

## 9. Things that will waste your time if you don't know them

1. **Check `git remote -v` before believing anything about remotes.** In some
   containers `origin` is `dcproject26/Claude` and returns 403; in others it is
   the Trustpilot repo and works. The stop-hook only ever measures `origin`, so
   in the first case it is a permanent false alarm — see §2.
2. **Full suite OOMs.** Run it in two chunks (§4).
3. **The pipeline detaches the Review row** before the model phase. Never
   `db.merge()` it back — re-read the live row. That was the close-revert bug.
4. **`enqueue()` dedupes** — a review with a queued/running job doesn't get a
   second. Correct, but it means a batch's "queued" count ≠ reviews looked at.
5. **`MOCK_MODE` leaks between tests.** It flips `_ai_down`. Pin it
   (`monkeypatch.setattr(P, "MOCK_MODE", False)`) in tests that read the trail —
   otherwise they pass alone and fail in the full run.
6. **The user pushes back on unverified claims, correctly.** Say "the logic is
   tested" not "it works" unless you've run it live.
7. **Diagnose with the user's shell.** They run commands on Replit and paste
   output; that's the only window into live data. Give copy-pasteable blocks.
   The route tally (`fieldvalue/free_text/requester`) is what corrected a wrong
   Zendesk diagnosis — ask for facts before theorising.
