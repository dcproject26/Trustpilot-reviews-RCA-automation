# VectorShift port — booking matcher

`matcher.py` is the booking-match logic as a standalone module. No Replit,
FastAPI, SQLAlchemy, zenpy or BigQuery imports — pure functions over plain
dicts, so it drops into a VectorShift Python node unchanged.

`test_matcher.py` runs offline and proves the port behaves like the version
verified against live Zendesk on 2026-07-28.

```
python3 vectorshift/test_matcher.py
```

## Pipeline shape

| Step | Node | Call |
|---|---|---|
| 1 | LLM | `render_prompt(review_text, reviewer_name, review_date)` |
| 2 | Python | `clean_indicators(llm_json)` |
| 3 | Python | `build_queries(indicators, reviewer_name)` → list of query strings |
| 4 | Zendesk | run each query, collect tickets |
| 5 | Python | `shortlist(tickets, indicators, reviewer_name)` → candidates |
| 6 | — | associate confirms one; **only then** look it up in BigQuery |

BigQuery is deliberately absent from matching. The booking id and every fact
needed to judge a match are on the Zendesk ticket; BQ is for enriching a
booking that has already been confirmed.

## The rule

1. BID already in the review → identified, no search.
2. No indicators at all → untraceable.
3. Search with whatever indicators exist.
4. Keep tickets satisfying **all** of them — absent indicators are skipped,
   never blocking.
5. Name-only review → the 5 most recent.
6. Nothing survives → untraceable.

**name** — both names required. A surname alone is not a match, so
"Joe Christopher" does not pull in "Christopher McCardle" where Christopher is
the first name. Initials match full names (`C.` → `Catherine`) and a nickname
table covers `Joe` → `Joseph`, which prefix matching cannot (`j-o-s` vs `j-o-e`).

**venue** — significant-word overlap, but the overlap must contain a
*distinctive* word. Matching only on a venue-type noun ("palace") returns half
the catalogue: Pena Palace, Buckingham Palace, Doge's Palace.

**city** — only filters when there is no venue. The extractor returns whatever
the review gives it, sometimes a country ("Poland"), which never token-matches
the ticket's city ("Warsaw") even though they agree.

**pax** — narrows, never rejects. It was decisive for a review naming
"9 combo tickets" (9 == 9 cut thirteen matches to one), but a review saying
"two tickets" against a ticket recording pax 1 is a counting difference, not a
different booking.

**Query breadth** — a bare `type:ticket <name>` matches every ticket mentioning
that name anywhere and trips Zendesk's "more results than allowed" cap; a bare
venue query returns everyone who booked it. Both are used only when they are the
sole indicator available.

## Verified outcomes (live Zendesk, 2026-07-28)

| Review | Booking | Matched on |
|---|---|---|
| Fredrik Olsen | `32885787` Wieliczka Salt Mine | name, venue, city |
| David | `32908218` Palace of Culture and Science | name, venue |
| Ciprian | `32900044` Oceanogràfic, pax 9 | name, venue, city, pax |
| C. Nauleau | `32244357` Louvre | name, venue, city |
| Joe Christopher | 4 Joseph/Joe bookings, newest first | name |

## Field IDs

`FIELDS` in `matcher.py`, confirmed against ZD-33979875. Note `360021524491` is
the **itinerary/payment id**, also 8 digits — never a booking id.

Re-confirm on your instance with `tools/zd_field_discovery.py <ticket_id>`.

## Two things to check in the VectorShift UI

Neither is answerable from the code:

1. **How a pipeline is exposed as an endpoint** Replit can call.
2. **Whether the VS Zendesk integration returns `custom_fields`** as a list of
   `{"id": int, "value": any}`. `ticket_signals()` expects that shape; if VS
   returns something else, that function is the only thing needing adjustment.
