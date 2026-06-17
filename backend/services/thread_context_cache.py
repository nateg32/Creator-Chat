"""Short-term evidence cache for active chat threads.

This is deliberately not a long-term memory store. It keeps the last few
retrieval packets for a thread so follow-up questions can reuse transcripts,
links, and live-search evidence without repeating the whole retrieval path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from backend.settings import settings

logger = logging.getLogger(__name__)

try:  # Optional in local dev; production uses Render Key Value/Valkey.
    import redis  # type: ignore
except Exception:  # pragma: no cover - exercised when dependency is absent.
    redis = None  # type: ignore


STOPWORDS = {
    "a", "about", "again", "all", "am", "an", "and", "are", "as", "at", "be",
    "but", "can", "could", "do", "does", "for", "from", "give", "had", "has",
    "have", "he", "help", "her", "him", "his", "how", "i", "im", "in", "is",
    "it", "just", "me", "more", "my", "of", "on", "or", "our", "she", "so",
    "tell", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "to", "u", "up", "ur", "was", "we", "what", "whats", "when",
    "where", "which", "who", "why", "with", "would", "you", "your",
}

FRESHNESS_RE = re.compile(
    r"\b("
    r"latest|newest|current|currently|today|tonight|tomorrow|yesterday|this week|"
    r"this month|right now|live|breaking|news|updated?|recent|most recent|"
    r"price|score|schedule|weather|available now|as of"
    r")\b",
    re.IGNORECASE,
)

FOLLOWUP_RE = re.compile(
    r"\b("
    r"that|this|it|those|them|there|same|above|before|previous|earlier|"
    r"explain|elaborate|more|why|how|link|links|source|video|post|clip|"
    r"episode|podcast|resource|watch|listen|read|break\s*down|breakdown|"
    r"summary|summari[sz]e|summarise|recap|takeaways?|example|plan|step|"
    r"start|recommend|reccomend|wdym"
    r")\b",
    re.IGNORECASE,
)


def _env_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _tokenize(text: str) -> Set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9']+", str(text or "").lower())
        if len(token) > 2 and token not in STOPWORDS
    }


def _history_tail_text(history: Optional[Sequence[Dict[str, Any]]], limit: int = 4) -> str:
    parts: List[str] = []
    for item in list(history or [])[-limit:]:
        content = item.get("content") or item.get("text") or ""
        if content:
            parts.append(str(content))
    return " ".join(parts)


def _safe_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _safe_json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


class ThreadContextCache:
    def __init__(self) -> None:
        self.enabled = bool(getattr(settings, "THREAD_CONTEXT_CACHE_ENABLED", True))
        self.ttl_seconds = max(60, int(getattr(settings, "THREAD_CONTEXT_CACHE_TTL_SECONDS", 900)))
        self.max_entries = max(1, int(getattr(settings, "THREAD_CONTEXT_CACHE_MAX_ENTRIES", 4)))
        self.max_chunks = max(1, int(getattr(settings, "THREAD_CONTEXT_CACHE_MAX_CHUNKS", 6)))
        self.max_bytes = max(10_000, int(getattr(settings, "THREAD_CONTEXT_CACHE_MAX_BYTES", 120_000)))
        self.redis_url = str(getattr(settings, "THREAD_CONTEXT_CACHE_REDIS_URL", "") or "").strip()
        self._redis_client = None
        self._redis_failed = False
        self._memory: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def status(self) -> Dict[str, Any]:
        backend = "redis" if self._redis_available() else "memory"
        return {
            "enabled": self.enabled,
            "backend": backend,
            "ttl_seconds": self.ttl_seconds,
            "max_entries": self.max_entries,
            "redis_configured": bool(self.redis_url),
        }

    def get_reusable_context(
        self,
        *,
        user_id: int,
        creator_id: int,
        thread_id: Optional[str],
        question: str,
        conversation_history: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled or not thread_id or not str(question or "").strip():
            return None
        if FRESHNESS_RE.search(question or ""):
            logger.info("[THREAD_CACHE] miss reason=freshness user=%s creator=%s thread=%s", user_id, creator_id, thread_id)
            return None

        key = self._key(user_id, creator_id, thread_id)
        payload = self._read_payload(key)
        entries = list((payload or {}).get("entries") or [])
        if not entries:
            return None

        best_entry: Optional[Dict[str, Any]] = None
        best_score = 0.0
        for entry in entries:
            score, reason = self._score_entry(entry, question, conversation_history)
            if score > best_score:
                best_score = score
                best_entry = dict(entry)
                best_entry["_reuse_reason"] = reason
                best_entry["_reuse_score"] = round(score, 3)

        if best_entry and best_score >= 0.34:
            logger.info(
                "[THREAD_CACHE] hit user=%s creator=%s thread=%s score=%.3f reason=%s",
                user_id,
                creator_id,
                thread_id,
                best_score,
                best_entry.get("_reuse_reason"),
            )
            return best_entry

        # Topic changed: clear the short-term packet so the next turn starts fresh.
        self.delete(user_id=user_id, creator_id=creator_id, thread_id=thread_id)
        logger.info(
            "[THREAD_CACHE] miss_clear user=%s creator=%s thread=%s best_score=%.3f",
            user_id,
            creator_id,
            thread_id,
            best_score,
        )
        return None

    def save_context(
        self,
        *,
        user_id: int,
        creator_id: int,
        thread_id: Optional[str],
        question: str,
        support_set: Sequence[Dict[str, Any]],
        voice_support_set: Optional[Sequence[Dict[str, Any]]] = None,
        rec_result: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not self.enabled or not thread_id or not support_set:
            return False
        if FRESHNESS_RE.search(question or ""):
            return False

        key = self._key(user_id, creator_id, thread_id)
        payload = self._read_payload(key) or {"entries": []}
        entries = list(payload.get("entries") or [])
        entry = self._build_entry(
            question=question,
            support_set=support_set,
            voice_support_set=voice_support_set,
            rec_result=rec_result,
            metadata=metadata,
        )
        entries = [entry] + [
            existing
            for existing in entries
            if existing.get("query_hash") != entry.get("query_hash")
        ]
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "entries": entries[: self.max_entries],
        }
        raw = _safe_json_dumps(payload)
        if len(raw.encode("utf-8")) > self.max_bytes:
            for cached_entry in payload["entries"]:
                cached_entry["support_set"] = cached_entry.get("support_set", [])[:3]
                cached_entry["voice_support_set"] = cached_entry.get("voice_support_set", [])[:3]
            raw = _safe_json_dumps(payload)
        self._write_payload(key, payload, raw)
        logger.info(
            "[THREAD_CACHE] saved user=%s creator=%s thread=%s chunks=%s entries=%s",
            user_id,
            creator_id,
            thread_id,
            len(support_set or []),
            len(payload["entries"]),
        )
        return True

    def delete(self, *, user_id: int, creator_id: int, thread_id: Optional[str]) -> None:
        if not thread_id:
            return
        key = self._key(user_id, creator_id, thread_id)
        client = self._client()
        if client:
            try:
                client.delete(key)
                return
            except Exception as exc:
                logger.warning("[THREAD_CACHE] redis delete failed: %s", exc)
        with self._lock:
            self._memory.pop(key, None)

    def clear_local(self) -> None:
        with self._lock:
            self._memory.clear()

    def _key(self, user_id: int, creator_id: int, thread_id: str) -> str:
        digest = hashlib.sha1(f"{user_id}:{creator_id}:{thread_id}".encode("utf-8")).hexdigest()
        return f"creatorbot:thread_context:v1:{digest}"

    def _redis_available(self) -> bool:
        return bool(self._client())

    def _client(self) -> Any:
        if not self.redis_url or redis is None or self._redis_failed:
            return None
        if self._redis_client is not None:
            return self._redis_client
        try:
            self._redis_client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.25,
                socket_timeout=0.35,
                retry_on_timeout=False,
            )
            self._redis_client.ping()
            logger.info("[THREAD_CACHE] Redis-compatible cache connected.")
            return self._redis_client
        except Exception as exc:
            self._redis_failed = True
            logger.warning("[THREAD_CACHE] Redis unavailable; using local memory fallback: %s", exc)
            return None

    def _read_payload(self, key: str) -> Optional[Dict[str, Any]]:
        client = self._client()
        if client:
            try:
                raw = client.get(key)
                if not raw:
                    return None
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else None
            except Exception as exc:
                logger.warning("[THREAD_CACHE] redis read failed: %s", exc)
                return None

        now = time.time()
        with self._lock:
            item = self._memory.get(key)
            if not item:
                return None
            if float(item.get("expires_at") or 0) < now:
                self._memory.pop(key, None)
                return None
            return item.get("payload")

    def _write_payload(self, key: str, payload: Dict[str, Any], raw: Optional[str] = None) -> None:
        client = self._client()
        if client:
            try:
                client.setex(key, self.ttl_seconds, raw or _safe_json_dumps(payload))
                return
            except Exception as exc:
                logger.warning("[THREAD_CACHE] redis write failed: %s", exc)

        with self._lock:
            self._memory[key] = {
                "expires_at": time.time() + self.ttl_seconds,
                "payload": payload,
            }

    def _score_entry(
        self,
        entry: Dict[str, Any],
        question: str,
        conversation_history: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Tuple[float, str]:
        q_terms = _tokenize(question)
        history_terms = _tokenize(_history_tail_text(conversation_history))
        query_terms = set(entry.get("query_terms") or [])
        evidence_terms = set(entry.get("evidence_terms") or [])
        all_terms = query_terms | evidence_terms
        if not all_terms:
            return 0.0, "empty_entry"

        overlap = len(q_terms & all_terms)
        direct = overlap / max(1, min(len(q_terms) or 1, len(all_terms)))
        query_overlap = len(q_terms & query_terms) / max(1, min(len(q_terms) or 1, len(query_terms) or 1))
        history_overlap = len(history_terms & all_terms) / max(1, min(len(history_terms) or 1, len(all_terms)))
        followup = self._looks_like_followup(question, conversation_history)

        score = max(direct, query_overlap * 0.9, history_overlap * 0.55)
        reason = "term_overlap"
        if followup:
            score = max(score, min(0.5, score + 0.18))
            reason = "followup_context"
        if not q_terms and followup:
            score = max(score, 0.42)
            reason = "short_followup"
        if (
            followup
            and re.search(
                r"\b(link|links|source|video|post|clip|episode|podcast|resource|where|watch|listen|read|"
                r"break\s*down|breakdown|summary|summari[sz]e|summarise|recap|takeaways?)\b",
                question or "",
                re.IGNORECASE,
            )
            and self._entry_has_links(entry)
        ):
            score = max(score, 0.46)
            reason = "followup_resource_request"
        return score, reason

    def _looks_like_followup(self, question: str, conversation_history: Optional[Sequence[Dict[str, Any]]]) -> bool:
        text = str(question or "").strip()
        if not text or not conversation_history:
            return False
        word_count = len(re.findall(r"[a-z0-9']+", text.lower()))
        return bool(word_count <= 14 and FOLLOWUP_RE.search(text))

    def _entry_has_links(self, entry: Dict[str, Any]) -> bool:
        for chunk in entry.get("support_set") or []:
            source_ref = chunk.get("source_ref") or {}
            if chunk.get("url") or source_ref.get("canonical_url"):
                return True
        return False

    def _build_entry(
        self,
        *,
        question: str,
        support_set: Sequence[Dict[str, Any]],
        voice_support_set: Optional[Sequence[Dict[str, Any]]],
        rec_result: Optional[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        safe_support = [self._sanitize_chunk(chunk) for chunk in list(support_set or [])[: self.max_chunks]]
        safe_voice = [
            self._sanitize_chunk(chunk)
            for chunk in list(voice_support_set or [])[: self.max_chunks]
        ]
        evidence_text = " ".join(
            [
                question,
                " ".join(str(chunk.get("title") or "") for chunk in safe_support),
                " ".join(str((chunk.get("source_ref") or {}).get("title") or "") for chunk in safe_support),
                " ".join(str(chunk.get("content") or "")[:300] for chunk in safe_support),
            ]
        )
        preferred_platforms = []
        if isinstance(rec_result, dict):
            preferred_platforms = list(((rec_result.get("resource_intent") or {}).get("preferred_platforms")) or [])
        return {
            "query": _safe_text(question, 500),
            "query_hash": hashlib.sha1(str(question or "").lower().strip().encode("utf-8")).hexdigest(),
            "query_terms": sorted(_tokenize(question)),
            "evidence_terms": sorted(_tokenize(evidence_text))[:160],
            "support_set": safe_support,
            "voice_support_set": safe_voice or safe_support[:2],
            "rec_result": {
                "best_candidate": None,
                "confidence": float((rec_result or {}).get("confidence") or 0.0) if isinstance(rec_result, dict) else 0.0,
                "resource_intent": {"preferred_platforms": preferred_platforms},
            },
            "metadata": metadata or {},
            "created_at": time.time(),
        }

    def _sanitize_chunk(self, chunk: Dict[str, Any]) -> Dict[str, Any]:
        source_ref = chunk.get("source_ref") or {}
        if not isinstance(source_ref, dict):
            source_ref = {}
        return {
            "content": _safe_text(chunk.get("content"), 1800),
            "snippet": _safe_text(chunk.get("snippet"), 400),
            "title": _safe_text(chunk.get("title") or source_ref.get("title"), 180),
            "url": _safe_text(chunk.get("url") or source_ref.get("canonical_url"), 500),
            "distance": chunk.get("distance"),
            "document_id": chunk.get("document_id"),
            "chunk_id": chunk.get("chunk_id"),
            "resource_type": chunk.get("resource_type"),
            "is_live_web_fact_block": bool(chunk.get("is_live_web_fact_block")),
            "source_ref": {
                "platform": _safe_text(source_ref.get("platform"), 60),
                "content_id": _safe_text(source_ref.get("content_id"), 120),
                "canonical_url": _safe_text(source_ref.get("canonical_url") or chunk.get("url"), 500),
                "title": _safe_text(source_ref.get("title") or chunk.get("title"), 180),
                "published_at": _safe_text(source_ref.get("published_at"), 80),
                "content_type": _safe_text(source_ref.get("content_type"), 80),
                "start_time_sec": source_ref.get("start_time_sec"),
                "end_time_sec": source_ref.get("end_time_sec"),
            },
        }


thread_context_cache = ThreadContextCache()
