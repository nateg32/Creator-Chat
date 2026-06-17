CREATE TABLE IF NOT EXISTS scrape_queue (
  id BIGSERIAL PRIMARY KEY,
  creator_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  source_id TEXT,
  url TEXT,
  title TEXT,
  raw_text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', -- pending, ingested, rejected
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS scrape_queue_creator_status_idx
ON scrape_queue (creator_id, status, created_at DESC);
