"""
Decides whether a query needs live web search before or after RAG retrieval.

This is the centralized trigger point for factual creator-public questions and
low-confidence retrieval fallback. It is intentionally lightweight so it can
run on every turn without adding model latency.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from backend.services.evidence_router import EvidenceRouter


logger = logging.getLogger(__name__)


FACTUAL_QUERY_PATTERNS = [
    r"\bwhen (did|was|were|is)\b",
    r"\bwhat (year|date|month|day)\b",
    r"\bhow (much|many)\b",
    r"\bwhat (is|was) the price\b",
    r"\bwhere can i (buy|get|find|purchase)\b",
    r"\bwhat (is|are) the (latest|newest|recent|current)\b",
    r"\bis .+ still\b",
    r"\bdid .+ (release|launch|publish|come out)\b",
    r"\bwhich (month|year|episode|season)\b",
    r"\bhow (long|old|tall|big)\b",
    r"\b(rank|ranking|ranked|followers|subscriber|subscribers|members|students|revenue|valuation)\b",
]

CREATOR_OWN_WORLD_PATTERNS = [
    r"\b(your|his|her|their) book\b",
    r"\b(your|his|her|their) (course|program|coaching|membership)\b",
    r"\b(your|his|her|their) (podcast|show|channel|newsletter)\b",
    r"\b(your|his|her|their) (website|instagram|twitter|youtube|linkedin)\b",
    r"\bhow many (followers|subscribers|students|members)\b",
    r"\bhow many (books|courses|programs|podcasts|shows|companies|businesses|products)\b",
    r"\bnet worth\b",
    r"\bwhen (did you|did he|did she) (start|launch|found|create|write|publish)\b",
    r"\bwhere (are you|is he|is she) (based|from|located)\b",
    r"\bwhat (companies|businesses) (do you|does he|does she) own\b",
    r"\bwhat books\b",
    r"\bwhich books\b",
    r"\bhave you (written|published|authored)\b",
    r"\b(books|courses|programs)\s+(have\s+)?(you|he|she)\s+(written|published|made|created)\b",
    r"\binvested in\b",
    r"\bvaluation\b",
    r"\bfounded\b",
    r"\bco.?founder\b",
]

_FACTUAL_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in FACTUAL_QUERY_PATTERNS]
_CREATOR_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in CREATOR_OWN_WORLD_PATTERNS]


@dataclass
class SearchDecision:
    should_search: bool
    reason: str
    reason_detail: str
    phase: str
    confidence: float = 1.0


class SearchDecisionEngine:
    RAG_CONFIDENCE_THRESHOLD = 0.65
    RAG_HIGH_CONFIDENCE_THRESHOLD = 0.80

    def __init__(self, creator: dict):
        self.creator = creator or {}
        self.creator_name = str(self.creator.get("name") or "").lower().strip()
        self.creator_terms = self._extract_creator_terms(self.creator)
        self.router = EvidenceRouter(self.creator)

    def _extract_creator_terms(self, creator: dict) -> list[str]:
        terms: list[str] = []
        name = str(creator.get("name") or "").strip()
        if name:
            terms.append(name.lower())
            first = name.split()[0].strip().lower()
            if first:
                terms.append(first)

        for field_name in ("identity_fingerprint", "soul_md", "research_summary"):
            raw = creator.get(field_name) or ""
            if isinstance(raw, dict):
                raw = json.dumps(raw)
            text = str(raw or "")
            quoted = re.findall(r'"([^"]{3,80})"', text)
            terms.extend(q.lower().strip() for q in quoted if q.strip())

        cleaned_terms = []
        seen = set()
        for term in terms:
            normalized = re.sub(r"\s+", " ", term).strip().lower()
            if normalized and normalized not in seen:
                cleaned_terms.append(normalized)
                seen.add(normalized)
        return cleaned_terms

    def _looks_like_entity_confirmation(self, query: str) -> bool:
        query_lower = str(query or "").lower().strip()
        if not query_lower:
            return False
        has_entity_term = any(term in query_lower for term in self.creator_terms if len(term) >= 4)
        if not has_entity_term:
            return False
        if re.search(r"\bis\s+.+\b(your|yours)\b", query_lower) and any(
            token in query_lower for token in ("book", "course", "program", "podcast", "show", "newsletter", "video")
        ):
            return True
        if re.search(r"\bdid you (?:write|make|create|start)\b", query_lower) and has_entity_term:
            return True
        return False

    def pre_retrieval_decision(
        self,
        query: str,
        conversation_history: Optional[list] = None,
    ) -> SearchDecision:
        query_lower = str(query or "").lower().strip()
        if not query_lower:
            return SearchDecision(
                should_search=False,
                reason="empty_query",
                reason_detail="Empty query",
                phase="pre_retrieval",
                confidence=1.0,
            )

        try:
            plan = self.router.build_plan(query, conversation_history=conversation_history)
            if plan.query_goal in {"entity_confirmation", "entity_overview"} and plan.entity_subject:
                return SearchDecision(
                    should_search=False,
                    reason="entity_graph_answerable",
                    reason_detail=f"EvidencePlan query_goal={plan.query_goal} entity_subject={plan.entity_subject}",
                    phase="pre_retrieval",
                    confidence=0.96,
                )
            if plan.query_goal == "availability_lookup" and not plan.should_search_web and plan.entity_subject:
                return SearchDecision(
                    should_search=False,
                    reason="official_entity_url_available",
                    reason_detail=f"EvidencePlan availability can be answered from known official URLs for {plan.entity_subject}",
                    phase="pre_retrieval",
                    confidence=0.94,
                )
            if plan.should_search_web and plan.primary_world in {"creator_world", "live_world"}:
                reason = "creator_own_world" if plan.primary_world == "creator_world" else "factual_query"
                return SearchDecision(
                    should_search=True,
                    reason=reason,
                    reason_detail=f"EvidencePlan primary_world={plan.primary_world} answer_mode={plan.answer_mode} risk_flags={','.join(plan.risk_flags)}",
                    phase="pre_retrieval",
                    confidence=0.95 if plan.primary_world == "creator_world" else 0.9,
                )
        except Exception as exc:
            logger.warning("Evidence router pre-retrieval plan failed, falling back to heuristic search rules: %s", exc)

        if self._looks_like_entity_confirmation(query_lower):
            return SearchDecision(
                should_search=False,
                reason="entity_graph_answerable",
                reason_detail="Known creator-owned entity appears to be answerable without live search",
                phase="pre_retrieval",
                confidence=0.9,
            )

        for pattern in _CREATOR_PATTERNS:
            if pattern.search(query_lower):
                return SearchDecision(
                    should_search=True,
                    reason="creator_own_world",
                    reason_detail=f"Query asks about creator public facts: matched '{pattern.pattern}'",
                    phase="pre_retrieval",
                    confidence=0.95,
                )

        for pattern in _FACTUAL_PATTERNS:
            if pattern.search(query_lower):
                return SearchDecision(
                    should_search=True,
                    reason="factual_query",
                    reason_detail=f"Query asks for a verifiable fact: matched '{pattern.pattern}'",
                    phase="pre_retrieval",
                    confidence=0.85,
                )

        words = set(re.findall(r"[a-z0-9']+", query_lower))
        fact_words = {"when", "how", "where", "what", "which", "who", "did", "does", "is", "was"}
        if any(term in query_lower for term in self.creator_terms) and words.intersection(fact_words):
            return SearchDecision(
                should_search=True,
                reason="creator_named_fact",
                reason_detail="Query names the creator and seeks factual information",
                phase="pre_retrieval",
                confidence=0.75,
            )

        return SearchDecision(
            should_search=False,
            reason="no_pre_retrieval_signal",
            reason_detail="No creator-public or factual signal detected",
            phase="pre_retrieval",
            confidence=0.80,
        )

    def post_retrieval_decision(
        self,
        query: str,
        chunks: list,
        top_score: Optional[float],
        conversation_history: Optional[list] = None,
    ) -> SearchDecision:
        try:
            plan = self.router.build_plan(
                query,
                conversation_history=conversation_history,
                top_score=top_score,
                retrieved_chunks=chunks,
            )
            if plan.query_goal in {"entity_confirmation", "entity_overview"} and plan.entity_subject:
                return SearchDecision(
                    should_search=False,
                    reason="entity_graph_answerable",
                    reason_detail=f"EvidencePlan query_goal={plan.query_goal} entity_subject={plan.entity_subject}",
                    phase="post_retrieval",
                    confidence=0.96,
                )
            if plan.query_goal == "availability_lookup" and not plan.should_search_web and plan.entity_subject:
                return SearchDecision(
                    should_search=False,
                    reason="official_entity_url_available",
                    reason_detail=f"EvidencePlan availability can be answered from known official URLs for {plan.entity_subject}",
                    phase="post_retrieval",
                    confidence=0.94,
                )
            if plan.primary_world in {"creator_world", "live_world"} and plan.should_search_web:
                reason = "low_rag_confidence" if top_score is not None and top_score < self.RAG_CONFIDENCE_THRESHOLD else "medium_confidence_factual"
                if not chunks:
                    reason = "no_rag_results"
                return SearchDecision(
                    should_search=True,
                    reason=reason,
                    reason_detail=f"EvidencePlan primary_world={plan.primary_world} should_verify={plan.should_verify} freshness={plan.freshness_required}",
                    phase="post_retrieval",
                    confidence=0.92 if not chunks else 0.85,
                )
        except Exception as exc:
            logger.warning("Evidence router post-retrieval plan failed, falling back to heuristic search rules: %s", exc)

        if self._looks_like_entity_confirmation(query):
            return SearchDecision(
                should_search=False,
                reason="entity_graph_answerable",
                reason_detail="Known creator-owned entity appears to be answerable without live search",
                phase="post_retrieval",
                confidence=0.9,
            )

        if not chunks:
            return SearchDecision(
                should_search=True,
                reason="no_rag_results",
                reason_detail="RAG returned no chunks for this query",
                phase="post_retrieval",
                confidence=0.99,
            )

        if top_score is not None and top_score < self.RAG_CONFIDENCE_THRESHOLD:
            return SearchDecision(
                should_search=True,
                reason="low_rag_confidence",
                reason_detail=f"Top chunk score {top_score:.2f} below threshold {self.RAG_CONFIDENCE_THRESHOLD:.2f}",
                phase="post_retrieval",
                confidence=0.90,
            )

        if top_score is not None and top_score >= self.RAG_HIGH_CONFIDENCE_THRESHOLD:
            return SearchDecision(
                should_search=False,
                reason="high_rag_confidence",
                reason_detail=f"Top chunk score {top_score:.2f} is strong enough to trust RAG",
                phase="post_retrieval",
                confidence=0.85,
            )

        query_lower = str(query or "").lower()
        for pattern in _FACTUAL_PATTERNS:
            if pattern.search(query_lower):
                return SearchDecision(
                    should_search=True,
                    reason="medium_confidence_factual",
                    reason_detail="Medium RAG confidence plus factual query should be verified on the web",
                    phase="post_retrieval",
                    confidence=0.70,
                )

        return SearchDecision(
            should_search=False,
            reason="medium_confidence_sufficient",
            reason_detail="Medium-confidence RAG is sufficient for a non-factual query",
            phase="post_retrieval",
            confidence=0.65,
        )


_SCHEMA_READY = False


def _ensure_search_decision_log_schema() -> bool:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return True
    try:
        from backend.db import db

        db.execute_update(
            """
            CREATE TABLE IF NOT EXISTS search_decision_log (
                id SERIAL PRIMARY KEY,
                creator_id TEXT,
                query TEXT,
                should_search BOOLEAN,
                reason TEXT,
                phase TEXT,
                confidence FLOAT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        db.execute_update(
            """
            CREATE INDEX IF NOT EXISTS idx_sdl_creator_id
            ON search_decision_log(creator_id, created_at DESC)
            """
        )
        _SCHEMA_READY = True
        return True
    except Exception as exc:
        logger.warning("Search decision log schema bootstrap failed: %s", exc)
        return False


def log_search_decision(creator_id: str, query: str, decision: SearchDecision) -> None:
    try:
        if not _ensure_search_decision_log_schema():
            return
        from backend.db import db

        db.execute_update(
            """
            INSERT INTO search_decision_log
                (creator_id, query, should_search, reason, phase, confidence, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                str(creator_id),
                str(query or ""),
                bool(decision.should_search),
                str(decision.reason or ""),
                str(decision.phase or ""),
                float(decision.confidence or 0.0),
            ),
        )
    except Exception as exc:
        logger.warning("Search decision log write failed: %s", exc)
