CREATE TABLE IF NOT EXISTS system_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id BIGINT REFERENCES creators(id) ON DELETE CASCADE,
    job_type VARCHAR(50) NOT NULL,
    status VARCHAR(20) DEFAULT 'queued',
    progress_percent INT DEFAULT 0,
    payload JSONB DEFAULT '{}'::jsonb,
    locked_at TIMESTAMPTZ,
    locked_by VARCHAR(255),
    error_log TEXT,
    retry_count INT DEFAULT 0,
    message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_system_jobs_status ON system_jobs(status);
CREATE INDEX IF NOT EXISTS idx_system_jobs_creator_id ON system_jobs(creator_id);

CREATE TABLE IF NOT EXISTS creator_documents (
    creator_id BIGINT REFERENCES creators(id) ON DELETE CASCADE,
    document_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (creator_id, document_id)
);

-- Note: In PostgreSQL, altering a column requires checking existing data.
-- We will enforce UNIQUE(source, source_id) iteratively by attempting to add the constraint.
-- First, clean up exact duplicates if any exist.
DELETE FROM documents
WHERE id IN (
  SELECT id FROM (
    SELECT id, ROW_NUMBER() OVER( PARTITION BY source, source_id ORDER BY id ASC ) AS row_num
    FROM documents
    WHERE source_id IS NOT NULL AND source_id != ''
  ) t
  WHERE t.row_num > 1
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'documents_source_source_id_key'
    ) THEN
        ALTER TABLE documents ADD CONSTRAINT documents_source_source_id_key UNIQUE (source, source_id);
    END IF;
END $$;
