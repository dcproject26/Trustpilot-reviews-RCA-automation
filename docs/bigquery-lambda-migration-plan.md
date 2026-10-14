# BigQuery `*_lambda` migration — as shipped

**Status: DONE & LIVE-VALIDATED ✅** — matcher (Tier-1/Tier-2/support), enrichment,
escalation and insights all confirmed on the real `_lambda` tables (`errors=0`).
Legacy tables dropped **15 Oct 2026**. Only action left is **redeploy**.

Commits on `main`: `0a07f2b` (matcher) · `59284ed` (insights) · `9232c45`
(escalation) · `eaa843c` (narrowing/type fix) · `7cde2a7` (this doc).

> **Lesson (why this took several rounds):** the first cut was built from the
> migration guide's *example SQL*, which was **wrong about the deployed schema**.
> It claimed `experience_id`/`experience_name` moved off bookings to
> `fct_orders_lambda`, implied a `booking_id` on `fct_fulfilments_lambda`, and
> said nothing about id column *types*. The live `INFORMATION_SCHEMA` told the
> truth. The mocked test suite stayed green the whole time and never caught any
> of it — only pulling the code onto Replit and running real BIDs did.
> **Verify column location AND type against the live schema, never the guide.**

---

## Guide/ping said  vs  live reality  vs  what the code does

### Tables
| Guide/ping | Live | Code |
|---|---|---|
| `fct_bookings` → `fct_bookings_lambda` | ✅ | `config.BIGQUERY_BOOKINGS_TABLE` |
| `fct_fulfilments` → `fct_fulfilments_lambda` | ✅ (grain `fulfilment_id`, cols `fulfilment_*`-prefixed) | **not queried at all** — unneeded |
| `fct_orders` → `fct_orders_lambda` | ✅ | used **only** by the venue fallback |
| `fct_reviews`, `fct_support_queries`, `dim_vendors` not in migration | ✅ | untouched |
| legacy dropped 15 Oct 2026 | ✅ | why this was urgent |
| Omni topic renames (`Bookings`→`Orders`, etc.) | Omni/Looker only | **N/A** — we query BigQuery directly |

### Columns — where the guide was WRONG
| Guide claim | Live reality | Code |
|---|---|---|
| `experience_date` → `experience_timestamp` | ✅ true | `DATE(b.experience_timestamp)` |
| `experience_id`/`experience_name` **dropped** → orders join | ❌ **both on `fct_bookings_lambda`** | read `b.experience_id`/`b.experience_name`; **no orders join** |
| fulfilment type / `is_partnered` need the fulfilments table | ❌ `is_partnered`, `final_fulfilment_type`, `fulfilment_status`, `vendor_id`/`vendor_name` all on bookings | read off `b.`; **no fulfilments join** |
| `booking_status NOT IN ('Dummy','Uncaptured')` required | ✅ true | on every match/count query; verify_bid guards in Python (rule 1) |
| `completion_type` on `fct_zendesk_tickets` | ✅ true | insights `LEFT JOIN fct_zendesk_tickets ON b.primary_ticket_id = ticket_id` |
| (silent on id types) | `experience_id`/`tour_id`/`vendor_id` are **STRING** | `CAST(... AS STRING)` + string params in narrowing |

---

## As-shipped field mapping (verified against live `INFORMATION_SCHEMA`)

Everything the matcher/enrichment needs is on **`fct_bookings_lambda`**:
`booking_id`, `created_at`, `experience_timestamp`, `experience_id`,
`experience_name`, `tour_id`, `tour_name`, `vendor_id`, `vendor_name`,
`primary_guest_name`, `booking_status`, `price_payable_usd`, `is_partnered`,
`final_fulfilment_type`, `fulfilment_status`, `primary_ticket_id`.

- **visit date:** `DATE(b.experience_timestamp)`  (was `experience_date`)
- **fulfilment type:** `b.final_fulfilment_type`  (was `f.fulfilment_type`)
- **partner flag:** `b.is_partnered`  (was `f.is_partnered`)
- **escalation contact:** `dim_vendors` joined on `b.vendor_id`, selected as 3
  scalar sub-queries (the REST shim can't deserialize a nested `STRUCT`)
- **experience-id matching:** `CAST(b.experience_id AS STRING)` + STRING params
- **insights `completion_type`:** `fct_zendesk_tickets.completion_type` via
  `b.primary_ticket_id = ticket_id` (one ticket per booking, no fan-out)
- **No orders join, no fulfilments join** anywhere in the matcher/enrichment.

## Files changed
- `server/config.py` — defaults flipped to `_lambda`; `BIGQUERY_ORDERS_TABLE` added (venue fallback only).
- `server/services/bigquery_patch.py` — `verify_bid`, both narrowing queries.
- `server/services/bigquery.py` — `find_booking` (both tiers), `find_via_support`, `_get_booking_extra`, escalation scalar read.
- `server/services/insights.py` — all tiles; zendesk join for `completion_type`.
- `server/services/venue_resolver.py` — fallback reads `experience_id`/`name` from `fct_orders_lambda`.
- Tests updated + added: `test_uncaptured_booking_is_not_matched.py`, `test_escalation_email_scalar_read.py`, and the fixtures/substrings in the affected suites.

## Verification done
- Full suite: 4560 passed, 0 failed.
- Mutation: 4/4 caught, 0 survived (Uncaptured guard, escalation read, + 2 Phase-1 guards).
- Live on Replit (`errors=0`): Tier-1, Tier-2 narrowing, support-anchored, enrichment, escalation (emails populate), insights (all tiles incl. TGID), Phase-1 assignment logic.

## Remaining (owner actions — not code)
1. **Redeploy** so live review-processing runs on this code.
2. **Suffix removal** later (`fct_X_lambda → fct_X`): config-only switch (all table names are env-driven) — just need the date from the data team.
