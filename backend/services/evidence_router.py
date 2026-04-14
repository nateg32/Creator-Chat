"""
Central evidence planning for Creator Bot.

Instead of asking only "should I search?", this router decides which world
should answer:

1. creator_memory - what the creator has already said in content
2. creator_world  - public facts about the creator and their owned entities
3. live_world     - fresh/current public facts that need verification

The router is cheap, deterministic, and designed to run before generation on
every turn.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
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
from backend.services.creator_entity_service import creator_entity_service
from backend.services.decision_service import decision_service


logger = logging.getLogger(__name__)


_EVIDENCE_SCHEMA_READY = False


def _is_user_relationship_business_question(query: str) -> bool:
    detector = getattr(decision_service, "is_user_relationship_business_question", None)
    return bool(detector(query)) if callable(detector) else False


_FOLLOWUP_REFERENTS = {"it", "that", "this", "one", "book", "course", "program", "podcast", "episode"}
_FACTUAL_PATTERNS = [
    re.compile(r"\bwhen (?:did|was|were|is)\b", re.IGNORECASE),
    re.compile(r"\bwhat (?:year|date|month|day)\b", re.IGNORECASE),
    re.compile(r"\bhow (?:many|much)\b", re.IGNORECASE),
    re.compile(r"\b(?:full|real|legal)\s+name\b", re.IGNORECASE),
    re.compile(r"\bwhat(?:'s|\s+is)?\s+(?:your|ur|u)\s+last\s+name\b", re.IGNORECASE),
    re.compile(r"\bprice\b|\bcost\b|\bpricing\b", re.IGNORECASE),
    re.compile(r"\bpublish(?:ed|ing)?\b|\bpublication\b|\brelease(?:d)?\b|\blaunch(?:ed)?\b", re.IGNORECASE),
    re.compile(r"\bfollowers?\b|\bsubscribers?\b|\bmembers?\b|\bstudents?\b|\branking\b|\branked\b|\bvaluation\b", re.IGNORECASE),
]
_TIMELINE_PATTERNS = [
    re.compile(r"\bwhen (?:did|was|were|is)\b", re.IGNORECASE),
    re.compile(r"\bwhat (?:year|date|month|day)\b", re.IGNORECASE),
    re.compile(r"\bpublish(?:ed|ing)?\b|\bpublication\b|\brelease(?:d)?\b|\blaunch(?:ed)?\b|\bcome out\b", re.IGNORECASE),
    re.compile(r"\bwhen\s+(?:did|do)\s+(?:you|u|he|she|they)\s+(?:start|begin|began|get into|got into|trade|day\s*trad(?:e|ing)|invest(?:ing)?|build|built|launch|launched|create|created)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+long\s+(?:have|has)\s+(?:you|u|he|she|they)\s+been\b", re.IGNORECASE),
]
_PRICE_PATTERNS = [
    re.compile(r"\bprice\b|\bcost\b|\bpricing\b", re.IGNORECASE),
    re.compile(r"\bhow much\b", re.IGNORECASE),
]
_STAT_PATTERNS = [
    re.compile(r"\bfollowers?\b|\bsubscribers?\b|\bmembers?\b|\bstudents?\b|\bemployees?\b|\branking\b|\branked\b|\bvaluation\b|\bnet worth\b", re.IGNORECASE),
]
_LIVE_PATTERNS = [
    re.compile(r"\b(latest|newest|recent|current|today|right now|currently|still)\b", re.IGNORECASE),
    re.compile(r"\bfollowers?\b|\bsubscribers?\b|\bprice\b|\bpricing\b|\branking\b|\branked\b", re.IGNORECASE),
]
_RESOURCE_PATTERNS = [
    re.compile(r"\bwhere can i (?:buy|get|find|purchase)\b", re.IGNORECASE),
    re.compile(r"\bwatch\b|\blink\b|\bvideo\b|\bepisode\b|\bresource\b", re.IGNORECASE),
]
_CONFIRMATION_PATTERNS = [
    re.compile(r"\bdo you know\b", re.IGNORECASE),
    re.compile(r"\bhave you heard of\b", re.IGNORECASE),
    re.compile(r"\bare you familiar with\b", re.IGNORECASE),
    re.compile(r"\bdo you have\b", re.IGNORECASE),
    re.compile(r"\bdid you write\b", re.IGNORECASE),
    re.compile(r"\bis there (?:a|an|any)\b", re.IGNORECASE),
    re.compile(r"\b(?:is|was)\s+.+\s+(?:your|my)\s+(?:book|course|program|podcast|show|company|business)\b", re.IGNORECASE),
]
_OVERVIEW_PATTERNS = [
    re.compile(r"\btell me about\b", re.IGNORECASE),
    re.compile(r"\bwhat is\b", re.IGNORECASE),
    re.compile(r"\bdo you know about\b", re.IGNORECASE),
]
_CATALOG_PATTERNS = [
    re.compile(r"\bhave (?:you|u) (?:written|published)\s+any\s+books\b", re.IGNORECASE),
    re.compile(r"\bhow many\s+books\s+(?:have\s+)?(?:you|u)\s+(?:written|published)\b", re.IGNORECASE),
    re.compile(r"\bhow many\s+(?:books|courses|programs|podcasts|shows)\b", re.IGNORECASE),
    re.compile(r"\b(?:books|courses|programs|podcasts|shows)\s+(?:have\s+)?(?:you|u)\s+(?:written|published|made|created)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+books\b", re.IGNORECASE),
    re.compile(r"\bwhich\s+books\b", re.IGNORECASE),
    re.compile(r"\bany\s+books\b", re.IGNORECASE),
    re.compile(r"\ball\s+books\b", re.IGNORECASE),
    re.compile(r"\bhave (?:you|u)\s+written\s+any\b", re.IGNORECASE),
    re.compile(r"\b(?:courses|programs|podcasts|shows)\b", re.IGNORECASE),
]
_ADVICE_PATTERNS = [
    re.compile(r"\bwhat (?:would|should) (?:you|u) rec(?:o|c)o?m+e?n?d\b", re.IGNORECASE),
    re.compile(r"\bwhat do you rec(?:o|c)o?m+e?n?d\b", re.IGNORECASE),
    re.compile(r"\bwhat should i do\b", re.IGNORECASE),
    re.compile(r"\bhow do i\b", re.IGNORECASE),
    re.compile(r"\bhow can i\b", re.IGNORECASE),
    re.compile(r"\bany advice\b", re.IGNORECASE),
]
_CREATOR_WORLD_HINTS = [
    re.compile(r"\b(your|my)\s+(book|course|program|podcast|show|newsletter|website|company|business)\b", re.IGNORECASE),
    re.compile(r"\b(your|my)\s+(?:full|real|legal|last)\s+name\b", re.IGNORECASE),
    re.compile(r"\bnet worth\b|\bemployees\b|\bfounded\b|\bvaluation\b|\bfollowers?\b|\bsubscribers?\b", re.IGNORECASE),
]


@dataclass
class EvidencePlan:
    primary_world: str
    secondary_worlds: List[str]
    should_search_web: bool
    should_search_corpus: bool
    should_verify: bool
    user_is_followup: bool
    resolved_query: str
    entity_subject: str
    freshness_required: str
    answer_mode: str
    risk_flags: List[str]
    query_goal: str = "general"
    search_strategy: str = "memory_first"
    entity_type: str = ""
    top_score: Optional[float] = None
    contradiction_risk: bool = False
    plan_version: str = "evidence_router_v1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _ensure_evidence_schema() -> bool:
    global _EVIDENCE_SCHEMA_READY
    if _EVIDENCE_SCHEMA_READY:
        return True
    try:
        db.execute_update(
            """
            CREATE TABLE IF NOT EXISTS evidence_plan_log (
                id SERIAL PRIMARY KEY,
                creator_id TEXT,
                query TEXT,
                resolved_query TEXT,
                primary_world TEXT,
                secondary_worlds JSONB DEFAULT '[]'::jsonb,
                query_goal TEXT,
                search_strategy TEXT,
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
            )
            """
        )
        db.execute_update(
            "ALTER TABLE evidence_plan_log ADD COLUMN IF NOT EXISTS query_goal TEXT"
        )
        db.execute_update(
            "ALTER TABLE evidence_plan_log ADD COLUMN IF NOT EXISTS search_strategy TEXT"
        )
        db.execute_update(
            """
            CREATE INDEX IF NOT EXISTS idx_evidence_plan_log_creator
            ON evidence_plan_log (creator_id, created_at DESC)
            """
        )
        _EVIDENCE_SCHEMA_READY = True
        return True
    except Exception as exc:
        logger.warning("Evidence plan schema bootstrap failed: %s", exc)
        return False


def log_evidence_plan(
    creator_id: int,
    query: str,
    plan: EvidencePlan,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        if not _ensure_evidence_schema():
            return
        db.execute_update(
            """
            INSERT INTO evidence_plan_log (
                creator_id,
                query,
                resolved_query,
                primary_world,
                secondary_worlds,
                query_goal,
                search_strategy,
                answer_mode,
                should_search_web,
                should_search_corpus,
                should_verify,
                freshness_required,
                entity_subject,
                entity_type,
                risk_flags,
                contradiction_risk,
                confidence_score,
                metadata,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, NOW())
            """,
            (
                str(creator_id),
                str(query or ""),
                str(plan.resolved_query or ""),
                str(plan.primary_world or ""),
                json.dumps(plan.secondary_worlds or []),
                str(plan.query_goal or ""),
                str(plan.search_strategy or ""),
                str(plan.answer_mode or ""),
                bool(plan.should_search_web),
                bool(plan.should_search_corpus),
                bool(plan.should_verify),
                str(plan.freshness_required or "none"),
                str(plan.entity_subject or ""),
                str(plan.entity_type or ""),
                json.dumps(plan.risk_flags or []),
                bool(plan.contradiction_risk),
                float(plan.top_score or 0.0),
                json.dumps(metadata or {}),
            ),
        )
    except Exception as exc:
        logger.warning("Evidence plan log write failed: %s", exc)


def recent_evidence_activity(creator_id: int, limit: int = 40) -> List[Dict[str, Any]]:
    try:
        if not _ensure_evidence_schema():
            return []
        rows = db.execute_query(
            """
            SELECT *
            FROM evidence_plan_log
            WHERE creator_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (str(creator_id), int(limit)),
        )
        return list(rows or [])
    except Exception as exc:
        logger.warning("Evidence plan log read failed: %s", exc)
        return []


def _extract_date_markers(text: str) -> List[str]:
    markers = set()
    for match in re.findall(r"\b(?:19|20)\d{2}\b", text or ""):
        markers.add(match)
    for match in re.findall(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
        text or "",
        flags=re.IGNORECASE,
    ):
        markers.add(re.sub(r"\s+", " ", match).strip())
    return sorted(markers)


def _extract_price_markers(text: str) -> List[str]:
    markers = set(re.findall(r"\$\s?\d[\d,]*(?:\.\d{2})?", text or ""))
    markers.update(re.findall(r"\b\d+(?:\.\d+)?\s?(?:usd|aud|cad|eur)\b", text or "", flags=re.IGNORECASE))
    return sorted(markers)


def _extract_count_markers(text: str) -> List[str]:
    markers = set(re.findall(r"\b\d[\d,]*(?:\.\d+)?(?:\s?[kKmM])?\b", text or ""))
    return sorted(marker for marker in markers if len(marker) <= 12)


def detect_evidence_contradiction(
    query: str,
    corpus_chunks: Optional[List[Dict[str, Any]]] = None,
    web_results: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    corpus_text = " ".join(str(chunk.get("content") or chunk.get("text") or "") for chunk in (corpus_chunks or []))
    web_text = " ".join(str(item.get("snippet") or item.get("text") or item.get("title") or "") for item in (web_results or []))
    if not corpus_text or not web_text:
        return {"has_contradiction": False, "kind": "none", "corpus_markers": [], "web_markers": []}

    lowered = str(query or "").lower()
    if any(token in lowered for token in ("published", "publication", "release", "launch", "when did", "what year", "what date", "which month")):
        corpus_markers = _extract_date_markers(corpus_text)
        web_markers = _extract_date_markers(web_text)
        if corpus_markers and web_markers and set(corpus_markers).isdisjoint(set(web_markers)):
            return {
                "has_contradiction": True,
                "kind": "date",
                "corpus_markers": corpus_markers,
                "web_markers": web_markers,
            }

    if any(token in lowered for token in ("price", "cost", "pricing", "how much")):
        corpus_markers = _extract_price_markers(corpus_text)
        web_markers = _extract_price_markers(web_text)
        if corpus_markers and web_markers and set(corpus_markers).isdisjoint(set(web_markers)):
            return {
                "has_contradiction": True,
                "kind": "price",
                "corpus_markers": corpus_markers,
                "web_markers": web_markers,
            }

    if any(token in lowered for token in ("followers", "subscribers", "members", "students", "employees", "ranking", "ranked")):
        corpus_markers = _extract_count_markers(corpus_text)
        web_markers = _extract_count_markers(web_text)
        if corpus_markers and web_markers and set(corpus_markers).isdisjoint(set(web_markers)):
            return {
                "has_contradiction": True,
                "kind": "count",
                "corpus_markers": corpus_markers,
                "web_markers": web_markers,
            }

    # Catalog count contradiction: RAG says N items, web says M items (e.g. "1 book" vs "2 books")
    if any(token in lowered for token in ("how many", "books", "courses", "programs", "written", "published", "authored")):
        corpus_markers = _extract_count_markers(corpus_text)
        web_markers = _extract_count_markers(web_text)
        if corpus_markers and web_markers and set(corpus_markers).isdisjoint(set(web_markers)):
            return {
                "has_contradiction": True,
                "kind": "catalog_count",
                "corpus_markers": corpus_markers,
                "web_markers": web_markers,
            }

    return {"has_contradiction": False, "kind": "none", "corpus_markers": [], "web_markers": []}


class EvidenceRouter:
    def __init__(self, creator: Dict[str, Any]):
        self.creator = dict(creator or {})
        self.creator_name = str(self.creator.get("name") or self.creator.get("handle") or "the creator").strip()

    def _infer_entity_type(self, query: str, entity: Optional[Dict[str, Any]]) -> str:
        if entity and entity.get("type"):
            return str(entity.get("type") or "").strip()
        lowered = str(query or "").lower()
        if any(token in lowered for token in ("books", "book", "author", "published", "publication", "audible", "amazon", "goodreads")):
            return "book"
        if any(token in lowered for token in ("courses", "course", "programs", "program", "coaching", "membership")):
            return "course"
        if any(token in lowered for token in ("podcasts", "podcast", "episodes", "episode", "show", "newsletter", "channel")):
            return "podcast"
        if any(token in lowered for token in ("companies", "company", "business", "businesses")):
            return "company"
        return ""

    def _resolve_query(self, query: str, conversation_history: Optional[List[Dict[str, str]]]) -> str:
        return decision_service.resolve_followup_question(query, conversation_history)

    def _risk_flags(self, query: str, entity: Optional[Dict[str, Any]]) -> List[str]:
        lowered = str(query or "").lower()
        policy = classify_creator_fact_query(query, entity_type=str((entity or {}).get("type") or ""))
        flags: List[str] = []
        if entity:
            flags.append("entity_resolved")
            if entity.get("creator_owned"):
                flags.append("creator_owned_entity")
            if entity.get("name") and entity.get("name", "").lower() in lowered:
                flags.append("title_match")
        if any(pattern.search(lowered) for pattern in _FACTUAL_PATTERNS) or policy.requires_web:
            flags.append("public_fact")
        if policy.kind in {"publication_timeline", "creator_start_timeline"} or any(token in lowered for token in ("published", "publication", "release", "launch", "when did", "what year", "what date", "which month", "start", "started", "begin", "began", "got into", "get into", "how long have you been")):
            flags.append("date")
        if policy.kind in {"price", "availability"} or any(token in lowered for token in ("price", "pricing", "cost", "how much", "buy", "purchase")):
            flags.append("pricing")
        if policy.kind == "stats" or any(token in lowered for token in ("followers", "subscribers", "members", "students", "ranking", "ranked", "valuation")):
            flags.append("stats")
        if any(token in lowered for token in ("latest", "newest", "recent", "current", "today", "right now", "currently", "still")):
            flags.append("fresh")
        if any(pattern.search(lowered) for pattern in _RESOURCE_PATTERNS):
            flags.append("resource_request")
        deduped: List[str] = []
        seen = set()
        for flag in flags:
            if flag not in seen:
                deduped.append(flag)
                seen.add(flag)
        return deduped

    def _freshness(self, query: str, risk_flags: List[str]) -> str:
        lowered = str(query or "").lower()
        if "fresh" in risk_flags or any(pattern.search(lowered) for pattern in _LIVE_PATTERNS):
            return "high"
        if any(flag in risk_flags for flag in ("pricing", "stats", "date")):
            return "medium"
        if "public_fact" in risk_flags:
            return "low"
        return "none"

    def _answer_mode(self, query: str, risk_flags: List[str], entity: Optional[Dict[str, Any]]) -> str:
        lowered = str(query or "").lower()
        policy = classify_creator_fact_query(query, entity_type=str((entity or {}).get("type") or ""))
        if "resource_request" in risk_flags:
            return "resource_recommendation"
        if any(flag in risk_flags for flag in ("date", "pricing", "stats")):
            return "direct_fact"
        if policy.kind == "identity":
            return "direct_fact"
        if _is_user_relationship_business_question(query):
            return "creator_take"
        if entity and entity.get("creator_owned"):
            return "hybrid"
        if any(pattern.search(lowered) for pattern in _ADVICE_PATTERNS) or any(token in lowered for token in ("advice", "how do you", "what's your best", "what do you think", "your take")):
            return "creator_take"
        return "hybrid"

    def _query_goal(self, query: str, entity: Optional[Dict[str, Any]], risk_flags: List[str]) -> str:
        lowered = str(query or "").lower()
        policy = classify_creator_fact_query(query, entity_type=str((entity or {}).get("type") or ""))
        if policy.kind == "catalog":
            return "entity_catalog_lookup"
        if policy.kind == "identity":
            return "identity_lookup"
        if policy.kind in {"publication_timeline", "creator_start_timeline"}:
            return "timeline_lookup"
        if policy.kind == "creator_journey":
            return "journey_lookup"
        if policy.kind == "price":
            return "price_lookup"
        if policy.kind == "stats":
            return "current_stat_lookup" if any(pattern.search(lowered) for pattern in _LIVE_PATTERNS) else "stat_lookup"
        if policy.kind == "availability":
            return "availability_lookup"
        if any(pattern.search(lowered) for pattern in _CATALOG_PATTERNS) and any(
            token in lowered for token in ("books", "courses", "programs", "podcasts", "shows", "written", "published")
        ):
            return "entity_catalog_lookup"
        if re.search(r"\bwhere can i (?:buy|get|find|purchase)\b", lowered, re.IGNORECASE):
            return "availability_lookup"
        if any(pattern.search(lowered) for pattern in _RESOURCE_PATTERNS):
            return "resource_lookup"
        if any(pattern.search(lowered) for pattern in _TIMELINE_PATTERNS):
            return "timeline_lookup"
        if any(pattern.search(lowered) for pattern in _PRICE_PATTERNS):
            return "price_lookup"
        if any(pattern.search(lowered) for pattern in _STAT_PATTERNS):
            return "current_stat_lookup" if any(pattern.search(lowered) for pattern in _LIVE_PATTERNS) else "stat_lookup"
        if entity and any(pattern.search(lowered) for pattern in _CONFIRMATION_PATTERNS):
            return "entity_confirmation"
        if entity and any(pattern.search(lowered) for pattern in _OVERVIEW_PATTERNS):
            return "entity_overview"
        if _is_user_relationship_business_question(query):
            return "creator_take"
        if any(pattern.search(lowered) for pattern in _ADVICE_PATTERNS) or any(token in lowered for token in ("advice", "how do you", "what do you think", "your take", "best advice")):
            return "creator_take"
        if entity and "creator_owned_entity" in risk_flags:
            return "entity_overview"
        return "general"

    def _classify_worlds(
        self,
        query: str,
        risk_flags: List[str],
        entity: Optional[Dict[str, Any]],
        answer_mode: str,
        top_score: Optional[float],
        query_goal: str,
    ) -> tuple[str, List[str], bool, bool, bool, str]:
        lowered = str(query or "").lower()
        if _is_user_relationship_business_question(query):
            return "creator_memory", [], False, True, False, "memory_first"
        creator_world_signal = bool(entity and entity.get("creator_owned")) or any(
            pattern.search(lowered) for pattern in _CREATOR_WORLD_HINTS
        )
        live_world_signal = "fresh" in risk_flags or any(pattern.search(lowered) for pattern in _LIVE_PATTERNS)
        factual_signal = any(flag in risk_flags for flag in ("public_fact", "date", "pricing", "stats"))

        if query_goal == "entity_confirmation":
            return "creator_memory", ["creator_world"], False, True, False, "entity_graph_first"

        if query_goal == "entity_overview":
            return "creator_memory", ["creator_world"], False, True, False, "memory_plus_entity_graph"

        if query_goal == "entity_catalog_lookup":
            return "creator_world", [], True, False, True, "entity_catalog_plus_web"

        if query_goal == "identity_lookup":
            return "creator_world", [], True, False, True, "official_grounded_search"

        if query_goal == "availability_lookup":
            has_official_urls = bool((entity or {}).get("official_urls"))
            return (
                "creator_world",
                [],
                not has_official_urls,
                False,
                not has_official_urls,
                "official_urls_first" if has_official_urls else "official_grounded_search",
            )

        if query_goal == "journey_lookup":
            return "creator_world", [], True, False, False, "journey_grounded_search"

        if query_goal == "resource_lookup":
            return "creator_world", ["creator_memory"], True, True, False, "resource_web_plus_corpus"

        if live_world_signal:
            primary_world = "live_world"
            secondary = ["creator_world"]
            should_search_web = True
            should_search_corpus = False
            should_verify = True
            search_strategy = "live_grounded_search"
        elif creator_world_signal or factual_signal:
            primary_world = "creator_world"
            secondary = []
            should_search_web = True
            should_search_corpus = False
            should_verify = True
            search_strategy = "official_grounded_search"
        else:
            primary_world = "creator_memory"
            secondary = []
            should_search_web = False
            should_search_corpus = True
            should_verify = False
            search_strategy = "memory_first"

        if primary_world == "creator_memory" and top_score is not None and top_score < 0.65:
            should_search_web = True
            should_verify = False
            secondary = ["live_world"]
            search_strategy = "memory_then_live_fallback"

        if primary_world == "creator_memory" and top_score is not None and top_score >= 0.80 and answer_mode == "creator_take":
            should_search_web = False
            search_strategy = "memory_only"

        deduped_secondary: List[str] = []
        seen = set()
        for world in secondary:
            if world != primary_world and world not in seen:
                deduped_secondary.append(world)
                seen.add(world)
        return primary_world, deduped_secondary, should_search_web, should_search_corpus, should_verify, search_strategy

    def build_plan(
        self,
        query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        top_score: Optional[float] = None,
        retrieved_chunks: Optional[List[Dict[str, Any]]] = None,
        web_results: Optional[List[Dict[str, Any]]] = None,
    ) -> EvidencePlan:
        resolved_query = self._resolve_query(query, conversation_history)
        entity = creator_entity_service.resolve_entity(
            resolved_query,
            creator_id=self.creator.get("id"),
            creator_profile=self.creator,
            conversation_history=conversation_history,
        )
        inferred_entity_type = self._infer_entity_type(resolved_query, entity)
        risk_flags = self._risk_flags(resolved_query, entity)
        freshness_required = self._freshness(resolved_query, risk_flags)
        answer_mode = self._answer_mode(resolved_query, risk_flags, entity)
        query_goal = self._query_goal(resolved_query, entity, risk_flags)
        primary_world, secondary_worlds, should_search_web, should_search_corpus, should_verify, search_strategy = self._classify_worlds(
            resolved_query,
            risk_flags,
            entity,
            answer_mode,
            top_score,
            query_goal,
        )
        contradiction = detect_evidence_contradiction(
            resolved_query,
            corpus_chunks=retrieved_chunks,
            web_results=web_results,
        )
        user_is_followup = (
            resolved_query != query
            or bool(set(re.findall(r"[a-z0-9']+", str(query or "").lower())) & _FOLLOWUP_REFERENTS)
        )

        return EvidencePlan(
            primary_world=primary_world,
            secondary_worlds=secondary_worlds,
            should_search_web=should_search_web,
            should_search_corpus=should_search_corpus,
            should_verify=should_verify,
            user_is_followup=user_is_followup,
            resolved_query=resolved_query,
            entity_subject=str((entity or {}).get("name") or ""),
            freshness_required=freshness_required,
            answer_mode=answer_mode,
            risk_flags=risk_flags,
            query_goal=query_goal,
            search_strategy=search_strategy,
            entity_type=inferred_entity_type,
            top_score=top_score,
            contradiction_risk=bool(contradiction.get("has_contradiction")),
        )


evidence_router = EvidenceRouter
