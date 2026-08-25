# ORM RCA Workbench — session handoff

Everything a fresh Claude session needs to continue this work. Written
2026-08-25. Current head: **`0a11f21`** on branch
**`claude/vectorshift-pipeline-review-coj74p`**.

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

### The repository

```bash
git clone https://github.com/dcproject26/Trustpilot-reviews-RCA-automation.git
git checkout claude/vectorshift-pipeline-review-coj74p
```

### ⚠️ The remote gotcha — read this before pushing

The working copy has **two remotes**, and they are not interchangeable:

| Remote | URL | Works? |
|---|---|---|
| `trustpilot` | `dcproject26/Trustpilot-reviews-RCA-automation` | ✅ **push here** |
| `origin` | `dcproject26/Claude` | ❌ returns **403** |

**Always** `git push -u origin <branch>` → fails. Use:

```bash
git push -u trustpilot claude/vectorshift-pipeline-review-coj74p
```

A stop-hook fires after most turns saying *"There are N unpushed commits"*. It
measures against `origin`, which is inaccessible, so **it is almost always a
false alarm**. Verify with the remote that matters:

```bash
git rev-list --count trustpilot/claude/vectorshift-pipeline-review-coj74p..HEAD
# 0 = everything is pushed
```

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

## 5. What this session changed (19 commits, all pushed)

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

## 7. Open items

### Needs live verification (highest value first)
1. **The FR/EN reply swap** — re-run a non-English review (Frédéric's, French).
   The box that gets SENT must hold the guest's language; the "English working
   copy" must hold English. This is the one open correctness bug.
2. **DSS + macro AI selection** — do the picks make sense on real reviews?
   Everything tested here ran through the keyword fallback.
3. **"Fix incomplete"** — never exercised live. Click it, then
   `GET /api/reviews/bulk-status` should show a live count and `run_jobs`
   should carry rows with `reason` starting `fix-incomplete:`.
4. **Slack ingest** — the 3-min poller should keep the DB current with no
   clicking. (`slack-poll` rows are appearing in `run_jobs`, so it is running.)

~~Zendesk timeline~~ and ~~the durable re-run~~ are **done** — verified live,
see §6.

### Known-broken / not fixed
- **Google Sheet export is not live.** `is_live("sheet_export") = False`, no GCP
  creds. `RCA_EXPORT_SHEET_ID` falls back to a **hardcoded sheet id
  (`19Im-BbgWq...`) that nobody in this conversation chose**. A failed write is
  caught and logged at WARNING and never surfaces on the dashboard. Decide:
  wire it to a real sheet, or disable it.
- **"Reporting" button is dead** — no click handler at all. The backend
  `/api/reporting` exists and returns full metrics. User chose "leave it for
  now".
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

# Which tickets reach a booking's timeline, and why
python3 - <<'PY'
from server.services.zendesk import (_get_client, collect_tickets,
                                     _search_with_retry, booking_id_from_ticket)
BID = "33543686"
z = _get_client()
tickets, tally = collect_tickets(BID, lambda q: _search_with_retry(z, q))
print(f"{len(tickets)} ticket(s) — {tally}\n")
for t in tickets:
    print(f"ZD-{t.id:<12} field={booking_id_from_ticket(t) or '(EMPTY)':<12} "
          f"{getattr(t,'subject','')[:55]!r}")
PY

# Force a full Slack backfill (self-sizing window; no ?hours needed)
curl -sS -X POST "https://$REPLIT_DEV_DOMAIN/api/reviews/refresh-slack" | python3 -m json.tool
```

---

## 9. Things that will waste your time if you don't know them

1. **`origin` is 403.** Push to `trustpilot`. The stop-hook is a false alarm.
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
