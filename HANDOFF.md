# Handoff — ORM RCA automation (Headout / Trustpilot)

Written 12 Aug 2026 to move this work to a fresh Claude session with nothing
lost. Everything below is verified against the repository, not recalled.

---

## 0.0 BEFORE YOU TRANSFER — the short checklist (updated 13 Aug 2026)

The code is done and on `main`; the deployment is durable. What remains is
operational and Slack-side — code cannot do it. Do these, in order, before
handing the project on.

**The two coordinates the new owner/account needs (and nothing else):**
- **Code:** `github.com/dcproject26/Trustpilot-reviews-RCA-automation`.
  `main` is now the live, deployed line. `archive/main-v4-line-2026-08-01`
  holds the superseded Aug-1 v4/dashboard line if it is ever wanted.
- **Runtime:** the Replit project — the deployment plus the **Production
  Postgres** (`neondb`) it now uses. Replit deploys from the repo above.
- The `github.com/dcproject26/Claude` repo is an **empty scaffold** — nothing
  lives there. Ignore or delete it. (It is also the cause of the
  `stop-hook-git-check.sh` "N unpushed commits" warnings — the hook watches
  that empty repo; `git rev-list --left-right --count trustpilot/<branch>...HEAD`
  reads `0 0`, i.e. everything is pushed. Cosmetic; it does not follow the
  transfer.)

**Open operational items — verify each is green:**
1. **Slack ingestion webhook (IMPORTANT — currently broken).** The diagnostic
   showed **0 webhook deliveries in 72h**: new reviews are not auto-arriving.
   Fix in the Slack app config, not in code:
   - **Event Subscriptions → Request URL** →
     `https://trustpilot-rca.replit.app/webhook/slack` → confirm **Verified**.
   - **OAuth & Permissions** → add `channels:read`, `groups:read`, `mpim:read`,
     `im:read` → reinstall the app.
   Until this is done, reviews only arrive via a manual `refresh-slack`.
2. **Confirm the review recovery is complete.** Production went 12 → 37 after
   re-ingest, so it works. If older reviews are still missing, widen the window:
   `curl -X POST "https://trustpilot-rca.replit.app/api/reviews/refresh-slack?hours=720"`
3. **Redeploy to make the current code live.** Production runs **`38b0276`**;
   `main` is **`5bd3a51`**. Everything in §0.1 below — the SP escalation email,
   the case-findings rebuild, the crashed-translation guard — is therefore
   NOT live yet. The database is already durable via the Postgres env, so this
   is about the code, not the data.

Deeper detail on the database incident is in §0.5; the recovery runbook is
there too.

---

## 0.1 PICKING THIS UP IN A FRESH SESSION (13 Aug 2026, `main` = `5bd3a51`)

Everything a new session needs to keep working on this without re-deriving it.

### Start here, in this order

1. `git pull` — a clone made before `5bd3a51` is missing four fixes. **If a
   local server is running, restart it**; it booted on the old code.
2. Read **`CLAUDE.md`** (the contract) and §0 below. The three rules there each
   describe a bug that shipped here and passed review.
3. Before committing anything:

       python3 -m pytest tests/ -q              # 3653 collected: 3651 pass, 2 skip
       python3 tools/verify_session_fixes.py    # 42 passed, 0 failed, exit 0
       python3 tools/mutate.py <spec>.json      # mutation-run THE DIFF, every push

   `tools/mutate.py` works on a copy of the tree — never mutate the tree you are
   about to commit. A spec is `[{file, name, find, replace}]`; a `find` that
   does not match exactly once reports SKIP, and a SKIP is not a pass.

### What the last session changed, and why it must not be undone

- **`8c6dc27` — the supply-partner escalation email.** It was read only from
  BigQuery `dim_vendors`; the booking record's own `Booking Escalation Email`
  field was never parsed. On match paths where the warehouse enrichment does not
  run, the card asserted the SP *had no* escalation email — false. Resolution is
  now booking record → warehouse → none, carrying `escalationEmailSource`
  (`booking_record` / `vendor_escalations` / `none_found` / `not_fetched`).
  **`not_fetched` must never collapse into `none_found`** — that distinction is
  rule 1 applied to the SP contact, and the UI and the prompt both depend on it.

- **`0b029c8` — case findings had become a second copy of the events timeline.**
  The model wrote the timestamp INSIDE the finding text
  (`22 Jul 15:28 IST — 'Automated Selenium run…'`), which is verbatim a timeline
  row, so the clock reached the card even though §1 renders no `time` field. The
  clock is now split into `time` (orders the section, never rendered — §1 still
  reads chronologically) and a finding that restates a REAL timeline event is
  dropped. **Evidence backing a guest claim is never dropped**, whatever it
  restates: settling what the guest said is a job the timeline cannot do.

- **`18a0d82` — that drop threshold, measured rather than eyeballed.** 0.7 was
  chosen by eye and deleted a real finding: `"Booking confirmed email sent late"`
  scores 0.80 because its words are a subset of a long timeline event, yet
  *"late"* is the entire finding. Verbatim copies score ≥ 0.93, so the bar sits
  at **0.85**, in the measured gap. A test pins the 0.80 case.

- **`5bd3a51` — a crashed inbound translation is not English.** `body_english`
  empty means EITHER the review is English OR the inbound step never ran — the
  same bytes. An Italian review whose translation crashed drew ONE box and would
  have sent an English reply to a guest who did not write in English.
  The guard uses **positive evidence of not-English only**: a script English does
  not use, or another language's function word.
  **Do not "improve" this by asking "does this look English?"** — that flags
  `"Great tour guide"` and `"Terrible experience overall"` (real English reviews
  carrying no function word) and puts a translate panel on every plain English
  review, which is the regression `912e03c` was written to undo. Absence of
  evidence stays one box.
  Markers that collide with English are deliberately absent and pinned by tests:
  `war`/`man`/`die`, `is`/`met`/`we`, `no`/`son`/`con`, `come`/`male`, `plus`,
  and the place names and abbreviations `los`/`las`/`del`, `est`, `mit`, `les` —
  so "Los Angeles", "9am EST", "MIT" and a guest named "Les" all stay one box.
  `verify_session_fixes.py` had been reporting 40/2 against this rule; it is 42/0
  again.

### Running locally

Local dev uses `./local.db` (sqlite). That is correct and safe — the durability
guard only refuses to boot when `REPLIT_DEPLOYMENT` is set, which never happens
off Replit. The connectors are offline on a local machine
(`bq=False, zd=False, ant=False, slack=False`): the dashboard browses fine, but
ingesting reviews, generating RCAs and language detection are inert.

**`ant=False` matters for the reply-language work specifically.** The async
language detector cannot run locally, so the `5bd3a51` guard is the only thing
standing between a non-English review and a one-box English reply on that
machine — which also makes it the easiest place to see the guard working.

### Running the suite on Windows — three defects, and a cross-platform loop

The suite was Linux-only by accident and hid three Windows-specific bugs. All
three are fixed (`706ac42`, `7411419`, `e41c01f`) and the suite now passes on
both platforms on the same commit — but the failure *shapes* recur, so know them:

1. **File locking.** `os.unlink` on a SQLite file SQLAlchemy still holds open
   raises `PermissionError: [WinError 32]` on Windows (POSIX allows it). A
   teardown that raises is reported as an **ERROR, not a failure** — 314 of them
   from healthy code. Delete throwaway databases with `drop_temp_db()`
   (conftest), never `os.unlink`.
2. **Encoding.** `open(path).read()` and `Path(path).read_text()` default to the
   locale encoding — **cp1252 on Windows**, utf-8 on Linux — and raise
   `UnicodeDecodeError` at **read time**, before the assertion runs, on any
   non-ASCII byte (an em dash, a box rule). Read source through `read_source()`
   (conftest). Tools that print non-ASCII (`show_draft.py`, `backfill_received_at.py`)
   `reconfigure(stdout, utf-8)` in `main()`, and their test harnesses decode utf-8.
   **CRLF is NOT a factor** — text mode folds `\r\n` to `\n` on read; that
   hypothesis cost a session real time, do not re-run it.
3. **Module-reload leaks.** `server/main.py` resolves the database THROUGH the
   module (`import server.db as _db`; `_db.SessionLocal()`), not by value at
   import, so the app follows whatever database a test reloaded `server.db` onto.
   Consequences that bite: a fixture must **not** reload `server.main` (an
   earlier version did, and left the app singleton bound to a since-deleted temp
   db — `no such table: reviews` in the lifespan of every later app harness).
   Anything that reloads `server.config`/`server.db`/`server.main` or mutates
   global module state **must restore it** at teardown, or it leaks into the
   next module (this even caught a *test* about the leak leaking).

**The working constraint, stated plainly: this machine runs Windows only, and
the reviewing session runs Linux only.** A cross-platform fix is NOT done until
BOTH have run the suite on the same commit. The loop is push → the other side
verifies → confirm. Two regressions here passed on one platform and failed on
the other; skipping the loop is how they ship. Do not call green on one OS done.

### The fastest whole-system diagnosis

    python3 tools/diagnose.py --url https://trustpilot-rca.replit.app

Its VERDICT block separates "configuration someone must change" from an actual
code fault, and lists the broken links in order.

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

## 0.5 THE DEPLOYMENT DATABASE — READ BEFORE ANY REDEPLOY

**Incident:** a day's ingested reviews vanished on a routine redeploy; only ~12
remained. Not a code bug — none of this session's commits delete data. The
deployment is **autoscale** (`.replit`: `deploymentTarget = "autoscale"`) —
stateless, a fresh container per instance and per deploy — and `DATABASE_URL`
fell back to `sqlite:///./local.db`, a file inside that container. So every
redeploy wiped the runtime database.

**What was done in code (committed):**
- `config._resolve_database_url()` — when `DATABASE_URL` is unset it builds the
  URL from Replit's `PGHOST/PGDATABASE/PGUSER/PGPASSWORD/PGPORT`, so a
  provisioned Postgres is picked up even when `DATABASE_URL` is not propagated
  into the deployment (the exact gap here).
- `db.assert_durable_on_deploy()` — a deployment (`REPLIT_DEPLOYMENT` set) on
  sqlite now **refuses to boot**, with a message naming the fix. Fail loud, not
  warn quiet. Dev repl is untouched (no `REPLIT_DEPLOYMENT`).
  `ALLOW_EPHEMERAL_DB=1` is the escape hatch.

**What the USER still must do — code cannot:**
1. Provision Replit Postgres (Tools → Database) — the `postgresql-16` module is
   already in `.replit`.
2. Make sure the **deployment's** environment carries `DATABASE_URL` or the
   `PG*` vars — not just the dev repl's.
3. Redeploy. (Until Postgres is attached, the deployment will now REFUSE to
   boot — that is intended, better down than silently losing data.)

**Recovery of the lost reviews** — Slack is the source of truth:
`POST /api/reviews/refresh-slack?hours=N` re-ingests anything with no Review
row. **Switch to Postgres FIRST**, or they vanish again on the next deploy.
Manual edits/RCAs on the lost reviews are gone; only the reviews come back.

Tests: `tests/test_deploy_db_is_durable.py`.

**STATUS — 2026-08-13: RESOLVED. Production is on durable Postgres.**
Confirmed from the deployment's own `/api/version`:
`"environment":"deployment"`, `"dialect":"postgresql"`, target
`ep-lively-breeze-azd6oe2u.c-3.ap-southeast-1.aws.neon.tech/neondb`
(Neon, cluster identity `7668292953428363858`), reviews climbing again (12 → 37
after the first refresh). The dev repl is on a **separate** Postgres
(`helium/heliumdb`, Replit's Development Database) — that Production ≠ Development
split is correct and intended; both are durable. A redeploy no longer wipes data.

**Recovery runbook — if reviews look missing after a deploy (do in order):**
1. **Confirm the deployment is on Postgres, not sqlite.** Open
   `https://trustpilot-rca.replit.app/api/version` in a browser and read
   `db.dialect`. Must be `postgresql`. (A command-line `curl` from some hosts
   fails cert verification against the `.replit.app` chain — that is a client
   trust-store issue, not the deployment being down; a browser is the reliable
   check.) If it somehow reads `sqlite`, the deployment env is missing
   `DATABASE_URL` — the guard should have refused boot, so a *running* site is
   almost certainly already Postgres.
2. **Only once step 1 says `postgresql`, re-ingest from Slack** (source of
   truth). Dashboard **↻ Refresh**, or:
   `curl -X POST "https://trustpilot-rca.replit.app/api/reviews/refresh-slack?hours=336"`
   Use a wide window (336 h = 14 days) — after an ingestion outage the gap can be
   more than a day. Doing this while still on sqlite re-loses everything on the
   next deploy, hence the ordering.
3. **If new reviews stopped arriving on their own** (not just a redeploy loss),
   the Slack webhook is the cause, *not* the database. `tools/diagnose.py`
   reports `webhook deliveries in 72h: 0` when Slack cannot reach the server —
   a redeploy can change the public URL or leave Event Subscriptions unverified.
   Fix in the Slack app config: **Event Subscriptions → Request URL** →
   `https://trustpilot-rca.replit.app/webhook/slack` → confirm **Verified**; and
   under **OAuth & Permissions** add `channels:read`, `groups:read`, `mpim:read`,
   `im:read` and reinstall (needed to verify the bot's channel membership).
   Until this is fixed, `refresh-slack` is a manual stopgap, not a cure.

**Fastest single diagnosis:** run `python3 tools/diagnose.py --url
https://trustpilot-rca.replit.app` in the Replit shell — its VERDICT block
separates "configuration someone must change" from an actual code fault, and
lists the broken links in order.

---

## 1. Where the work lives

| | |
|---|---|
| Branch | `claude/vectorshift-pipeline-review-coj74p` |
| Remote | **`trustpilot`** → `https://github.com/dcproject26/Trustpilot-reviews-RCA-automation.git` (see the warning below) |
| Head | `git log --oneline -1` — do not trust a hash written here, it goes stale every commit |
| State | tree clean, 0 ahead of the remote |

**PUSH TO `trustpilot`.** `origin` points at `dcproject26/Claude`, an
unrelated repo that has never held this branch — pushing there fails with 403.
It was repointed at the canonical URL during this session and **the environment
reset it back**, so check `git remote -v` before every push rather than trusting
it. The stale `origin` is also why the stop hook reports ~586 phantom "unpushed
commits": a local tracking ref pinned at `56754f3`, divergent from this history.

    git remote -v                     # CHECK FIRST, every time
    git push -u trustpilot claude/vectorshift-pipeline-review-coj74p

Confirmed three separate times in one session: repointing `origin` does not
stick. If the hook reports phantom unpushed commits, this is why — verify with
`git rev-list --count trustpilot/<branch>..HEAD` before believing it.

### Verify the fixes without credentials

    python3 tools/verify_session_fixes.py

41 driven checks over every fix from this session — no database, no
connectors. It prints the value the OLD code produced beside the new one, so
the difference is visible rather than a dot, and it ends with an explicit list
of what it could NOT check because the connectors are offline. Exits non-zero
on any failure.

### The mutation specs are in the repo

`tools/mutations/` holds all twelve specs from this session with a README
explaining what each covers and what the runs found. They were in a scratchpad
that dies with the session; they are the record of which guarantees were
actually checked, which a passing suite cannot tell you.

### Test suite

**3580 passed, 2 skipped, ZERO failures.** 184 test files. Plus
`python3 tools/verify_session_fixes.py` → 42 passed.

**WITHOUT PLAYWRIGHT YOU RUN 3049, NOT 3582, AND NOTHING SAYS SO.** 35 test
modules — **533 tests, 15% of the suite** — are skipped at import when
playwright is absent, so they never appear in the count at all. Verified on
two machines: 3582 collected here, 3059 on a workspace without it.

Those 533 are the DASHBOARD tests: the controls, inbox search, card
rendering, merged timeline, scenario chips, takedown reason. A green
"3057 passed" is a green run of everything EXCEPT the user interface, which
is the part other people actually touch. To include them:

    .venv/bin/pip install playwright
    .venv/bin/playwright install chromium

Two caveats before you do: it pulls ~150MB, and these are the tests recorded
below as flaky under load.

**ON REPLIT THEY STILL WILL NOT RUN, and that is not a code problem.** With
playwright installed the modules now COLLECT (3585, up from 3059 — the
collection bug is fixed), but every browser test then skips with:

    TargetClosedError: BrowserType.launch: Target page, context or browser
    has been closed

That is chromium starting and dying immediately, which on a Nix container means
missing shared libraries (libnss3, libatk, libgbm...). `playwright install-deps
chromium` fixes it with apt, and Nix has no apt. Confirm with:

    ldd ~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome | grep 'not found'

**THE WAY THROUGH ON NIX: give it a chromium that already works.** Nix cannot
install playwright's dependencies, but it can supply the browser itself, with
its libraries resolved. Add the `chromium` package to the Nix config, then:

    CHROME_BIN=$(which chromium) .venv/bin/python -m pytest tests/ -q

`conftest._chrome_path()` honours `CHROME_BIN` before anything else, and shouts
if it points at nothing rather than falling through to the same skip the reader
was trying to escape. Verified here: with the override set, 65/65 browser tests
pass; with a bad path, the run says which variable is wrong.

Failing that, the 513 need a different runner — CI, or a local machine. They
pass here.
Until they run somewhere on every change, the dashboard is the part of this
project with the least standing coverage, and it is the part other people
touch. That is worth solving properly, not worth another workaround.

The skip message names which of the two failures it is, because "not installed"
and "installed and will not start" need opposite actions — the first version of
it told a user who had just downloaded 184MB of browser to download it again.

```
# On Replit, pip is blocked by the immutable Nix store, so use a venv that
# inherits the app deps and adds only the test runner:
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q

# Anywhere else:
python3 -m pip install -r requirements-dev.txt    # pytest is NOT in requirements.txt
python3 -m pytest tests/ -q

python3 tools/mutate.py <spec>.json --fail-fast
```

**A CORRECTION, because an earlier version of this file said the opposite.**
`test_db_migration.py::test_an_unopenable_database_is_a_sentence_not_a_traceback`
was called an environmental failure for a whole session, and every mutation run
was handed `-k "not test_an_unopenable_database..."` to work around it.

It was not environmental. It was a BROKEN TEST: it passed an unreachable HOST
and asserted `"cannot open the"`, but `create_engine()` does not connect — it
builds lazily and succeeds for any dead host so long as the driver imports, and
psycopg2-binary is in requirements.txt. The run went past that branch to the
identity probe and printed "Refusing to copy on a guess". The assertion could
not pass on any machine that had installed this project.

The reasoning that hid it was **"it fails on unmodified `b5c22ad` too"**. That
establishes it is not a REGRESSION. It says nothing about whose fault it is,
and the two were treated as the same thing about fifteen times. A test that
fails identically before and after a change is a reason to go and read it, not
a reason to name it environmental and move on.

Fixed — it now names a driver this project genuinely does not install, so the
missing-driver path actually runs — and the case it was accidentally exercising
(a live driver against a dead host must refuse rather than guess) is kept as
its own test. **The `-k` exclusion is no longer needed by anything.**

**`pytest` is not a runtime dependency and was not declared anywhere.** The
sandbox this was developed in had it preinstalled, so the suite ran there and
a fresh clone answered `No module named pytest` — a suite that only guards one
machine. `requirements-dev.txt` now carries it.

### KNOWN FLAKY: the browser tests fail under load

`tests/test_rca_ui_rendered.py` (65 tests) drives a real server and Chromium.
Observed in this session: **17 failures on a machine running two other pytest
processes, and 65/65 passing in 15 seconds in isolation.** A clean full run
was 3580 passed / 2 skipped / 0 failed.

So a red run of THAT FILE specifically is not evidence of a regression until
it has been re-run alone:

    python3 -m pytest tests/test_rca_ui_rendered.py -q

**NOT REPRODUCED, and the obvious hypothesis was tested and killed.** The
suspicion was a port race in `_free_port()` — it closes the socket before
uvicorn binds, so two runs can be handed the same number, and since both seed
IDENTICAL fixture data a browser could drive the wrong server without noticing.
Tested properly: the OLD code passed **5 concurrent single-file runs**, then
**2 concurrent FULL suites** (6 pytest processes alive), 3578 passed each.
So the race is real but is not the cause of what was seen, and the trigger
remains unknown. `conftest.py` now checks `srv.poll()` before trusting whatever
answered on the port — kept as hardening, NOT as a fix for this symptom.

**This is unfixed and it matters more than it looks.** Someone runs the suite
on a busy laptop, sees 17 red, and either burns an afternoon or learns to
ignore failures — and a suite that cries wolf is worse than no suite. The
likely cause is CPU/port contention around the `ui_server` fixture
(`conftest.py:179`), which already skips with "server did not start" when it
times out; the failures may simply be that timeout being too tight under load.
Worth a real diagnosis, not a guess.

**When reading a suite run, do not `tail` the output.** Write it to a file and
grep `^FAILED`. This session reported "17 failed" from a truncated tail that
carried no failure reasons and only 14 of the 17 names — a number with no
evidence attached, which is the same defect the codebase is full of.

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
3. **A LOOP THAT REPORTED SUCCESS**, found by the final code review.
   `detect_language` filters blanks, UNKNOWN, over-long answers and anything
   with a space — but a bare two-letter code passes all four. Stored, the
   outcome said `detected` and the column looked filled, while `language_state`
   read it straight back as UNESTABLISHED (because `"en"` is exactly the ingest
   default). The card then re-fired the check on every render, spending a model
   call each time, never settling. A code is now refused with a named reason.
4. The 409 said *"Name the guest's language on the card"* — that input was
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
