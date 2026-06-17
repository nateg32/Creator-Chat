-- Users table for authentication
CREATE TABLE IF NOT EXISTS users (
  id BIGSERIAL PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Sessions table for session management
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS sessions_user_id_idx ON sessions(user_id);
CREATE INDEX IF NOT EXISTS sessions_expires_at_idx ON sessions(expires_at);

-- Creators table (enhanced from just using creator_id as integer)
-- Add user_id column if creators table exists without it
DO $$
DECLARE
  owner_user_id BIGINT;
  creators_has_rows BOOLEAN := FALSE;
BEGIN
  -- Add user_id to existing creators table if it doesn't have it
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'creators') THEN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'creators' AND column_name = 'user_id') THEN
      SELECT EXISTS (SELECT 1 FROM creators) INTO creators_has_rows;

      -- Fresh installs create users through the auth flow. Only legacy backfills
      -- need an existing owner row to attach old creators to.
      IF creators_has_rows THEN
        SELECT id INTO owner_user_id FROM users ORDER BY id LIMIT 1;
        IF owner_user_id IS NULL THEN
          RAISE EXCEPTION
            'Cannot backfill creators.user_id because users is empty. Create a real user, then rerun 002_auth_creators.sql.';
        END IF;
      END IF;

      -- Add user_id column (nullable first)
      ALTER TABLE creators ADD COLUMN user_id BIGINT;
      -- Attach legacy creators to the first existing user when this migration is
      -- backfilling an older private install.
      IF creators_has_rows THEN
        UPDATE creators SET user_id = owner_user_id WHERE user_id IS NULL;
      END IF;
      -- Make it NOT NULL and add foreign key
      ALTER TABLE creators ALTER COLUMN user_id SET NOT NULL;
      ALTER TABLE creators ADD CONSTRAINT creators_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
    END IF;
  END IF;
END $$;

-- Create table if it doesn't exist
CREATE TABLE IF NOT EXISTS creators (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  handle TEXT,
  platforms JSONB DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS creators_user_id_idx ON creators(user_id);

-- Update existing tables to reference creators table if needed
-- Note: This assumes existing creator_id values can be migrated
-- For now, we'll support both old integer creator_id and new creator records
