"""Gemini context-cache retrieval for hybrid Creator Bot chat."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.db import db
from backend.services.corpus_state import compute_creator_corpus_checksum
from backend.services.llm_provider import GeminiLLMProvider, LLMProviderError, get_gemini_provider
from backend.settings import settings

logger = logging.getLogger(__name__)


REFERENCE_RE = re.compile(
    r"\b(video|clip|reel|post|tweet|caption|transcript|episode|part|section|where (?:he|she|they|you) (?:talk|said|says|mention)|remember that part|what did (?:he|she|they|you) say|quote|timestamp|scene)\b",
    re.IGNORECASE,
)


def _load_jsonish(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _extract_json(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


class GeminiContextCacheService:
    def __init__(self, provider: Optional[GeminiLLMProvider] = None):
        self.provider = provider or get_gemini_provider()

    def should_lookup(self, question: str, *, conversation_history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        if not settings.GEMINI_DYNAMIC_RAG_ENABLED or not settings.GEMINI_API_KEY:
            return {"requires_lookup": False, "reason": "disabled"}
        question = (question or "").strip()
        if not question:
            return {"requires_lookup": False, "reason": "empty"}

        heuristic_hit = bool(REFERENCE_RE.search(question))
        if not heuristic_hit and len(question.split()) < 5:
            return {"requires_lookup": False, "reason": "short_general_message"}

        history_tail = "\n".join(
            f"{m.get('role')}: {m.get('content')}"
            for m in (conversation_history or [])[-4:]
            if m.get("content")
        )
        prompt = f"""
Decide whether this user message requires looking up specific creator corpus content.

Return JSON only:
{{
  "requires_lookup": true,
  "reason": "short reason",
  "query": "best corpus search query",
  "reference_type": "video|post|quote|topic|general"
}}

Use requires_lookup=true for:
- references to a specific video/post/clip/transcript/quote/scene
- "remember that part" style questions
- requests for what the creator said about a topic in their content
- requests for timestamps, exact phrasing, or specific examples

Use requires_lookup=false for greetings, casual chat, general advice, or broad persona conversation.

Recent conversation:
{history_tail}

User message:
{question}
"""
        try:
            raw = self.provider.generate_text(
                prompt=prompt,
                system_instruction="You are a cheap semantic router. Return only valid JSON.",
                model=settings.GEMINI_CACHE_ROUTER_MODEL,
                temperature=0.0,
                json_mode=True,
            )
            routed = _extract_json(raw)
        except Exception as exc:
            logger.warning("Gemini cache router failed, using heuristic: %s", exc)
            routed = {}

        if not routed:
            routed = {
                "requires_lookup": heuristic_hit,
                "reason": "heuristic_reference_match" if heuristic_hit else "router_empty",
                "query": question,
                "reference_type": "topic",
            }
        routed["requires_lookup"] = bool(routed.get("requires_lookup") or heuristic_hit)
        routed.setdefault("query", question)
        return routed

    def _load_creator_cache_meta(self, creator_id: int) -> Dict[str, Any]:
        try:
            row = db.execute_one(
                """
                SELECT gemini_cache_name, gemini_cache_model, gemini_cache_token_count,
                       gemini_cache_expires_at, gemini_cache_corpus_checksum
                FROM creators
                WHERE id = %s
                """,
                (creator_id,),
            )
            return row or {}
        except Exception as exc:
            logger.warning("Gemini cache metadata unavailable: %s", exc)
            return {}

    def _load_corpus_text(self, creator_id: int) -> str:
        rows = db.execute_query(
            """
            SELECT title, content, source, url, metadata
            FROM documents
            WHERE creator_id = %s AND source != 'persona'
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
            LIMIT 250
            """,
            (creator_id,),
        )
        parts: List[str] = []
        char_budget = max(20_000, int(settings.GEMINI_CONTEXT_CACHE_MAX_CHARS))
        used = 0
        for idx, row in enumerate(rows or [], start=1):
            metadata = _load_jsonish(row.get("metadata"))
            title = row.get("title") or metadata.get("title") or f"Source {idx}"
            url = row.get("url") or metadata.get("canonical_url") or metadata.get("source_url") or ""
            content = re.sub(r"\s+", " ", str(row.get("content") or "")).strip()
            if not content:
                continue
            block = f"[Source {idx}] title={title} | source={row.get('source') or ''} | url={url}\n{content}\n"
            if used + len(block) > char_budget:
                break
            parts.append(block)
            used += len(block)
        return "\n---\n".join(parts)

    def ensure_cache(self, creator_id: int, creator_name: str = "") -> Optional[Dict[str, Any]]:
        if not settings.GEMINI_CONTEXT_CACHE_ENABLED or not settings.GEMINI_API_KEY:
            return None
        checksum = compute_creator_corpus_checksum(creator_id)
        meta = self._load_creator_cache_meta(creator_id)
        expires_at = meta.get("gemini_cache_expires_at")
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except Exception:
                expires_at = None
        if (
            meta.get("gemini_cache_name")
            and meta.get("gemini_cache_model") == settings.GEMINI_CACHE_LOOKUP_MODEL
            and meta.get("gemini_cache_corpus_checksum") == checksum
            and (not expires_at or expires_at > datetime.now(timezone.utc))
        ):
            return {
                "name": meta.get("gemini_cache_name"),
                "model": meta.get("gemini_cache_model"),
                "token_count": meta.get("gemini_cache_token_count"),
                "expires_at": expires_at.isoformat() if expires_at else None,
                "cache_hit": True,
            }

        corpus = self._load_corpus_text(creator_id)
        if len(corpus.split()) < 300:
            logger.info("Gemini cache skipped for creator %s: corpus too small.", creator_id)
            return None

        try:
            from google.genai import types
            client = self.provider._get_client()
            cache = client.caches.create(
                model=settings.GEMINI_CACHE_LOOKUP_MODEL,
                config=types.CreateCachedContentConfig(
                    display_name=f"creator-{creator_id}-corpus",
                    system_instruction=(
                        f"You are retrieving exact evidence from {creator_name or 'this creator'}'s approved corpus. "
                        "Find only what is supported by the cached transcripts/posts. Do not invent quotes."
                    ),
                    contents=[corpus],
                    ttl=f"{int(settings.GEMINI_CONTEXT_CACHE_TTL_SECONDS)}s",
                ),
            )
        except Exception as exc:
            logger.warning("Gemini cache create failed for creator %s: %s", creator_id, exc)
            return None

        usage = getattr(cache, "usage_metadata", None)
        token_count = getattr(usage, "total_token_count", None) or getattr(usage, "prompt_token_count", None)
        expire_time = getattr(cache, "expire_time", None)
        db.execute_update(
            """
            UPDATE creators
            SET gemini_cache_name = %s,
                gemini_cache_model = %s,
                gemini_cache_token_count = %s,
                gemini_cache_expires_at = %s,
                gemini_cache_corpus_checksum = %s
            WHERE id = %s
            """,
            (
                getattr(cache, "name", None),
                settings.GEMINI_CACHE_LOOKUP_MODEL,
                token_count,
                expire_time,
                checksum,
                creator_id,
            ),
        )
        return {
            "name": getattr(cache, "name", None),
            "model": settings.GEMINI_CACHE_LOOKUP_MODEL,
            "token_count": token_count,
            "expires_at": expire_time.isoformat() if hasattr(expire_time, "isoformat") else str(expire_time or ""),
            "cache_hit": False,
        }

    def lookup(self, creator_id: int, question: str, creator_name: str = "", *, router: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        router = router or self.should_lookup(question)
        if not router.get("requires_lookup"):
            return None
        cache = self.ensure_cache(creator_id, creator_name)
        if not cache or not cache.get("name"):
            return None
        prompt = f"""
User question:
{question}

Router query:
{router.get('query') or question}

Find the most relevant exact evidence in the cached creator corpus.

Return JSON only:
{{
  "found": true,
  "source_title": "title if known",
  "source_url": "url if known",
  "timestamp": "timestamp if present, otherwise empty",
  "evidence_quote": "exact quote or closest transcript excerpt",
  "retrieved_fact": "brief factual answer grounded in the quote",
  "confidence": 0.0
}}

If the cache does not contain relevant support, return found=false and leave quote/fact empty.
Never fabricate quotes, titles, URLs, or timestamps.
"""
        try:
            from google.genai import types
            client = self.provider._get_client()
            response = client.models.generate_content(
                model=settings.GEMINI_CACHE_LOOKUP_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    cached_content=cache["name"],
                    response_mime_type="application/json",
                    temperature=0.0,
                    safety_settings=self.provider.safety_settings(),
                ),
            )
            parsed = _extract_json(getattr(response, "text", "") or "")
        except Exception as exc:
            logger.warning("Gemini cached lookup failed for creator %s: %s", creator_id, exc)
            return None

        if not parsed or not parsed.get("found"):
            return None
        parsed["cache"] = cache
        parsed["router"] = router
        return parsed

    @staticmethod
    def as_support_chunk(result: Dict[str, Any]) -> Dict[str, Any]:
        quote = str(result.get("evidence_quote") or "").strip()
        fact = str(result.get("retrieved_fact") or "").strip()
        title = str(result.get("source_title") or "Gemini cached creator corpus").strip()
        timestamp = str(result.get("timestamp") or "").strip()
        content = f"[GEMINI CACHED CORPUS RESULT]\nFact: {fact}\nEvidence quote: {quote}"
        if timestamp:
            content += f"\nTimestamp: {timestamp}"
        return {
            "chunk_id": "gemini_cache",
            "chunk_index": 0,
            "content": content,
            "title": title,
            "url": result.get("source_url") or "",
            "distance": 0.0,
            "rerank_score": 1.0,
            "is_gemini_cache": True,
            "source_ref": {
                "title": title,
                "canonical_url": result.get("source_url") or "",
                "timestamp": timestamp,
                "platform": "gemini_cache",
            },
        }


gemini_context_cache_service = GeminiContextCacheService()

