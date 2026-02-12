-- Advanced Scrape Pipeline Migration

-- 1. Scrape Cursors: Tracks where we left off for each creator/platform
CREATE TABLE IF NOT EXISTS scrape_cursors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    platform_key TEXT NOT NULL,
    cursor_data JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(creator_id, platform_key)
);

-- 2. Source Items: Raw items before chunking/embedding
CREATE TABLE IF NOT EXISTS source_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    platform_key TEXT NOT NULL,
    source_id TEXT NOT NULL, -- Platform native ID
    source_url TEXT,
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    content_type TEXT, -- 'post', 'video', 'article', etc.
    raw_json JSONB,
    normalized_text TEXT,
    content_hash TEXT, -- SHA256 of normalized text
    quality_score INT DEFAULT 0,
    status TEXT DEFAULT 'NEW', -- NEW, FILTERED_OUT, QUEUED, INGESTED, FAILED
    UNIQUE(creator_id, platform_key, source_id)
);

CREATE INDEX IF NOT EXISTS idx_source_items_creator_platform_pub ON source_items(creator_id, platform_key, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_items_creator_hash ON source_items(creator_id, content_hash);

-- 3. Scrape Runs: Observability for each scrape execution
CREATE TABLE IF NOT EXISTS scrape_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    platform_key TEXT NOT NULL,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT DEFAULT 'RUNNING', -- RUNNING, COMPLETED, FAILED, PARTIAL
    items_fetched INT DEFAULT 0,
    items_new INT DEFAULT 0,
    items_deduped INT DEFAULT 0,
    items_filtered_out INT DEFAULT 0,
    jobs_enqueued INT DEFAULT 0,
    duration_ms INT,
    error_message TEXT
);

-- 4. Ingest Jobs: Async processing queue
CREATE TABLE IF NOT EXISTS ingest_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    platform_key TEXT NOT NULL,
    source_item_id UUID REFERENCES source_items(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL, -- 'EMBED', 'TRANSCRIBE', 'BOTH'
    status TEXT DEFAULT 'PENDING', -- PENDING, RUNNING, COMPLETED, FAILED, RETRY
    priority INT DEFAULT 1,
    attempts INT DEFAULT 0,
    next_run_at TIMESTAMPTZ DEFAULT NOW(),
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingest_jobs_status_next ON ingest_jobs(status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_ingest_jobs_creator_status ON ingest_jobs(creator_id, status);

-- Add updated_at trigger for ingest_jobs
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS update_ingest_jobs_modtime ON ingest_jobs;
CREATE TRIGGER update_ingest_jobs_modtime BEFORE UPDATE ON ingest_jobs FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
