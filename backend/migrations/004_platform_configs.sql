-- Add platform_configs JSONB to creators.
-- Schema: { "instagram": { "enabled": true, "url": "...", "timeFilter": { "mode": "since"|"last_days"|"all", "since"?: "YYYY-MM-DD", "days"?: number }, "maxItems"?: number }, ... }

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'creators' AND column_name = 'platform_configs'
  ) THEN
    ALTER TABLE creators ADD COLUMN platform_configs JSONB NOT NULL DEFAULT '{}'::jsonb;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS creators_platform_configs_gin_idx ON creators USING GIN (platform_configs);
