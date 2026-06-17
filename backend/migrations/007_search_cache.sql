-- search_cache table for ResearchProvider
CREATE TABLE IF NOT EXISTS search_cache (
    id BIGSERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    query_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    results JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(creator_id, query_hash, provider)
);

CREATE INDEX IF NOT EXISTS search_cache_lookup_idx ON search_cache(creator_id, query_hash, provider);
CREATE INDEX IF NOT EXISTS search_cache_created_at_idx ON search_cache(created_at);
