-- 003: repair schema drift on review_metrics — columns present in the
-- SQLAlchemy model (server/db.py ReviewMetric) but missing from older DBs,
-- which caused 500s on PATCH /api/reviews/{id}/draft-v2. Idempotent.
ALTER TABLE review_metrics ADD COLUMN IF NOT EXISTS l1 TEXT;
ALTER TABLE review_metrics ADD COLUMN IF NOT EXISTS l2 TEXT;
ALTER TABLE review_metrics ADD COLUMN IF NOT EXISTS dss_connected BOOLEAN DEFAULT FALSE;
ALTER TABLE review_metrics ADD COLUMN IF NOT EXISTS flagged_to_biz BOOLEAN DEFAULT FALSE;
