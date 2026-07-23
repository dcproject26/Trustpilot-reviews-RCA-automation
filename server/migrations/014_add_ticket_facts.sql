-- Migration 014: Add ticket_facts column to rca_drafts
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS ticket_facts JSONB;
