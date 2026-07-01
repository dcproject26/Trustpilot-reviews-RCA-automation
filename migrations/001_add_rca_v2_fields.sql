-- Migration 001: Add fields for the demo-parity RCA output
-- Run this against Replit Postgres before deploying new code.
-- Safe to re-run (uses IF NOT EXISTS).

ALTER TABLE rca_drafts
  ADD COLUMN IF NOT EXISTS stated_issue TEXT,
  ADD COLUMN IF NOT EXISTS l1 VARCHAR,
  ADD COLUMN IF NOT EXISTS l2 VARCHAR,
  ADD COLUMN IF NOT EXISTS l1_reasoning TEXT,
  ADD COLUMN IF NOT EXISTS diagnostic_checks JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS what_went_wrong_bullets JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS support_interaction_frames JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS support_summary TEXT,
  ADD COLUMN IF NOT EXISTS sp_interaction_frames JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS area_of_improving JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS actions_taken JSONB DEFAULT '{"sp":[],"customer":[],"business":[],"product":[],"ce":[]}'::jsonb,
  ADD COLUMN IF NOT EXISTS resolution TEXT,
  ADD COLUMN IF NOT EXISTS similar_support JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS similar_reviews JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS candidates_list JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS candidate_state BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS selected_candidate_bid VARCHAR,
  ADD COLUMN IF NOT EXISTS confidence_trail JSONB DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS flag_to_biz_state VARCHAR DEFAULT 'off',  -- off | drafted | sent
  ADD COLUMN IF NOT EXISTS flag_to_biz_message TEXT,
  ADD COLUMN IF NOT EXISTS dss_connected_at TIMESTAMP;

-- Sqlite fallback (if you're running locally without Postgres, run the equivalent):
-- ALTER TABLE rca_drafts ADD COLUMN stated_issue TEXT;
-- (repeat for each column — sqlite lacks IF NOT EXISTS on ALTER TABLE)
