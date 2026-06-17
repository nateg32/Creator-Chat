
-- Conversation Memory Table
-- Stores extracted facts and tone profile for Natural Recall

CREATE TABLE IF NOT EXISTS conversation_memories (
    user_id BIGINT NOT NULL,
    creator_id BIGINT NOT NULL,
    facts JSONB DEFAULT '[]'::jsonb,
    tone_profile JSONB DEFAULT '{}'::jsonb,
    last_interaction TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, creator_id)
);

CREATE INDEX IF NOT EXISTS conversation_memories_updated_idx ON conversation_memories(last_interaction);
