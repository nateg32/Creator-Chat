-- Persist search progress so it survives backend restarts.

CREATE TABLE IF NOT EXISTS search_progress (
    search_id UUID PRIMARY KEY,
    progress_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS search_progress_updated_at_idx ON search_progress(updated_at);
