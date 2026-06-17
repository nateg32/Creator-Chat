CREATE TABLE IF NOT EXISTS recommendation_asset_profiles (
    document_id BIGINT PRIMARY KEY,
    creator_id BIGINT NOT NULL,
    summary TEXT,
    problem_solved TEXT,
    audience_level TEXT,
    content_mode TEXT,
    format_label TEXT,
    actionability_score FLOAT DEFAULT 0.5,
    primary_topic TEXT,
    secondary_topics JSONB DEFAULT '[]'::jsonb,
    frameworks JSONB DEFAULT '[]'::jsonb,
    metadata JSONB DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recommendation_asset_profiles_creator
    ON recommendation_asset_profiles (creator_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS recommendation_feedback_log (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    creator_id BIGINT,
    thread_id TEXT,
    event_type TEXT NOT NULL,
    query TEXT,
    candidate_title TEXT,
    candidate_url TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recommendation_feedback_thread
    ON recommendation_feedback_log (thread_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_recommendation_feedback_creator
    ON recommendation_feedback_log (creator_id, created_at DESC);
