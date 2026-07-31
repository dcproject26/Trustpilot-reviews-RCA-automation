-- Migration 015: RCA v4 output shape.
--
-- The v4 prompt returns six things the schema had no column for. They also
-- live inside the rca_v3 JSON blob, which is what the dashboard's data-v3p
-- editor writes to and therefore the source of truth for RCA content - these
-- columns are the queryable copy the pipeline writes, for reporting and for
-- reading a draft without parsing JSON.
--
-- Nothing is dropped. prevention, wwr_chain and checklist_answers stay in
-- place: v4 stops writing them, and leaving them means a rollback to v3 loses
-- nothing.
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS sop_compliance JSONB;
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS booking_logs   JSONB;
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS flags          JSONB;
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS takedown       JSONB;
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS dss            JSONB;
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS guest_issues   JSONB;

-- issue_specific_answers changes type: v3 stored {question: answer}, v4 stores
-- [{question, verdict, evidence, source, ref}]. The column is already JSON so
-- no DDL is needed, but readers must handle both - a draft written before this
-- deploy still holds the object form.
