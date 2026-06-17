-- Add per-creator custom instructions
-- Allows users to set unique instructions per creator (e.g., "use basketball analogies")
-- These are merged with global response_preferences at query time

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name = 'user_creator_preferences' 
    AND column_name = 'custom_instructions'
  ) THEN
    ALTER TABLE user_creator_preferences 
    ADD COLUMN custom_instructions TEXT DEFAULT '';
  END IF;
END $$;
