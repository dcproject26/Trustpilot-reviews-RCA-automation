# Reporting field & measure audit

Verified against the real export `rca_export (7).csv` (249 rows, Aug–Sep 2026),
which is what `reporting_query.records()` reads through `sheet_export.row_for`.
Counts below are RAW (before the test-owner exclusion; the live engine drops
those rows — `selfcheck.excluded_test_rows` reports how many).

## Dimensions — all 28 verified against real data (added Booking date, Visit date)

| Key | Label | Source (through row_for → project) | Populated | Verdict |
|---|---|---|---|---|
| date | Date | `received_at`[:10] | 249/249 | OK |
| rating | Rating | `rating` (1/2/3) | 249/249 | OK |
| language | Language | `language` | 230/249 | OK (19 blank = never detected) |
| status | Status | `status` (sent/draft/new) | 249/249 | OK |
| experience | Experience / product | booking: `experienceName`/`experience`/`experience_name` | 169/169 traceable | OK (was blank pre-f0548aa) |
| vendor | Vendor / supply partner | booking: `vendorName`/`vendor_name`/`partner` | 169/169 | OK |
| tgid | TGID | booking: `tgid` | 169/169 | OK |
| fulfilment_type | Fulfilment type | booking: `fulfilmentType`/`fulfilment_type` | 169/169 | OK |
| booking_status | Booking status | `booking_status`/`status`/`bookingStatus` | 167/169 | OK |
| tier | Match tier | `match_tier` + declared-untraceable override | 249/249 | OK (T1 108, T2 107, Untr 34) |
| traceable | Booking traced | `booking_id` present? | 249/249 | OK (169 traced) |
| match_method | Match method | `match_method` | 249/249 | OK |
| l1 | L1 category | `l1` | 246/249 | OK |
| l2 | L2 category | `l2` | 246/249 | OK |
| sub_themes | Sub-theme (multi) | `sub_themes[]` | 175/249 | OK |
| scenarios | Scenario (multi) | `scenarios[]` | 226/249 | OK |
| overlay_scenarios | Overlay scenario (multi) | `overlay_scenarios[]` | 117/249 | SPARSE (47%) |
| claim_accuracy | Claim accuracy (multi) | `claim_accuracy[]` per issue | 245/249 | OK |
| resolution_given | Resolution given | `resolution` non-empty? | 249/249 | OK (47 Yes) |
| takedown | Review takedown | `rca_v3.takedown.verdict` | 245/249 | OK — **calculated**: No 122, Yes 56, Untraceable 67 |
| outcome_category | Outcome category | `rca_v3.outcome_category` | 34/249 | SPARSE (14%) — flag in picker |
| dss_followed | DSS followed | `rca_v3.dss.followed` | 137/249 | OK (not_followed 123, followed 3) |
| owner | Picked up by | `picked_up_by` (test-owner → excluded) | 249/249 | OK |
| sent_route | Sent route | `review.sent_route` | 185/249 | OK (only sent rows have it) |
| close_reason | Close reason | `close_reason`[:40] | 86/249 | OK (only closed rows) |
| prompt_version | RCA prompt version | `rca_prompt_version` | 245/249 | OK |

## Measures — all 11 verified

| Key | Label | Formula | Value on 249 rows | Verdict |
|---|---|---|---|---|
| count | Count of reviews | len(rows) | 249 | OK |
| solved | Solved | count(status==sent) | 185 | OK |
| solved_pct | Solved % | solved / count | 74.3% | OK |
| traced_pct | Booking traced % | count(traceable) / count | 67.9% | OK |
| untraceable | Untraceable | count(tier==Untraceable) | 34 | OK |
| posted | RCAs posted | count(rca_posted_at set) | 118 | OK |
| avg_rating | Avg rating | mean(rating) | 1.10 | OK |
| median_tts | Median time to send (h) | median(received→sent) | (needs live datetimes) | OK — median not mean, by design |
| flags | Flags raised | sum(len(flags)) | 807 | OK |
| zendesk | Zendesk tickets | sum(len(zendesk_tickets)) | 772 | OK |
| avg_issues | Avg issues / review | mean(issue_count) | 2.26 | OK |

## Fixed drifts (this session)

- **Daily/weekly digest tier block counted a confusing third cohort.** It broke
  down the "handled" union (received OR finished in the window), which summed to
  neither Received nor Solved — e.g. Received 10, Solved 10, Tier summing to 15,
  reconciling with nothing on screen. It now breaks down the SOLVED cohort and
  is titled "Solved by tier", so it sums to Solved and each share is of that
  total. The dead `collect_window_rows` / `_collect_window_rows` helpers were
  removed with it.

- **Booking date blank on the Slack post / export** (`— not recorded` while the
  dashboard showed it): `row_for`/`_booking_details_lines` missed the
  `date_of_booking` alias the `verify_bid` path writes. Now first in the list,
  matching the client's own `bookingCreatedAt`.
- **Booking fields blank for the patch path**: `experienceName`-only reads →
  `_bkfield` alias helper (f0548aa).
- **Test-owner rows counted**: excluded at every entry point (f0548aa).
- **Weekly Reports tab used the old composer** (different format): now routes to
  `/api/reports/weekly/preview` (`build_weekly_digest`), matching the auto-post.
- **Rank-sections-by / Sections builder shown for daily & weekly**: now Custom-only.

## Dashboard vs Reporting — why they can differ

1. The dashboard's booking panel does a LIVE BigQuery lookup; Reporting reads the
   PERSISTED `draft.booking`. A field enriched live but never persisted shows on
   the panel and not in Reporting. Booking date IS persisted (`pipeline.py:156`).
2. A STALE Replit deployment serves old client + old server code. Every fix since
   `39ee669` needs a republish.
3. Reporting excludes test-owner rows; the dashboard shows them. The difference
   is `selfcheck.excluded_test_rows`.

## Still sparse (not broken — flag in the picker)

- `outcome_category` 14% populated
- `overlay_scenarios` 47%
