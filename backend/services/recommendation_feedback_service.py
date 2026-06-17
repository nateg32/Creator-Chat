"""
Recommendation impression and interaction logging.

This provides the data exhaust needed to tune the recommendation stack against
real user behavior instead of relying on vibes.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

try:
    from backend.db import db
except Exception:  # pragma: no cover - lightweight test environments may not ship psycopg
    db = type(
        "_NullDB",
        (),
        {
            "execute_update": staticmethod(lambda *args, **kwargs: None),
            "execute_insert": staticmethod(lambda *args, **kwargs: None),
            "execute_query": staticmethod(lambda *args, **kwargs: []),
            "execute_one": staticmethod(lambda *args, **kwargs: None),
        },
    )()


logger = logging.getLogger(__name__)


_SCHEMA_READY = False


def _ensure_schema() -> bool:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return True
    try:
        db.execute_update(
            """
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
            )
            """
        )
        db.execute_update(
            """
            CREATE INDEX IF NOT EXISTS idx_recommendation_feedback_thread
            ON recommendation_feedback_log (thread_id, created_at DESC)
            """
        )
        db.execute_update(
            """
            CREATE INDEX IF NOT EXISTS idx_recommendation_feedback_creator
            ON recommendation_feedback_log (creator_id, created_at DESC)
            """
        )
        _SCHEMA_READY = True
        return True
    except Exception as exc:
        logger.warning("Recommendation feedback schema bootstrap failed: %s", exc)
        return False


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


class RecommendationFeedbackService:
    def log_event(
        self,
        *,
        event_type: str,
        user_id: Optional[int] = None,
        creator_id: Optional[int] = None,
        thread_id: Optional[str] = None,
        query: str = "",
        candidate_title: str = "",
        candidate_url: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        if not _ensure_schema():
            return None
        try:
            return db.execute_insert(
                """
                INSERT INTO recommendation_feedback_log (
                    user_id,
                    creator_id,
                    thread_id,
                    event_type,
                    query,
                    candidate_title,
                    candidate_url,
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (
                    user_id,
                    creator_id,
                    thread_id,
                    _clean(event_type),
                    _clean(query),
                    _clean(candidate_title),
                    _clean(candidate_url),
                    json.dumps(metadata or {}),
                ),
            )
        except Exception as exc:
            logger.warning("Recommendation feedback log failed: %s", exc)
            return None

    def log_impression(
        self,
        *,
        user_id: Optional[int],
        creator_id: Optional[int],
        thread_id: Optional[str],
        query: str,
        best_candidate: Optional[Dict[str, Any]],
        alternate_candidates: Optional[List[Dict[str, Any]]] = None,
        resource_intent: Optional[Dict[str, Any]] = None,
        confidence: Optional[float] = None,
        query_variants: Optional[List[str]] = None,
        retrieval_debug: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        shown_candidates = []
        for position, candidate in enumerate([best_candidate] + list(alternate_candidates or []), start=1):
            if not candidate:
                continue
            shown_candidates.append(
                {
                    "position": position,
                    "title": _clean(candidate.get("title") or ""),
                    "url": _clean(candidate.get("url") or ""),
                    "platform": _clean(candidate.get("platform") or (candidate.get("source_ref") or {}).get("platform") or ""),
                    "score": float(candidate.get("rerank_score") or 0.0),
                    "asset_profile": candidate.get("asset_profile") or {},
                }
            )

        metadata = {
            "confidence": float(confidence or 0.0),
            "resource_intent": resource_intent or {},
            "shown_candidates": shown_candidates,
            "query_variants": list(query_variants or []),
            "retrieval_debug": retrieval_debug or {},
        }
        return self.log_event(
            event_type="impression",
            user_id=user_id,
            creator_id=creator_id,
            thread_id=thread_id,
            query=query,
            candidate_title=_clean((best_candidate or {}).get("title") or ""),
            candidate_url=_clean((best_candidate or {}).get("url") or ""),
            metadata=metadata,
        )


recommendation_feedback_service = RecommendationFeedbackService()

