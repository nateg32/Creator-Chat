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
  default_user_id BIGINT;
BEGIN
  -- Ensure at least one user exists (create a default one if needed)
  IF NOT EXISTS (SELECT 1 FROM users LIMIT 1) THEN
    INSERT INTO users (email, password_hash) 
    VALUES ('default@example.com', '$2b$12$placeholder') 
    RETURNING id INTO default_user_id;
  ELSE
    SELECT id INTO default_user_id FROM users LIMIT 1;
  END IF;
  
  -- Add user_id to existing creators table if it doesn't have it
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'creators') THEN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'creators' AND column_name = 'user_id') THEN
      -- Add user_id column (nullable first)
      ALTER TABLE creators ADD COLUMN user_id BIGINT;
      -- Set all existing creators to the default user
      UPDATE creators SET user_id = default_user_id WHERE user_id IS NULL;
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
