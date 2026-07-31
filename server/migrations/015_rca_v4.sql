-- Migration 015: RCA v4 output shape.
--
-- The v4 prompt returns six things the schema had no column for. Each of these
-- columns is a PROJECTION of a key inside the rca_v3 JSON blob, not a field of
-- its own. rca_v3 is the store; these are the flat copy so reporting queries
-- do not have to parse JSON.
--
-- One writer: the pipeline, at generation time. One editor: rca_v3, through
-- PATCH /draft-v2. The client never writes these columns. If you add another
-- column here, keep that rule - two writers for one value is how the copies
-- drift, and the reader is then guessing.
--
-- Nothing is dropped. prevention, wwr_chain and checklist_answers stay in
-- place: v4 stops writing them, and leaving them means a rollback to v3 loses
-- nothing.
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS sop_compliance JSONB;  -- rca_v3.sop_compliance
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS booking_logs   JSONB;  -- rca_v3.booking_logs
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS flags          JSONB;  -- rca_v3.flags
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS takedown       JSONB;  -- rca_v3.takedown
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS dss            JSONB;  -- rca_v3.dss
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS guest_issues   JSONB;  -- rca_v3.what_went_wrong.guest_issues

COMMENT ON COLUMN rca_drafts.sop_compliance IS 'projection of rca_v3.sop_compliance; pipeline-written, never client-written';
COMMENT ON COLUMN rca_drafts.booking_logs   IS 'projection of rca_v3.booking_logs; pipeline-written, never client-written';
COMMENT ON COLUMN rca_drafts.flags          IS 'projection of rca_v3.flags; pipeline-written, never client-written';
COMMENT ON COLUMN rca_drafts.takedown       IS 'projection of rca_v3.takedown; pipeline-written, never client-written';
COMMENT ON COLUMN rca_drafts.dss            IS 'projection of rca_v3.dss; pipeline-written, never client-written';
COMMENT ON COLUMN rca_drafts.guest_issues   IS 'projection of rca_v3.what_went_wrong.guest_issues; pipeline-written, never client-written';

-- issue_specific_answers changes type: v3 stored {question: answer}, v4 stores
-- [{question, verdict, evidence, source, ref}]. The column is already JSON so
-- no DDL is needed, but readers must handle both - a draft written before this
-- deploy still holds the object form.

-- Provenance, not content: which prompt wrote this RCA. Null means the row
-- predates the stamp and its shape has to be guessed from its keys - which is
-- the ambiguity this removes. A v3 draft read as a v4 checkpoint reports every
-- v3 artefact as a validator failure, with nothing on the row to say otherwise.
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS rca_prompt_version VARCHAR;
COMMENT ON COLUMN rca_drafts.rca_prompt_version IS 'prompt that produced rca_v3, e.g. rca_v4; null = predates the stamp';
