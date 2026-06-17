"""
Canonical public fact cache for creator-world and live-world answers.

This lets us reuse recent verified facts with source provenance instead of
searching the web from scratch for every factual follow-up.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from backend.services.creator_fact_policy import classify_creator_fact_query

try:
    from backend.db import db
except Exception:  # pragma: no cover - lightweight test environments may not ship psycopg
    db = type(
        "_NullDB",
        (),
        {
            "execute_update": staticmethod(lambda *args, **kwargs: None),
            "execute_query": staticmethod(lambda *args, **kwargs: []),
            "execute_one": staticmethod(lambda *args, **kwargs: None),
        },
    )()


logger = logging.getLogger(__name__)


_FACT_SCHEMA_READY = False


@dataclass
class CachedFact:
    creator_id: str
    entity_subject: str
    entity_type: str
    fact_field: str
    fact_value: str
    source_url: str = ""
    source_domain: str = ""
    source_title: str = ""
    source_snippet: str = ""
    confidence: float = 0.8
    freshness: str = "low"
    verified_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "creator_id": self.creator_id,
            "entity_subject": self.entity_subject,
            "entity_type": self.entity_type,
            "fact_field": self.fact_field,
            "fact_value": self.fact_value,
            "source_url": self.source_url,
            "source_domain": self.source_domain,
            "source_title": self.source_title,
            "source_snippet": self.source_snippet,
            "confidence": self.confidence,
            "freshness": self.freshness,
            "verified_at": self.verified_at,
            "metadata": self.metadata or {},
        }


def _ensure_fact_registry_schema() -> bool:
    global _FACT_SCHEMA_READY
    if _FACT_SCHEMA_READY:
        return True
    try:
        db.execute_update(
            """
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
            )
            """
        )
        db.execute_update(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fact_registry_unique
            ON fact_registry (creator_id, entity_subject, fact_field)
            """
        )
        db.execute_update(
            """
            CREATE INDEX IF NOT EXISTS idx_fact_registry_recent
            ON fact_registry (creator_id, verified_at DESC)
            """
        )
        _FACT_SCHEMA_READY = True
        return True
    except Exception as exc:
        logger.warning("Fact registry schema bootstrap failed: %s", exc)
        return False


def _clean_value(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _domain(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


class FactRegistryService:
    _freshness_windows = {
        "none": timedelta(days=3650),
        "low": timedelta(days=180),
        "medium": timedelta(days=30),
        "high": timedelta(days=3),
    }

    def infer_fact_field(self, question: str, entity_type: str = "") -> str:
        lowered = _clean_value(question).lower()
        policy = classify_creator_fact_query(question, entity_type=entity_type)
        if policy.fact_field and policy.fact_field != "public_fact":
            if policy.kind == "stats":
                if any(token in lowered for token in ("revenue", "sales", "arr")):
                    return "revenue"
                if any(token in lowered for token in ("profit", "profits", "earnings")):
                    return "earnings"
                if "income" in lowered or re.search(r"\b(?:you|u|he|she|they).{0,25}\b(?:made|earned)\b|\b(?:most|highest|biggest).{0,45}\b(?:made|earned)\b", lowered):
                    return "income"
                if any(token in lowered for token in ("net worth", "worth")):
                    return "net_worth"
                if any(token in lowered for token in ("valuation", "valued at")):
                    return "valuation"
                if any(token in lowered for token in ("subscribers", "subscriber")):
                    return "subscribers"
                if any(token in lowered for token in ("students", "student")):
                    return "students"
                if any(token in lowered for token in ("members", "member")):
                    return "members"
                return "followers"
            return policy.fact_field
        if any(token in lowered for token in ("published", "publication", "release date", "released", "come out", "launch date", "write", "wrote", "written")):
            return "publication_date" if entity_type == "book" else "launch_date"
        if any(token in lowered for token in ("price", "pricing", "cost", "how much")):
            return "price"
        if any(token in lowered for token in ("followers", "subscribers", "members", "students")):
            return "followers"
        if any(token in lowered for token in ("revenue", "sales", "arr")):
            return "revenue"
        if any(token in lowered for token in ("profit", "profits", "earnings")):
            return "earnings"
        if "income" in lowered or re.search(r"\b(?:you|u|he|she|they).{0,25}\b(?:made|earned)\b|\b(?:most|highest|biggest).{0,45}\b(?:made|earned)\b", lowered):
            return "income"
        if any(token in lowered for token in ("latest episode", "newest episode", "recent episode")):
            return "latest_episode"
        if any(token in lowered for token in ("where can i buy", "where do i buy", "website", "official site")):
            return "official_url"
        if any(token in lowered for token in ("valuation", "valued at")):
            return "valuation"
        if any(token in lowered for token in ("net worth", "worth")):
            return "net_worth"
        return "public_fact"

    def lookup_fact(
        self,
        creator_id: int,
        entity_subject: str,
        fact_field: str,
        freshness_required: str = "low",
    ) -> Optional[CachedFact]:
        try:
            if not _ensure_fact_registry_schema():
                return None
            window = self._freshness_windows.get(str(freshness_required or "low").lower(), self._freshness_windows["low"])
            cutoff = datetime.now(timezone.utc) - window
            row = db.execute_one(
                """
                SELECT *
                FROM fact_registry
                WHERE creator_id = %s
                  AND lower(entity_subject) = lower(%s)
                  AND fact_field = %s
                  AND verified_at >= %s
                ORDER BY verified_at DESC
                LIMIT 1
                """,
                (str(creator_id), _clean_value(entity_subject), fact_field, cutoff),
            )
            if not row:
                return None
            return CachedFact(
                creator_id=str(row.get("creator_id") or ""),
                entity_subject=str(row.get("entity_subject") or ""),
                entity_type=str(row.get("entity_type") or ""),
                fact_field=str(row.get("fact_field") or ""),
                fact_value=str(row.get("fact_value") or ""),
                source_url=str(row.get("source_url") or ""),
                source_domain=str(row.get("source_domain") or ""),
                source_title=str(row.get("source_title") or ""),
                source_snippet=str(row.get("source_snippet") or ""),
                confidence=float(row.get("confidence") or 0.8),
                freshness=str(row.get("freshness") or "low"),
                verified_at=str(row.get("verified_at") or ""),
                metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
            )
        except Exception as exc:
            logger.warning("Fact registry lookup failed: %s", exc)
            return None

    def upsert_fact(
        self,
        creator_id: int,
        entity_subject: str,
        entity_type: str,
        fact_field: str,
        fact_value: str,
        *,
        source_url: str = "",
        source_title: str = "",
        source_snippet: str = "",
        confidence: float = 0.85,
        freshness: str = "low",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            if not _ensure_fact_registry_schema():
                return
            db.execute_update(
                """
                INSERT INTO fact_registry (
                    creator_id,
                    entity_subject,
                    entity_type,
                    fact_field,
                    fact_value,
                    source_url,
                    source_domain,
                    source_title,
                    source_snippet,
                    confidence,
                    freshness,
                    verified_at,
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s::jsonb)
                ON CONFLICT (creator_id, entity_subject, fact_field)
                DO UPDATE SET
                    entity_type = EXCLUDED.entity_type,
                    fact_value = EXCLUDED.fact_value,
                    source_url = EXCLUDED.source_url,
                    source_domain = EXCLUDED.source_domain,
                    source_title = EXCLUDED.source_title,
                    source_snippet = EXCLUDED.source_snippet,
                    confidence = EXCLUDED.confidence,
                    freshness = EXCLUDED.freshness,
                    verified_at = NOW(),
                    metadata = EXCLUDED.metadata
                """,
                (
                    str(creator_id),
                    _clean_value(entity_subject),
                    _clean_value(entity_type),
                    _clean_value(fact_field),
                    _clean_value(fact_value),
                    _clean_value(source_url),
                    _domain(source_url),
                    _clean_value(source_title),
                    _clean_value(source_snippet),
                    float(confidence or 0.0),
                    _clean_value(freshness or "low"),
                    json.dumps(metadata or {}),
                ),
            )
        except Exception as exc:
            logger.warning("Fact registry upsert failed: %s", exc)

    def list_facts(self, creator_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            if not _ensure_fact_registry_schema():
                return []
            rows = db.execute_query(
                """
                SELECT *
                FROM fact_registry
                WHERE creator_id = %s
                ORDER BY verified_at DESC
                LIMIT %s
                """,
                (str(creator_id), int(limit)),
            )
            return list(rows or [])
        except Exception as exc:
            logger.warning("Fact registry list failed: %s", exc)
            return []


fact_registry = FactRegistryService()
