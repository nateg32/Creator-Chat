-- Chat Threads and Messages
-- Implements ChatGPT-style multi-threaded conversations

-- Chat threads table
CREATE TABLE IF NOT EXISTS chat_threads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
  title TEXT NOT NULL DEFAULT 'New conversation',
  title_locked BOOLEAN NOT NULL DEFAULT false,
  last_preview TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_message_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_active BOOLEAN NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS chat_threads_user_creator_idx ON chat_threads(user_id, creator_id);
CREATE INDEX IF NOT EXISTS chat_threads_last_message_idx ON chat_threads(last_message_at DESC);

-- Chat messages table
CREATE TABLE IF NOT EXISTS chat_messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id UUID NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS chat_messages_thread_idx ON chat_messages(thread_id, created_at);

-- User preferences for last active thread per creator
CREATE TABLE IF NOT EXISTS user_creator_preferences (
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
  last_active_thread_id UUID REFERENCES chat_threads(id) ON DELETE SET NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, creator_id)
);

-- Add display_name and response_preferences to users table if not exists
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'display_name') THEN
    ALTER TABLE users ADD COLUMN display_name TEXT;
  END IF;
  
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'profile_picture_url') THEN
    ALTER TABLE users ADD COLUMN profile_picture_url TEXT;
  END IF;
  
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'response_preferences') THEN
    ALTER TABLE users ADD COLUMN response_preferences JSONB DEFAULT '{}'::jsonb;
  END IF;
END $$;
