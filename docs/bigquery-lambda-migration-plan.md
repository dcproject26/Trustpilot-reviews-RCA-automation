# BigQuery `*_lambda` migration — implementation plan

**Status:** Steps 1–3 **done** — de-hardcoded, defaults flipped to `_lambda`, all field edits applied, full suite green (4555 passed), new guard mutation-CAUGHT. Awaiting a **live single-BID smoke test** (see below) before it can be trusted in production. · **Hard deadline:** legacy tables dropped **15 Oct 2026**

## Live validation required (cannot be done from mocked tests)
The mocked suite validates query structure/routing, not that the real `_lambda`
schema matches the guide. Before/after deploy, run one real BID through the
matcher and confirm:
1. `verify_bid('<a-known-BID>')` returns the booking (experience name/id populated via the orders join, visit date correct).
2. The fulfilments dedup assumes `fct_fulfilments_lambda` keeps a **`created_at`** column (used in `ORDER BY created_at DESC`). If it was renamed, `verify_bid` will fail — the one guide-unconfirmed column. Confirm it exists, or adjust the `ORDER BY`.
3. An Uncaptured/Dummy BID returns None with the "not a real booking" log line.
**Source:** [Realtime Tables Migration Guide](https://docs.google.com/document/d/1PqelQkqPteR4v3SGPoLX3v943YyszQ8iaml6GplyT3o/edit)

When `fct_bookings` and `fct_fulfilments` are dropped, **every booking match in the
tool fails** (Tier-1 BID lookup, Tier-2 name cascade, booking enrichment). This is
not a table rename — three columns we rely on are dropped or renamed, and the new
bookings table silently includes `Uncaptured`/`Dummy` rows we must exclude.

---

## 1. Scope — what changes and what does not

| Our table (`config.py`) | New table | Fate |
|---|---|---|
| `…analytics_reporting.fct_bookings` | `fct_bookings_lambda` (grain `booking_id`, unchanged) | **Must migrate** |
| `…analytics_reporting.fct_fulfilments` | `fct_fulfilments_lambda` (grain → `fulfilment_id`) | **Must migrate** |
| `…analytics_reporting.fct_reviews` | — not in migration — | No change |
| `…analytics_reporting.fct_support_queries` | — not in migration — | No change |
| `dim_vendors` | — not in migration — | No change |

### Confirmed field mapping (from the guide's own example queries)
Every column our match/enrichment SQL reads, and where it comes from after migration:

| Our column today | After migration | Source table |
|---|---|---|
| `b.booking_id` | `bookings.booking_id` | `fct_bookings_lambda` |
| `b.created_at` | `bookings.created_at` | `fct_bookings_lambda` |
| `b.experience_date` | `EXTRACT(DATE FROM bookings.experience_timestamp)` | `fct_bookings_lambda` |
| `b.experience_id` | `orders.experience_id` | **`fct_orders_lambda`** (join `ON bookings.order_id = orders.order_id`) |
| `b.experience_name` | `orders.experience_name` | **`fct_orders_lambda`** |
| `b.tour_id` | `bookings.tour_id` | `fct_bookings_lambda` |
| `b.tour_name` | `bookings.tour_name` | `fct_bookings_lambda` |
| `b.vendor_id` | `bookings.vendor_id` | `fct_bookings_lambda` |
| `v.vendor_name` (via `dim_vendors`) | `bookings.vendor_name` (direct) | `fct_bookings_lambda` — dim_vendors join no longer needed for the *name* |
| `b.primary_guest_name` | `bookings.primary_guest_name` | `fct_bookings_lambda` (not renamed; `primary_guest_number`→`primary_guest_phone_number` is a different field) |
| `b.booking_status` | `bookings.booking_status` **+ `NOT IN ('Dummy','Uncaptured')`** | `fct_bookings_lambda` |
| `b.price_payable_usd` | `bookings.price_payable_usd` | `fct_bookings_lambda` |
| `f.fulfilment_type` | `f.fulfilment_type` (unchanged) | `fct_fulfilments_lambda` — keep the join |
| `f.is_partnered` | `f.is_partnered` (unchanged) | `fct_fulfilments_lambda` — keep the join |
| `f.vendor_id` (fulfilling vendor, for escalation `dim_vendors` join) | `f.vendor_id` (unchanged) | `fct_fulfilments_lambda` — keep the join |

**Fulfilments: keep the join, don't move to the rollup.** The `fct_fulfilments → fct_fulfilments_lambda` section has NO field-level renamed/dropped table — only the grain change. `is_partnered` / `vendor_id` / `fulfilment_type` are unchanged, so the escalation path (`_get_booking_extra`, which deliberately uses the *fulfilling* vendor `f.vendor_id` — bigquery.py:602-615) stays exactly as it is, just on the renamed table. The booking-rollup columns (`requested_fulfilment_type` etc.) exist but we don't need them, so we avoid the "which vendor is `bookings_lambda.vendor_id`" ambiguity entirely.

**Grain fan-out (the one code concern):** fulfilments grain is now `fulfilment_id`, so `LEFT JOIN … ON b.booking_id = f.booking_id` can return multiple rows per booking. Dedup to one fulfilment per booking (e.g. latest attempt via a `QUALIFY ROW_NUMBER() OVER (PARTITION BY f.booking_id ORDER BY f.created_at DESC) = 1`, or a correlated subquery), mirroring the existing `LIMIT 1` discipline, so one booking still yields one row with its canonical fulfilment facts.

Grain (bookings↔orders): Order↔Booking is **1:1 for non-combo**; combo orders have multiple bookings per order. We match booking→order (many-to-one), so one booking always has one order row — the orders join is safe for our direction.

### Column changes on `fct_bookings_lambda` that hit us
- **`experience_date` → RENAMED `experience_timestamp`** (combined date+time). Use `DATE(b.experience_timestamp)` wherever we had `experience_date` / `DATE(b.experience_date)`.
- **`experience_id` → DROPPED** (now on `fct_orders_lambda`, join on `order_id`). Core to Tier-2 narrowing (`experience_id IN UNNEST(@tgid_list)`).
- **`experience_name` → DROPPED** (now on `fct_orders_lambda`).
- **`booking_status`** kept, but the table now contains `Uncaptured`/`Dummy` rows → must filter.
- Kept (not in any dropped/renamed list, **verify against live schema**): `booking_id`, `created_at`, `tour_id`, `vendor_id`, `primary_guest_name`, `tour_name`, `price_payable_usd`.

### `fct_fulfilments_lambda`
- Grain is now per **`fulfilment_id`** → `LEFT JOIN … ON booking_id` can fan out to multiple rows per booking.
- `fct_bookings_lambda` now carries a **booking-level fulfilment rollup** (last/first attempt status, type, vendor) → prefer reading `fulfilment_type` (and possibly `is_partnered`/vendor) from the rollup and **drop the fulfilments join** where we only need booking-level fulfilment facts.

---

## 2. Open questions — confirm with the data team BEFORE coding (Task 0)
1. Full paths of the new tables — assumed `headout-analytics.analytics_reporting.{fct_bookings_lambda, fct_fulfilments_lambda, fct_orders_lambda}`. Confirm dataset.
2. Exact **rollup column names** on `fct_bookings_lambda` for fulfilment type / vendor / `is_partnered` (decides whether we can drop the fulfilments join).
3. Is `order_id` on `fct_bookings_lambda` 1:1 with `booking_id`? (The `fct_orders_lambda` join for `experience_id`/`experience_name` assumes it.)
4. Does `fct_fulfilments_lambda` keep `booking_id`, `vendor_id`, `fulfilment_type`, `is_partnered`, and does `dim_vendors` still join on the same `vendor_id`?
5. Confirm `price_payable_usd`, `created_at`, `tour_id`, `tour_name` exist unchanged on the lambda table.
6. Second cutover: the guide's final step renames `fct_X_lambda → fct_X`. Get the date so we plan the follow-up switch (see Task 6).

> If answers are not available before 15 Oct, mitigate by confirming whether the legacy tables are truly dropped on the 15th or merely deprecated, and request a short grace window.

---

## 3. Code to change (exact locations)

### `server/config.py` (lines ~52–59) — **Step 1/2a done**
- `BIGQUERY_ORDERS_TABLE` added (default `…fct_orders_lambda`), unused until Step 3.
- `BIGQUERY_BOOKINGS_TABLE` / `BIGQUERY_FULFILMENTS_TABLE` defaults **still legacy on purpose** — flip to `_lambda` together with the Step-3 field edits (or set the env in the deployment).
- `BIGQUERY_REVIEWS_TABLE`, `BIGQUERY_SUPPORT_TABLE` unchanged (not in migration).

### `server/services/bigquery_patch.py` — **de-hardcode done (Step 1)**
- `_VERIFY_BID_SQL`, `_NARROWING_SQL_WITH_TGIDS`, `_NARROWING_SQL_NO_TGIDS` now f-strings using the config constants (verified byte-identical to the old literals). `dim_vendors` left as a literal (not migrating).
- Step-3 field edits still to apply (see per-query edits below).

### `server/services/insights.py` — Experience Insights (found during verification)
- Module constants `_BOOKINGS_TABLE` / `_FULFILMENTS_TABLE` (~59–61) — **de-hardcoded (Step 1 done)**, now `= BIGQUERY_BOOKINGS_TABLE` / `BIGQUERY_FULFILMENTS_TABLE`.
- Still needs Step-3 field edits: uses `experience_date` ×5 (~741, 981, 989, 999) and `experience_id` ×3 (~854, 940, 971). Same `experience_timestamp` rename + `fct_orders_lambda` join for `experience_id`, and the `NOT IN ('Dummy','Uncaptured')` filter on its booking scans.

### `server/services/venue_resolver.py` — venue→TGID fallback (found during verification)
- `_FALLBACK_SQL` (~15) — **de-hardcoded (Step 1 done)** to `BIGQUERY_BOOKINGS_TABLE`.
- Step-3: it `SELECT`s `experience_id, experience_name` — **both dropped from `fct_bookings_lambda`**. When the default flips, repoint this fallback to `fct_orders_lambda` (or rely solely on `dim_experiences`, the primary path).

### `server/services/bigquery.py`
- Tier-1 query (~120–138) and Tier-2 query (~149–168): `experience_date`, `experience_id`, `experience_name`, fulfilments join.
- `_get_booking_extra` (~622–647): `booking_status`, `tour_name`, `f.is_partnered`, `dim_vendors` via `f.vendor_id`.
- `_get_booking_amount` (~689–695): `price_payable_usd`.
- Support/reviews joins (~453–454, ~550) reference `BIGQUERY_BOOKINGS_TABLE` — verify the booking side still resolves after the swap.

### Per-query edits (apply to every booking SELECT above)
1. `experience_date` → `experience_timestamp`; any `DATE(b.experience_date)` → `DATE(b.experience_timestamp)`; `experience_date BETWEEN …` → `DATE(b.experience_timestamp) BETWEEN …`; `ORDER BY b.experience_date` → `… b.experience_timestamp`.
2. Add `JOIN fct_orders_lambda o ON b.order_id = o.order_id` and source `experience_id` / `experience_name` from `o.` (or confirm they are exposed elsewhere). Keep aliases `tgid` / `experienceName` identical so downstream code and `_row_to_dict` are unchanged.
3. Add **`AND b.booking_status NOT IN ('Dummy', 'Uncaptured')`** to every booking match/enrichment query (Tier-1, Tier-2 narrowing, `_get_booking_extra`, `_get_booking_amount`). Per CLAUDE.md rule 1, log when this filter drops the only candidate (an excluded-because-uncaptured match must not read the same as "no booking found").
4. Fulfilments join: where only `fulfilment_type` is needed, read it from the `fct_bookings_lambda` rollup and drop the `fct_fulfilments_lambda` join. Where the escalation path needs the **fulfilling vendor** (`_get_booking_extra` joins `dim_vendors` on `f.vendor_id`), keep a fulfilments join but **de-fan-out** (pick one fulfilment deterministically — e.g. latest attempt — mirroring the existing `ORDER BY … LIMIT 1` discipline) so one booking still yields one row.

---

## 4. Tests to update (6 files reference the old names)
`test_booking_created_keeps_its_time.py`, `test_insights_window_is_applied.py`,
`test_recurrence_scope_and_bids.py`, `test_support_anchored_match.py`,
`test_venue_corrected_spelling.py`, `test_venue_says_why_it_failed.py`,
`test_zendesk_guest_name_fallback.py`, and the schema/census refs in
`test_controls_actually_work.py`, `test_version_reports_connectors.py`.
- Update mock BQ fixtures to the new column names (`experience_timestamp`, order-join for `experience_id`/`experience_name`).
- Add a test proving an `Uncaptured`/`Dummy` row is **not** matched (new correctness guard), and that it is logged as excluded, not as "no booking".
- Mutation-run the diff (`tools/mutate.py`) — the new `booking_status` filter and the timestamp rename are exactly the kind of change a spelling-check test would miss.

---

## 5. Validation before deploy
- `MOCK_MODE` run of the full pipeline against seeded reviews — Tier-1, Tier-2 single match, Tier-2 candidate cascade, untraceable.
- If possible, one live BID through `verify_bid` against `_lambda` to confirm column names resolve (catches any rename we assumed wrong).
- Full suite + the standing mutation run; cherry-pick to `tmp-main` and re-run (CLAUDE.md "Before committing").

---

## 6. Follow-up — the second cutover
The guide's final step removes the `_lambda` suffix (`fct_bookings_lambda → fct_bookings`).
Because all four tables are env-driven after this work, that switch is **config-only**
(update the `BIGQUERY_*` env values / defaults) — no SQL change. Track the date from
open question #6 and schedule it.

---

## 7. Sequencing
0. Confirm the open questions (§2) — blocks correctness of §3.
1. De-hardcode `bigquery_patch.py` to use config constants (no behaviour change) + run suite.
2. Add `BIGQUERY_ORDERS_TABLE`; repoint bookings/fulfilments defaults to `_lambda`.
3. Apply per-query edits (§3.1–3.4) in `bigquery.py` + `bigquery_patch.py`.
4. Update fixtures/tests (§4); add the Uncaptured guard test.
5. Validate (§5); mutation-run; cherry-pick to `tmp-main`.
6. Deploy before 15 Oct; set the deployment env vars (CLAUDE.md rule 3 — the deployment must carry the new table names).
7. Schedule the suffix-removal follow-up (§6).
