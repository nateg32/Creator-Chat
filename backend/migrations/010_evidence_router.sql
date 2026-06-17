CREATE TABLE IF NOT EXISTS creator_entity_graph (
    creator_id TEXT PRIMARY KEY,
    graph JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fact_registry (
    id SERIAL PRIMARY KEY,
    creator_id TEXT NOT NULL,
    entity_subject TEXT NOT NULL,
    entity_type TEXT,
    fact_field TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    source_url TEXT,
    source_domain TEXT,
    source_title TEXT,
    source_snippet TEXT,
    confidence FLOAT DEFAULT 0.8,
    freshness TEXT DEFAULT 'low',
    verified_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fact_registry_unique
ON fact_registry (creator_id, entity_subject, fact_field);

CREATE INDEX IF NOT EXISTS idx_fact_registry_recent
ON fact_registry (creator_id, verified_at DESC);

CREATE TABLE IF NOT EXISTS evidence_plan_log (
    id SERIAL PRIMARY KEY,
    creator_id TEXT,
    query TEXT,
    resolved_query TEXT,
    primary_world TEXT,
    secondary_worlds JSONB DEFAULT '[]'::jsonb,
    answer_mode TEXT,
    should_search_web BOOLEAN,
    should_search_corpus BOOLEAN,
    should_verify BOOLEAN,
    freshness_required TEXT,
    entity_subject TEXT,
    entity_type TEXT,
    risk_flags JSONB DEFAULT '[]'::jsonb,
    contradiction_risk BOOLEAN DEFAULT FALSE,
    confidence_score FLOAT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_evidence_plan_log_creator
ON evidence_plan_log (creator_id, created_at DESC);
