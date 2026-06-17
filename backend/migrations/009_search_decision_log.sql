CREATE TABLE IF NOT EXISTS search_decision_log (
    id SERIAL PRIMARY KEY,
    creator_id TEXT,
    query TEXT,
    should_search BOOLEAN,
    reason TEXT,
    phase TEXT,
    confidence FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sdl_creator_id
    ON search_decision_log(creator_id, created_at DESC);
