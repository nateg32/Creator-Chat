-- Reduce transcript duplication while preserving searchable chunk evidence.
-- documents.content becomes a compact preview for chunked content; chunks remain
-- the retrieval source of truth.

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

CREATE INDEX IF NOT EXISTS chunks_document_id_chunk_index_idx
  ON chunks(document_id, chunk_index);

CREATE INDEX IF NOT EXISTS embeddings_chunk_id_idx
  ON embeddings(chunk_id);

DELETE FROM embeddings e
WHERE NOT EXISTS (
  SELECT 1 FROM chunks c WHERE c.id = e.chunk_id
);

DELETE FROM chunks c
WHERE NOT EXISTS (
  SELECT 1 FROM documents d WHERE d.id = c.document_id
);

DELETE FROM creator_documents cd
WHERE NOT EXISTS (
  SELECT 1 FROM documents d WHERE d.id = cd.document_id
);

UPDATE documents d
SET
  metadata = COALESCE(d.metadata, '{}'::jsonb)
    || jsonb_build_object(
      'storage_policy', 'chunked_preview_v1',
      'full_text_available_in', 'chunks',
      'source_text_char_count', length(COALESCE(d.content, '')),
      'document_preview_char_count', LEAST(length(COALESCE(d.content, '')), 1200)
    ),
  content = trim(
    concat_ws(
      E'\n\n',
      NULLIF(
        concat_ws(
          ' | ',
          NULLIF(d.title, ''),
          NULLIF(COALESCE(d.metadata->>'platform', d.source, ''), ''),
          NULLIF(COALESCE(d.metadata->>'source_url', d.metadata->>'canonical_url', d.url, ''), '')
        ),
        ''
      ),
      CASE
        WHEN length(COALESCE(d.content, '')) > 1200
          THEN left(COALESCE(d.content, ''), 1200) || '...'
        ELSE COALESCE(d.content, '')
      END
    )
  )
WHERE d.source != 'persona'
  AND COALESCE(d.metadata->>'storage_policy', '') != 'chunked_preview_v1'
  AND length(COALESCE(d.content, '')) > 1600
  AND EXISTS (
    SELECT 1 FROM chunks c WHERE c.document_id = d.id
  );

UPDATE scrape_items
SET transcript = NULL,
    metadata = COALESCE(metadata, '{}'::jsonb)
      || jsonb_build_object('transcript_storage', 'pruned_after_review')
WHERE review_status = 'denied'
  AND COALESCE(transcript, '') <> '';
