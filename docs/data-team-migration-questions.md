# Data team — migration notes

After reading the full migration guide, there are **no blocking questions** — the
guide (and its example queries) covered everything the ORM RCA tool needs:

- Table paths: `…analytics_reporting.{fct_bookings_lambda, fct_fulfilments_lambda, fct_orders_lambda}`.
- `experience_date → EXTRACT(DATE FROM experience_timestamp)`.
- `experience_id` / `experience_name` → `fct_orders_lambda`, joined `ON bookings.order_id = orders.order_id` (Order↔Booking 1:1 for non-combo).
- `tour_id`, `tour_name`, `vendor_id`, `vendor_name`, `created_at`, `price_payable_usd`, `booking_status`, `primary_guest_name` stay on `fct_bookings_lambda`.
- `booking_status NOT IN ('Dummy','Uncaptured')` now required.
- `fct_fulfilments → fct_fulfilments_lambda` has **no field changes** (grain → `fulfilment_id`). `is_partnered` / `vendor_id` / `fulfilment_type` unchanged — we keep our existing join; only need to dedup for the finer grain.

**Only a scheduling heads-up to send (optional, non-blocking):**

> Hi Harshita — migrating the ORM RCA tool to the `*_lambda` tables now; the guide
> covered everything. One scheduling ask: rough date for the final `fct_X_lambda → fct_X`
> suffix removal? Our table names are env-driven, so it's a config-only switch on our
> side — just want to line it up.
