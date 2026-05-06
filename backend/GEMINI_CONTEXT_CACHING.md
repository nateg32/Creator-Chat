# Gemini Context Caching Design Note

Current state: Creator Bot uses dynamic Gemini context-cache lookup for specific content-reference questions, then injects the retrieved fact into the normal chat renderer.

Durable per-creator cache metadata is stored on `creators`:

- `creator_id`
- `gemini_cache_name`
- `model`
- `token_count`
- `expires_at`
- `content_corpus_checksum`

Recommended behavior:

1. Create/reuse a cache only when the approved corpus checksum matches.
2. Expire or delete cache metadata when the corpus changes or Gemini reports the cache as expired.
3. Keep normal RAG as the fallback if cache creation or lookup fails.
4. Gate cache usage with `GEMINI_CONTEXT_CACHE_ENABLED`.
5. Gate the semantic lookup route with `GEMINI_DYNAMIC_RAG_ENABLED`.

Model defaults:

- `GEMINI_CACHE_LOOKUP_MODEL=gemini-3-pro-preview`
- `GEMINI_CACHE_ROUTER_MODEL=gemini-3-flash-preview`

If Google exposes a Flash-Lite model to the account, set `GEMINI_CACHE_ROUTER_MODEL` to that exact official model ID in `.env`.
