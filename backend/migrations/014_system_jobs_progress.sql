CREATE INDEX IF NOT EXISTS idx_system_jobs_status_created
ON system_jobs (status, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_system_jobs_creator_status_created
ON system_jobs (creator_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_system_jobs_processing_updated
ON system_jobs (updated_at)
WHERE status = 'processing';
