# Data team — migration follow-up

The `*_lambda` migration is done and live-validated (see
`bigquery-lambda-migration-plan.md`). Everything was resolved by reading the live
`INFORMATION_SCHEMA` directly, so there are **no blocking questions**.

**One scheduling note to send when convenient** (non-blocking):

> Hi Harshita — we've migrated the ORM RCA tool to the `*_lambda` tables and
> validated it live. One scheduling ask: rough date for the final
> `fct_X_lambda → fct_X` suffix removal? Our table names are env-driven, so it's
> a config-only switch on our side — just want to line it up.
