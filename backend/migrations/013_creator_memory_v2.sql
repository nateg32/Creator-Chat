-- Creator memory v2 foundation.
-- This migration is intentionally additive: it does not drop or rename the
-- legacy documents/chunks/scrape tables. New ingestion can dual-write here,
-- then reads can migrate once the backfill is verified.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS creator_platform_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    handle TEXT,
    profile_url TEXT,
    platform_user_id TEXT,
    display_name TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    scrape_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_scraped_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_creator_platform_accounts_creator
ON creator_platform_accounts (creator_id, platform);

CREATE UNIQUE INDEX IF NOT EXISTS idx_creator_platform_accounts_unique_source
ON creator_platform_accounts (creator_id, platform, COALESCE(platform_user_id, handle, profile_url, ''));

CREATE TABLE IF NOT EXISTS content_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    scrape_run_id UUID,
    scrape_item_id UUID,
    legacy_document_id BIGINT,
    platform TEXT NOT NULL,
    content_type TEXT NOT NULL,
    source_id TEXT,
    source_url TEXT,
    title TEXT,
    caption TEXT,
    canonical_text TEXT,
    raw_text_storage_url TEXT,
    raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_checksum TEXT NOT NULL,
    transcript_status TEXT NOT NULL DEFAULT 'unknown',
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (creator_id, content_checksum)
);

CREATE INDEX IF NOT EXISTS idx_content_documents_creator_published
ON content_documents (creator_id, published_at DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_content_documents_creator_source
ON content_documents (creator_id, platform, source_id);

CREATE INDEX IF NOT EXISTS idx_content_documents_source_url
ON content_documents (source_url)
WHERE source_url IS NOT NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'scrape_runs')
       AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'content_documents_scrape_run_id_fkey')
    THEN
        ALTER TABLE content_documents
        ADD CONSTRAINT content_documents_scrape_run_id_fkey
        FOREIGN KEY (scrape_run_id) REFERENCES scrape_runs(id) ON DELETE SET NULL;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'scrape_items')
       AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'content_documents_scrape_item_id_fkey')
    THEN
        ALTER TABLE content_documents
        ADD CONSTRAINT content_documents_scrape_item_id_fkey
        FOREIGN KEY (scrape_item_id) REFERENCES scrape_items(id) ON DELETE SET NULL;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'documents')
       AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'content_documents_legacy_document_id_fkey')
    THEN
        ALTER TABLE content_documents
        ADD CONSTRAINT content_documents_legacy_document_id_fkey
        FOREIGN KEY (legacy_document_id) REFERENCES documents(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS transcript_segments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES content_documents(id) ON DELETE CASCADE,
    segment_index INTEGER NOT NULL,
    start_seconds NUMERIC,
    end_seconds NUMERIC,
    speaker TEXT,
    text TEXT NOT NULL,
    char_start INTEGER,
    char_end INTEGER,
    raw_segment JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, segment_index)
);

CREATE INDEX IF NOT EXISTS idx_transcript_segments_document_index
ON transcript_segments (document_id, segment_index);

CREATE INDEX IF NOT EXISTS idx_transcript_segments_time
ON transcript_segments (document_id, start_seconds, end_seconds);

CREATE TABLE IF NOT EXISTS content_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES content_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_type TEXT NOT NULL DEFAULT 'transcript',
    text TEXT NOT NULL,
    token_count INTEGER,
    segment_start_index INTEGER,
    segment_end_index INTEGER,
    char_start INTEGER,
    char_end INTEGER,
    chunking_strategy TEXT NOT NULL DEFAULT 'structured_v1',
    chunking_version INTEGER NOT NULL DEFAULT 1,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_checksum TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_content_chunks_creator
ON content_chunks (creator_id);

CREATE INDEX IF NOT EXISTS idx_content_chunks_document
ON content_chunks (document_id, chunk_index);

CREATE TABLE IF NOT EXISTS content_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id UUID NOT NULL REFERENCES content_chunks(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    embedding VECTOR,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chunk_id, provider, model)
);

CREATE INDEX IF NOT EXISTS idx_content_embeddings_chunk
ON content_embeddings (chunk_id);

CREATE TABLE IF NOT EXISTS persona_analyses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'draft',
    analysis_md TEXT NOT NULL,
    structured_analysis JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_provider TEXT NOT NULL DEFAULT 'gemini',
    model TEXT NOT NULL,
    source_corpus_checksum TEXT NOT NULL,
    source_document_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence_score NUMERIC,
    data_volume TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_persona_analyses_creator_created
ON persona_analyses (creator_id, created_at DESC);

CREATE TABLE IF NOT EXISTS soul_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    persona_analysis_id UUID REFERENCES persona_analyses(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    version INTEGER NOT NULL DEFAULT 1,
    soul_md TEXT NOT NULL,
    model_provider TEXT NOT NULL DEFAULT 'gemini',
    model TEXT NOT NULL,
    source_corpus_checksum TEXT NOT NULL,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (creator_id, version)
);

CREATE INDEX IF NOT EXISTS idx_soul_versions_creator_status
ON soul_versions (creator_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS runtime_prompts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    soul_version_id UUID REFERENCES soul_versions(id) ON DELETE CASCADE,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    prompt_md TEXT NOT NULL,
    compressed_summary TEXT,
    model_provider TEXT NOT NULL DEFAULT 'gemini',
    model TEXT NOT NULL,
    token_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_prompts_one_active
ON runtime_prompts (creator_id)
WHERE active;

CREATE INDEX IF NOT EXISTS idx_runtime_prompts_creator_created
ON runtime_prompts (creator_id, created_at DESC);

CREATE TABLE IF NOT EXISTS gemini_context_caches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    cache_name TEXT NOT NULL UNIQUE,
    model TEXT NOT NULL,
    corpus_checksum TEXT NOT NULL,
    source_document_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    token_count INTEGER,
    expires_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gemini_context_caches_creator_status
ON gemini_context_caches (creator_id, status, expires_at DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS retrieval_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id BIGINT REFERENCES creators(id) ON DELETE SET NULL,
    thread_id UUID,
    message_id UUID,
    query TEXT NOT NULL,
    route_decision TEXT,
    chat_provider TEXT,
    retrieval_provider TEXT,
    used_vector_rag BOOLEAN NOT NULL DEFAULT FALSE,
    used_gemini_cache BOOLEAN NOT NULL DEFAULT FALSE,
    gemini_cache_id UUID REFERENCES gemini_context_caches(id) ON DELETE SET NULL,
    retrieved_chunk_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    retrieved_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
    latency_ms INTEGER,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_retrieval_events_creator_created
ON retrieval_events (creator_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_retrieval_events_thread_created
ON retrieval_events (thread_id, created_at DESC);
