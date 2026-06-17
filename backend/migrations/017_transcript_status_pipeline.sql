-- Keep the scrape/transcript state machine aligned with the worker pipeline.
-- Older databases only allowed present/missing/error, while the newer worker
-- legitimately moves items through not_started/queued/pending/processing.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'scrape_items_transcript_status_check'
  ) THEN
    ALTER TABLE scrape_items DROP CONSTRAINT scrape_items_transcript_status_check;
  END IF;

  ALTER TABLE scrape_items ADD CONSTRAINT scrape_items_transcript_status_check
    CHECK (transcript_status IN (
      'present',
      'missing',
      'error',
      'not_started',
      'queued',
      'pending',
      'processing'
    ));
END $$;

UPDATE scrape_items
SET transcript_status = 'present'
WHERE COALESCE(transcript, '') <> ''
  AND transcript_status <> 'present';

CREATE INDEX IF NOT EXISTS scrape_items_review_transcript_status_idx
  ON scrape_items(review_status, transcript_status);
