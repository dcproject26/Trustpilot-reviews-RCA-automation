-- Delta v6.5: RCA v3 shape + checklist answers
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS tldr                   TEXT;
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS wwr_chain              JSON DEFAULT '[]';
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS prevention             TEXT;
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS evidence               JSON DEFAULT '[]';
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS issue_specific_answers JSON DEFAULT '{}';
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS checklist_answers      JSON DEFAULT '[]';
