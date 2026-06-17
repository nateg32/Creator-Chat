-- Migration: Instagram Reels Staging Tables
-- Creates staging tables for approval gate before ingestion into knowledge base

-- Scrape runs table (tracks each scrape request)
CREATE TABLE IF NOT EXISTS scrape_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_url TEXT NOT NULL,
  platform TEXT NOT NULL DEFAULT 'instagram',
  mode TEXT, -- 'reel' or 'profile'
  creator_handle TEXT,
  items_found INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS scrape_runs_created_at_idx ON scrape_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS scrape_runs_platform_idx ON scrape_runs(platform);

-- Scrape items table (staging approval gate)
CREATE TABLE IF NOT EXISTS scrape_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scrape_run_id UUID NOT NULL REFERENCES scrape_runs(id) ON DELETE CASCADE,
  creator_handle TEXT NOT NULL,
  content_type TEXT NOT NULL DEFAULT 'reel',
  source_url TEXT NOT NULL UNIQUE,
  caption TEXT,
  transcript TEXT,
  transcript_status TEXT NOT NULL DEFAULT 'missing', -- 'present', 'missing', 'error'
  published_at TIMESTAMPTZ,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb, -- likes, views, duration, hashtags, mentions, audio, etc.
  review_status TEXT NOT NULL DEFAULT 'pending_review', -- 'pending_review', 'approved', 'denied'
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS scrape_items_scrape_run_id_idx ON scrape_items(scrape_run_id);
CREATE INDEX IF NOT EXISTS scrape_items_review_status_idx ON scrape_items(review_status);
CREATE INDEX IF NOT EXISTS scrape_items_transcript_status_idx ON scrape_items(transcript_status);
CREATE INDEX IF NOT EXISTS scrape_items_source_url_idx ON scrape_items(source_url);
CREATE INDEX IF NOT EXISTS scrape_items_created_at_idx ON scrape_items(created_at DESC);

-- Add transcript_status check constraint
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'scrape_items_transcript_status_check'
  ) THEN
    ALTER TABLE scrape_items ADD CONSTRAINT scrape_items_transcript_status_check
      CHECK (transcript_status IN ('present', 'missing', 'error'));
  END IF;
  
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'scrape_items_review_status_check'
  ) THEN
    ALTER TABLE scrape_items ADD CONSTRAINT scrape_items_review_status_check
      CHECK (review_status IN ('pending_review', 'approved', 'denied'));
  END IF;
END $$;
