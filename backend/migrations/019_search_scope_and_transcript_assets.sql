-- Separate per-creator search scope from globally reusable transcript assets.
-- scrape_items still stages public source rows, but scrape_runs.creator_id lets
-- approval UI interpret review state through the current creator's corpus.

ALTER TABLE scrape_runs
  ADD COLUMN IF NOT EXISTS creator_id BIGINT REFERENCES creators(id) ON DELETE CASCADE;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'scrape_runs' AND column_name = 'created_at'
  ) THEN
    CREATE INDEX IF NOT EXISTS idx_scrape_runs_creator_created
      ON scrape_runs (creator_id, created_at DESC)
      WHERE creator_id IS NOT NULL;
  ELSIF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'scrape_runs' AND column_name = 'started_at'
  ) THEN
    CREATE INDEX IF NOT EXISTS idx_scrape_runs_creator_started
      ON scrape_runs (creator_id, started_at DESC)
      WHERE creator_id IS NOT NULL;
  ELSE
    CREATE INDEX IF NOT EXISTS idx_scrape_runs_creator
      ON scrape_runs (creator_id)
      WHERE creator_id IS NOT NULL;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS transcript_assets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_key TEXT NOT NULL UNIQUE,
  source_url TEXT,
  platform TEXT,
  title TEXT,
  transcript TEXT,
  transcript_status TEXT NOT NULL DEFAULT 'present',
  transcript_checksum TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transcript_assets_source_url
  ON transcript_assets (source_url)
  WHERE source_url IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_transcript_assets_platform
  ON transcript_assets (platform)
  WHERE platform IS NOT NULL;
