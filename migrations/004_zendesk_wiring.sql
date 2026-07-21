-- Zendesk wiring delta: draft columns for ticket IDs + raw comment bodies,
-- and Slack event dedupe table.

ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS zendesk_ticket_ids JSON DEFAULT '[]';
ALTER TABLE rca_drafts ADD COLUMN IF NOT EXISTS timeline_raw       JSON DEFAULT '[]';

CREATE TABLE IF NOT EXISTS slack_events_seen (
  event_id  VARCHAR PRIMARY KEY,
  seen_at   TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_slack_events_seen_at ON slack_events_seen(seen_at);
