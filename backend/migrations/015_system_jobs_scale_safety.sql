ALTER TABLE system_jobs
ADD COLUMN IF NOT EXISTS dedupe_key TEXT;

ALTER TABLE system_jobs
ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ DEFAULT NOW();

UPDATE system_jobs
SET available_at = COALESCE(available_at, created_at, NOW())
WHERE available_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_system_jobs_active_dedupe
ON system_jobs (dedupe_key)
WHERE dedupe_key IS NOT NULL
  AND status IN ('queued', 'processing');

CREATE INDEX IF NOT EXISTS idx_system_jobs_available
ON system_jobs (status, available_at, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_documents_creator_source_identity
ON documents (creator_id, source, source_id)
WHERE source_id IS NOT NULL AND source_id <> '';

CREATE INDEX IF NOT EXISTS idx_chunks_creator_document
ON chunks (creator_id, document_id);
