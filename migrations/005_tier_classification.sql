ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS bid_source         VARCHAR;
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS extracted_signals  JSON DEFAULT '{}';
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS narrowing_attempts JSON DEFAULT '[]';
