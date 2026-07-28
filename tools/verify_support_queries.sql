-- Verification for the query_category change in server/services/insights.py
--
-- Three claims were taken from the fct_support_queries LookML and have never
-- been checked against the data. Each query below settles one of them. Run
-- them in order; the answers change what the code should do.
--
-- Run against: headout-analytics.analytics_reporting


-- ===========================================================================
-- 1. Does the `tags` column exist, and is it a scalar STRING?
--
-- The whole change rests on this. The LookML compares tags to comma-joined
-- strings ("CHATBOT, CHATBOT-TRANSFER"), which only makes sense if tags is one
-- STRING per row rather than an ARRAY. If it comes back ARRAY<STRING>, the
-- CASE in insights.py will not even compile and needs rewriting as a
-- membership test.
-- ===========================================================================
SELECT column_name, data_type
FROM `headout-analytics.analytics_reporting.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'fct_support_queries'
  AND column_name IN ('tags', 'query_tag', 'query_category', 'booking_id');


-- ===========================================================================
-- 2. Is "Chat Abandoned" really absent from query_tag?
--
-- This is the claim that says the old exclusion list matched nothing. If row
-- 1 of this result comes back with a non-zero count, then "Chat Abandoned" IS
-- a raw query_tag value, the old code was excluding some of them, and only
-- the chatbot-derived ones were being missed.
--
-- Row 2 is the population the old code was silently keeping.
-- ===========================================================================
SELECT
  COUNTIF(query_tag = 'Chat Abandoned')                       AS raw_chat_abandoned,
  COUNTIF(tags IN ('CHATBOT, CHATBOT-TRANSFER',
                   'CHATBOT-TRANSFER, CHATBOT'))              AS derived_chat_abandoned,
  COUNTIF(query_tag IS NULL)                                  AS null_query_tag,
  COUNT(*)                                                    AS total_rows
FROM `headout-analytics.analytics_reporting.fct_support_queries`
WHERE DATE(created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY);


-- ===========================================================================
-- 3. What does the NAR regex actually catch?
--
-- The new exclusion is a nine-term regex lifted from fcr_dashboard, applied
-- case-insensitively. Two things to look for:
--
--   - Anything in `excluded_by_new` that is obviously a real guest contact.
--     "NAR" is matched as a substring, so a category like "Narrative Issue"
--     would be wrongly dropped. If that shows up, the pattern needs word
--     boundaries: \bNAR\b instead of NAR.
--   - The size of `kept_by_old_excluded_by_new`. That is the correction -
--     rows that used to inflate the support denominator and no longer do.
-- ===========================================================================
WITH c AS (
  SELECT
    CASE WHEN tags IN ('CHATBOT, CHATBOT-TRANSFER', 'CHATBOT-TRANSFER, CHATBOT')
         THEN 'Chat Abandoned' ELSE query_tag END AS query_category
  FROM `headout-analytics.analytics_reporting.fct_support_queries`
  WHERE DATE(created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
)
SELECT
  query_category,
  COUNT(*) AS n,
  REGEXP_CONTAINS(IFNULL(query_category, ''),
    r'(?i)Auto resolved|Blank Call/no Response|Chat Abandoned|Missed Chat|'
    r'Out Call|Vendor Query|Vendor Ticket Email|Outbound Call|NAR'
  ) AS excluded_by_new,
  IFNULL(query_category, '') IN ('Chat Abandoned', 'Nar', 'Out Call', 'Vendor Query')
    AS excluded_by_old
FROM c
GROUP BY query_category
ORDER BY n DESC
LIMIT 100;


-- ===========================================================================
-- 4. How much does the support ratio actually move?
--
-- The end-to-end effect on one real booking. Substitute the tid and vid of a
-- booking you are looking at on the dashboard; the anchor is that booking's
-- experience date, which is what insights.py uses.
--
-- If old_denominator and new_denominator are identical, the change is a no-op
-- for this experience and something above is wrong.
-- ===========================================================================
DECLARE p_tid    STRING DEFAULT '<TID>';
DECLARE p_vid    STRING DEFAULT '<VID>';
DECLARE p_anchor DATE   DEFAULT DATE '<VISIT_DATE>';

WITH c AS (
  SELECT
    sq.booking_id,
    CASE WHEN sq.tags IN ('CHATBOT, CHATBOT-TRANSFER', 'CHATBOT-TRANSFER, CHATBOT')
         THEN 'Chat Abandoned' ELSE sq.query_tag END AS query_category
  FROM `headout-analytics.analytics_reporting.fct_support_queries` sq
  LEFT JOIN `headout-analytics.analytics_reporting.fct_bookings` b
    ON CAST(b.booking_id AS STRING) = sq.booking_id
  WHERE b.tour_id = p_tid AND b.vendor_id = p_vid
    AND DATE(b.experience_date) <  p_anchor
    AND DATE(b.experience_date) >  DATE_SUB(p_anchor, INTERVAL 30 DAY)
)
SELECT
  COUNT(DISTINCT IF(
    IFNULL(query_category, '') NOT IN ('Chat Abandoned', 'Nar', 'Out Call', 'Vendor Query'),
    booking_id, NULL))                                  AS old_denominator,
  COUNT(DISTINCT IF(
    NOT REGEXP_CONTAINS(IFNULL(query_category, ''),
      r'(?i)Auto resolved|Blank Call/no Response|Chat Abandoned|Missed Chat|'
      r'Out Call|Vendor Query|Vendor Ticket Email|Outbound Call|NAR'),
    booking_id, NULL))                                  AS new_denominator,
  COUNT(DISTINCT booking_id)                            AS unfiltered
FROM c;
