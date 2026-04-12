
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import backend.rag as rag
from backend.db import db
from backend.settings import settings
from backend.services.creator_entity_service import creator_entity_service
from backend.services.creator_fact_policy import (
    classify_creator_fact_query,
    extract_timeline_focus as _policy_extract_timeline_focus,
    is_creator_journey_question,
    is_creator_start_timeline_question,
    is_publication_timeline_question as _policy_is_publication_timeline_question,
    is_timeline_question as _policy_is_timeline_question,
    looks_like_catalog_question as _policy_looks_like_catalog_question,
)
from backend.services.decision_service import decision_service
from backend.services.evidence_router import EvidenceRouter, detect_evidence_contradiction, log_evidence_plan
from backend.services.fact_registry import fact_registry
from backend.services.live_search_rules import build_live_search_query
from backend.services.search_decision_engine import SearchDecisionEngine


logger = logging.getLogger(__name__)

SEARCH_TRACE_PREFIX = "[SEARCH_TRACE]"
HOT_FACT_CACHE_TTL = 900
MONTH_PATTERN = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
TIMELINE_TOKENS = (
    "publish",
    "published",
    "publication",
    "release",
    "released",
    "launch",
    "launched",
    "come out",
    "what year",
    "what date",
    "which month",
    "write",
    "wrote",
    "written",
    "start",
    "started",
    "begin",
    "began",
    "get into",
    "got into",
    "how long have you been",
)
_hot_fact_cache: Dict[str, Dict[str, Any]] = {}


@dataclass
class StructuredFactCandidate:
    fact_field: str
    subject: str
    value: str
    answer_text: str
    source_url: str = ""
    source_title: str = ""
    confidence: float = 0.9


def extract_search_text(result: Any) -> str:
    extracted = ""
    if isinstance(result, dict):
        for field in ("answer_text", "response_text", "snippet", "text", "value"):
            val = result.get(field, "")
            if isinstance(val, str) and len(val.strip()) > 10:
                extracted = val.strip()
                break
        if not extracted:
            parts: List[str] = []
            for field in ("title", "snippet", "text", "response_text"):
                val = result.get(field, "")
                if isinstance(val, str) and val.strip():
                    parts.append(val.strip())
            if parts:
                extracted = " ".join(parts[:3]).strip()
            else:
                nested_parts: List[str] = []
                for field in ("results", "items"):
                    val = result.get(field)
                    if isinstance(val, list):
                        nested_text = extract_search_text(val)
                        if nested_text:
                            nested_parts.append(nested_text)
                if nested_parts:
                    extracted = " ".join(nested_parts[:2]).strip()
                elif any(field in result for field in ("results", "sources", "packages", "citations", "query_plan", "search_entry_point")):
                    extracted = ""
                else:
                    extracted = str(result).strip()
    elif isinstance(result, list):
        parts = []
        for item in result:
            item_text = extract_search_text(item)
            if item_text:
                parts.append(item_text)
        extracted = " ".join(parts[:3]).strip()
    else:
        extracted = str(result).strip()
    logger.info(f"{SEARCH_TRACE_PREFIX} extraction: raw_type={type(result).__name__} extracted_len={len(extracted)}")
    return extracted


def _looks_like_catalog_question(question: str, query_goal: str = "") -> bool:
    return _policy_looks_like_catalog_question(question, query_goal=query_goal)


def _looks_like_timeline_question(question: str, query_goal: str = "") -> bool:
    return _policy_is_timeline_question(question, query_goal=query_goal)


def _is_publication_timeline_question(question: str) -> bool:
    return _policy_is_publication_timeline_question(question)


def _extract_timeline_focus(question: str) -> str:
    return _policy_extract_timeline_focus(question)


def _looks_like_bibliographic_timeline_result(blob: str) -> bool:
    lowered = str(blob or "").lower()
    if not lowered:
        return False
    has_publication_marker = any(
        token in lowered
        for token in (
            "published",
            "publication date",
            "release date",
            "released on",
            "launched on",
            "amazon",
            "audible",
            "goodreads",
            "publisher",
            "ebook",
            "e book",
        )
    )
    has_start_marker = any(
        token in lowered
        for token in (
            "started",
            "began",
            "got into",
            "first got into",
            "since",
            "journey",
        )
    )
    return has_publication_marker and not has_start_marker


def _render_timeline_sentence(question: str, *, subject: str = "", value: str = "", is_direct_voice: bool = False) -> str:
    if not value:
        return ""
    if _is_publication_timeline_question(question):
        phrase = "put out" if is_direct_voice else "published"
        thing = subject or "it"
        return f"I {phrase} {thing} in {value}."
    focus = _extract_timeline_focus(question) or subject
    focus = str(focus or "").strip()
    if focus and focus.lower() not in {"it", "this", "that"}:
        return f"I started {focus} in {value}."
    return f"I started in {value}."


def _extract_fact_value_from_text(fact_field: str, text: str) -> str:
    candidate_text = re.sub(r"\s+", " ", str(text or "").strip())
    if not candidate_text:
        return ""

    lowered_field = str(fact_field or "").strip().lower()
    if lowered_field == "full_name":
        for pattern in (
            re.compile(r"(?:full|real|legal)\s+name\s*(?:is|:)\s*([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})"),
            re.compile(r"^[A-Za-z0-9@._' -]{2,60}\s*\(([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})\)"),
        ):
            match = pattern.search(candidate_text)
            if match:
                return re.sub(r"\s+", " ", match.group(1)).strip()

    if lowered_field in {"publication_date", "launch_date", "start_date", "public_fact"}:
        for pattern in (
            rf"\b({MONTH_PATTERN})\s+\d{{1,2}},\s+\d{{4}}\b",
            rf"\b({MONTH_PATTERN})\s+\d{{4}}\b",
            r"\b(20\d{2}|19\d{2})\b",
        ):
            match = re.search(pattern, candidate_text, re.IGNORECASE)
            if match:
                return match.group(0)

    if lowered_field == "price":
        match = re.search(r"\$\s?\d[\d,]*(?:\.\d{2})?", candidate_text)
        if match:
            return match.group(0)

    if lowered_field in {"followers", "subscribers", "students", "members", "latest_episode", "valuation", "net_worth"}:
        match = re.search(r"\b\d[\d,]*(?:\.\d+)?(?:\s?[kKmM])?\b", candidate_text)
        if match:
            return match.group(0)

    return ""


def _normalize_creator_start_focus(focus: str) -> str:
    normalized = re.sub(r"\s+", " ", str(focus or "").strip().lower())
    normalized = re.sub(r"^(?:doing|in|into|on)\s+", "", normalized)
    if not normalized:
        return ""

    alias_map = (
        (r"\b(?:day\s*)?trad(?:e|ing|er|ers)\b|\bswing\s*trad(?:e|ing)\b", "trading"),
        (r"\binvest(?:ing|ment|ments)?\b", "investing"),
        (r"\b(?:youtube|content\s+creation|creating\s+content|making\s+content|videos?)\b", "content creation"),
        (r"\b(?:podcast|podcasting)\b", "podcasting"),
        (r"\b(?:dropship(?:ping)?|e-?commerce|online\s+store)\b", "ecommerce"),
        (r"\b(?:business|entrepreneur(?:ship)?)\b", "business"),
    )
    for pattern, canonical in alias_map:
        if re.search(pattern, normalized):
            return canonical
    return normalized


def _normalize_journey_reason_text(reason: str, creator_name: str = "") -> str:
    text = re.sub(r"\s+", " ", str(reason or "").strip(" .,:;!?"))
    if not text:
        return ""
    if creator_name:
        text = re.sub(rf"\b{re.escape(creator_name)}(?:'s)?\b", "I", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(he|she|they)\b", "I", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(his|her|their)\b", "my", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(himself|herself|themselves)\b", "myself", text, flags=re.IGNORECASE)
    text = re.sub(r"^I\s+said\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^said\s+", "", text, flags=re.IGNORECASE)
    return text.strip(" .,:;!?")


def _extract_journey_reason(blob: str, creator_name: str = "") -> str:
    normalized = re.sub(r"\s+", " ", str(blob or "").strip())
    if not normalized:
        return ""

    segments = re.split(r"(?<=[.!?])\s+", normalized)
    patterns = [
        re.compile(r"\bbecause\s+([^.!?;]+)", re.IGNORECASE),
        re.compile(r"\bafter\s+([^.!?;]+)", re.IGNORECASE),
        re.compile(r"\b(?:wanted|wanting)\s+to\s+([^.!?;]+)", re.IGNORECASE),
        re.compile(r"\blooking\s+for\s+([^.!?;]+)", re.IGNORECASE),
    ]

    for segment in segments:
        cleaned_segment = segment.strip()
        if not cleaned_segment:
            continue
        for pattern in patterns:
            match = pattern.search(cleaned_segment)
            if not match:
                continue
            candidate = match.group(0) if pattern.pattern.startswith("\\bbecause") or pattern.pattern.startswith("\\bafter") else match.group(0)
            normalized_candidate = _normalize_journey_reason_text(candidate, creator_name)
            if normalized_candidate and len(normalized_candidate.split()) >= 3:
                return normalized_candidate
    return ""


def _preview_text(value: Any, limit: int = 500) -> str:
    text = extract_search_text(value)
    if not text:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _hot_cache_key(creator_id: int, entity_subject: str, fact_field: str) -> str:
    normalized_subject = re.sub(r"\s+", " ", str(entity_subject or "").strip().lower())
    normalized_field = str(fact_field or "").strip().lower()
    return f"{creator_id}:{normalized_subject}:{normalized_field}"


def _get_hot_fact(cache_key: str) -> Optional[StructuredFactCandidate]:
    entry = _hot_fact_cache.get(cache_key)
    if not entry:
        return None
    if time.time() - float(entry.get("ts") or 0.0) > HOT_FACT_CACHE_TTL:
        _hot_fact_cache.pop(cache_key, None)
        return None
    candidate = entry.get("value")
    if isinstance(candidate, StructuredFactCandidate):
        logger.info(f"{SEARCH_TRACE_PREFIX} cache_hit: {cache_key}")
        return candidate
    return None


def _set_hot_fact(cache_key: str, candidate: StructuredFactCandidate) -> None:
    if not candidate or not candidate.answer_text.strip():
        return
    _hot_fact_cache[cache_key] = {"value": candidate, "ts": time.time()}
    logger.info(f"{SEARCH_TRACE_PREFIX} cache_set: {cache_key} ({len(candidate.answer_text)} chars)")


def _repair_first_person_creator_reference(text: str, creator_name: str) -> str:
    repaired = str(text or "")
    if not repaired.strip() or not creator_name:
        return repaired.strip()
    escaped = re.escape(creator_name)
    repairs = [
        (rf"\b{escaped}'s\b", "my"),
        (rf"\b{escaped}\s+is\b", "I am"),
        (rf"\b{escaped}\s+was\b", "I was"),
        (rf"\b{escaped}\s+has\b", "I have"),
        (rf"\baccording to {escaped}\b", "from what I know"),
        (rf"\bcheck {escaped}'s\b", "check my"),
        (rf"\bvisit {escaped}'s\b", "visit my"),
    ]
    for pattern, replacement in repairs:
        repaired = re.sub(pattern, replacement, repaired, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", repaired).strip()

class PersonalBioService:
    """
    Handles personal/biographical questions about the creator.
    Pipeline:
    1. Search internal knowledge (chunks, bios).
    2. If validation fails/low confidence -> Search Web (trusted sources).
    3. Determine Decision Move (Answer/Decline/Reframe) using policy.
    4. Synthesize answer in creator voice based on the chosen move.
    """

    def _structured_candidate_has_journey_reason(
        self,
        candidate: Optional[StructuredFactCandidate],
        creator_name: str,
        *,
        source_snippet: str = "",
    ) -> bool:
        if not candidate:
            return False
        blob = " ".join(
            part
            for part in [
                str(candidate.answer_text or "").strip(),
                str(candidate.value or "").strip(),
                str(source_snippet or "").strip(),
            ]
            if part
        )
        return bool(_extract_journey_reason(blob, creator_name))

    def __init__(self):
        from backend.services.research_provider import get_research_provider
        self.researcher = get_research_provider()

    def handle_personal_question(
        self, 
        user_id: int, 
        creator_id: int, 
        question: str, 
        voice_profile: Dict[str, Any],
        creator_name: str,
        decision_policy: Dict[str, Any],
        creator_profile: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        allow_web: bool = True,
    ) -> Dict[str, Any]:
        """
        Main entry point. Returns { "answer": str, "confidence": "HIGH"|"MEDIUM"|"LOW", "sources": [], "move": str }
        """
        creator_payload = dict(creator_profile or {})
        creator_payload.setdefault("id", creator_id)
        creator_payload.setdefault("name", creator_name)
        evidence_plan = EvidenceRouter(creator_payload).build_plan(
            question,
            conversation_history=conversation_history,
        )
        resolved_question = evidence_plan.resolved_query or decision_service.resolve_followup_question(question, conversation_history)
        resolved_entity = creator_entity_service.resolve_entity(
            resolved_question,
            creator_id=creator_id,
            creator_profile=creator_payload,
            conversation_history=conversation_history,
        )
        contextual_question = self._contextualize_search_question(
            resolved_question,
            creator_name,
            conversation_history,
            entity=resolved_entity,
            evidence_plan=evidence_plan,
        )
        entity_graph_evidence = creator_entity_service.build_entity_support_chunks(
            entity=resolved_entity,
            query=resolved_question,
            creator_id=creator_id,
            creator_profile=creator_payload,
            conversation_history=conversation_history,
        )
        logger.info(
            "PersonalBioService: Processing '%s' for creator %s (resolved='%s', contextual='%s')",
            question,
            creator_id,
            resolved_question,
            contextual_question,
        )
        log_evidence_plan(
            creator_id,
            question,
            evidence_plan,
            metadata={"service": "personal_bio_service"},
        )
        
        # 1. Classification
        q_type, topic, sufficiency = decision_service.classify_question(resolved_question, "personal_bio_question", conversation_history)
        public_fact_query = (
            evidence_plan.primary_world == "live_world"
            or (
                evidence_plan.primary_world == "creator_world"
                and (evidence_plan.should_verify or evidence_plan.should_search_web)
            )
            or evidence_plan.query_goal in {"timeline_lookup", "price_lookup", "stat_lookup", "current_stat_lookup"}
            or (
                evidence_plan.query_goal in {"availability_lookup", "resource_lookup"}
                and evidence_plan.should_search_web
            )
            or self._is_public_creator_fact_query(
                contextual_question,
                creator_name,
                creator_profile,
                conversation_history=conversation_history,
            )
        )
        if evidence_plan.query_goal in {"entity_confirmation", "entity_overview"}:
            public_fact_query = False
        if evidence_plan.query_goal == "entity_catalog_lookup":
            public_fact_query = False
        researcher_enabled = bool(getattr(self.researcher, "enabled", True))
        # Respect creator retrieval mode strictly. When the caller disables web
        # (for "ingested only"), this service must stay inside ingested content,
        # soul_md, and creator profile data without silently re-enabling search.
        effective_allow_web = bool(allow_web and researcher_enabled)
        policy = classify_creator_fact_query(
            resolved_question,
            entity_type=evidence_plan.entity_type,
            query_goal=evidence_plan.query_goal,
        )
        fact_field = fact_registry.infer_fact_field(resolved_question, evidence_plan.entity_type)
        entity_subject = self._derive_entity_subject(
            resolved_question,
            creator_name,
            evidence_plan=evidence_plan,
            entity=resolved_entity,
        )
        hot_cache_key = _hot_cache_key(creator_id, entity_subject, fact_field) if public_fact_query and entity_subject and fact_field else ""
        hot_cached_fact = _get_hot_fact(hot_cache_key) if hot_cache_key else None
        cached_fact = None
        if public_fact_query and entity_subject and fact_field:
            cached_fact = fact_registry.lookup_fact(
                creator_id,
                entity_subject,
                fact_field,
                freshness_required=evidence_plan.freshness_required,
            )
        candidate_source_snippet = ""
        if policy.kind == "creator_journey" and hot_cached_fact:
            if not self._structured_candidate_has_journey_reason(hot_cached_fact, creator_name):
                hot_cached_fact = None
        if policy.kind == "creator_journey" and cached_fact:
            candidate_source_snippet = str(getattr(cached_fact, "source_snippet", "") or "")
        logger.info(
            f"{SEARCH_TRACE_PREFIX} handle_start: question={question!r} resolved={resolved_question!r} "
            f"goal={evidence_plan.query_goal} fact_field={fact_field!r} entity={entity_subject!r} "
            f"public_fact={public_fact_query} allow_web={effective_allow_web}"
        )

        if evidence_plan.query_goal == "entity_confirmation" and resolved_entity:
            return {
                "answer": self._answer_entity_confirmation(resolved_entity),
                "confidence": "HIGH",
                "sources": entity_graph_evidence,
                "move": "ANSWER_ENTITY_GRAPH_CONFIRMATION",
                "evidence_plan": evidence_plan.to_dict(),
                "fact_cache_hit": False,
                "contradiction_report": {"has_contradiction": False, "kind": "none"},
            }

        if evidence_plan.query_goal == "availability_lookup" and resolved_entity and (resolved_entity.get("official_urls") or []):
            return {
                "answer": self._answer_entity_availability(resolved_entity),
                "confidence": "HIGH",
                "sources": entity_graph_evidence,
                "move": "ANSWER_ENTITY_GRAPH_AVAILABILITY",
                "evidence_plan": evidence_plan.to_dict(),
                "fact_cache_hit": False,
                "contradiction_report": {"has_contradiction": False, "kind": "none"},
            }

        if evidence_plan.query_goal == "entity_catalog_lookup":
            entity_catalog = creator_entity_service.list_entities(
                entity_type=evidence_plan.entity_type,
                creator_id=creator_id,
                creator_profile=creator_payload,
            )
            web_catalog = []
            if effective_allow_web and callable(getattr(self.researcher, "lookup_creator_entities", None)):
                web_lookup = self.researcher.lookup_creator_entities(
                    contextual_question,
                    creator_payload,
                    entity_type=evidence_plan.entity_type,
                    conversation_history=conversation_history,
                ) or {}
                web_catalog = list(web_lookup.get("entities") or [])
            merged_catalog = self._merge_entity_catalog(entity_catalog, web_catalog, evidence_plan.entity_type)
            if merged_catalog:
                return {
                    "answer": self._answer_entity_catalog(
                        merged_catalog,
                        creator_name,
                        evidence_plan.entity_type,
                        question=resolved_question,
                    ),
                    "confidence": "HIGH" if web_catalog else "MEDIUM",
                    "sources": self._catalog_sources(merged_catalog),
                    "move": "ANSWER_ENTITY_CATALOG",
                    "evidence_plan": evidence_plan.to_dict(),
                    "fact_cache_hit": False,
                    "contradiction_report": {"has_contradiction": False, "kind": "none"},
                }
        
        # 2. Evidence Gathering
        internal_facts = (
            self._search_internal_knowledge(creator_id, contextual_question, creator_profile=creator_profile)
            if evidence_plan.should_search_corpus
            else []
        )
        if entity_graph_evidence:
            internal_facts = entity_graph_evidence + internal_facts
        cached_facts = self._cached_fact_to_evidence(cached_fact)
        if cached_facts:
            internal_facts = cached_facts + internal_facts

        extracted_fact = hot_cached_fact or self._cached_fact_to_candidate(cached_fact)
        if policy.kind == "creator_journey" and extracted_fact and not self._structured_candidate_has_journey_reason(
            extracted_fact,
            creator_name,
            source_snippet=candidate_source_snippet,
        ):
            extracted_fact = None
        web_facts = []
        if not extracted_fact and effective_allow_web and evidence_plan.should_search_web and (
            public_fact_query or self._needs_more_evidence(internal_facts) or evidence_plan.should_verify
        ):
            logger.info("PersonalBioService: Internal evidence weak, checking web...")
            web_facts, extracted_fact = self._search_web_evidence(
                creator_id,
                creator_name,
                contextual_question,
                creator_profile=creator_profile,
                conversation_history=conversation_history,
                evidence_plan=evidence_plan,
                resolved_entity=resolved_entity,
                fact_field=fact_field,
                entity_subject=entity_subject,
            )
            if policy.kind == "creator_journey" and extracted_fact and not self._structured_candidate_has_journey_reason(
                extracted_fact,
                creator_name,
                source_snippet=str((web_facts[0] or {}).get("text") or "") if web_facts else "",
            ):
                extracted_fact = None
        elif extracted_fact:
            logger.info(f"{SEARCH_TRACE_PREFIX} fact_source: using_cached_fact field={extracted_fact.fact_field} value={extracted_fact.value!r}")

        contradiction_report = detect_evidence_contradiction(
            resolved_question,
            corpus_chunks=internal_facts,
            web_results=web_facts,
        )
        if contradiction_report.get("has_contradiction") and web_facts:
            all_evidence = web_facts + internal_facts
        else:
            all_evidence = internal_facts + web_facts

        requires_verified_timeline = bool(
            public_fact_query
            and str(getattr(evidence_plan, "query_goal", "") or "").lower() == "timeline_lookup"
            and effective_allow_web
        )

        if public_fact_query:
            if extracted_fact:
                rendered_structured_answer = self._render_structured_fact_answer(
                    extracted_fact,
                    resolved_question,
                    creator_name,
                    voice_profile,
                    entity=resolved_entity,
                )
                if rendered_structured_answer:
                    extracted_fact.answer_text = rendered_structured_answer
                    self._cache_structured_fact(
                        creator_id,
                        entity_subject,
                        evidence_plan.entity_type or str((resolved_entity or {}).get("type") or ""),
                        fact_field,
                        extracted_fact,
                        evidence_plan.freshness_required,
                        cache_key=hot_cache_key,
                    )
                    answer = extracted_fact.answer_text
                    logger.info(f"{SEARCH_TRACE_PREFIX} handle_return: move=ANSWER_STRUCTURED_FACT answer={answer[:240]!r}")
                    return {
                        "answer": answer,
                        "confidence": "HIGH" if web_facts or hot_cached_fact else ("MEDIUM" if cached_fact else "MEDIUM"),
                        "sources": all_evidence,
                        "move": "ANSWER_STRUCTURED_FACT",
                        "evidence_plan": evidence_plan.to_dict(),
                        "fact_cache_hit": bool(cached_fact or hot_cached_fact),
                        "contradiction_report": contradiction_report,
                    }
            if requires_verified_timeline and not web_facts:
                fallback_answer = self._public_fact_fallback(resolved_question, creator_name, evidence_plan=evidence_plan, entity=resolved_entity)
                logger.info(f"{SEARCH_TRACE_PREFIX} handle_return: move=TIMELINE_VERIFY_REQUIRED answer={fallback_answer[:240]!r}")
                return {
                    "answer": fallback_answer,
                    "confidence": "LOW",
                    "sources": all_evidence,
                    "move": "TIMELINE_VERIFY_REQUIRED",
                    "evidence_plan": evidence_plan.to_dict(),
                    "fact_cache_hit": bool(cached_fact),
                    "contradiction_report": contradiction_report,
                }
            direct_public_answer = self._answer_public_creator_fact(
                resolved_question,
                all_evidence,
                creator_name,
                entity=resolved_entity,
                voice_profile=voice_profile,
            )
            if direct_public_answer:
                self._cache_public_fact_answer(
                    creator_id,
                    entity_subject,
                    evidence_plan.entity_type or str((resolved_entity or {}).get("type") or ""),
                    fact_field,
                    direct_public_answer,
                    web_facts or cached_facts or internal_facts,
                    evidence_plan.freshness_required,
                )
                return {
                    "answer": direct_public_answer,
                    "confidence": "HIGH" if web_facts else ("MEDIUM" if cached_fact else "MEDIUM"),
                    "sources": all_evidence,
                    "move": "ANSWER_PUBLIC_FACT_CACHE" if cached_fact and not web_facts else "ANSWER_PUBLIC_FACT",
                    "evidence_plan": evidence_plan.to_dict(),
                    "fact_cache_hit": bool(cached_fact),
                    "contradiction_report": contradiction_report,
                }

            synthesized_public_answer = self._synthesize_public_fact_answer(
                resolved_question,
                all_evidence,
                voice_profile,
                creator_name,
            )
            if synthesized_public_answer:
                self._cache_public_fact_answer(
                    creator_id,
                    entity_subject,
                    evidence_plan.entity_type or str((resolved_entity or {}).get("type") or ""),
                    fact_field,
                    synthesized_public_answer,
                    web_facts or cached_facts or internal_facts,
                    evidence_plan.freshness_required,
                )
                return {
                    "answer": synthesized_public_answer,
                    "confidence": "HIGH" if web_facts else "MEDIUM",
                    "sources": all_evidence,
                    "move": "ANSWER_PUBLIC_FACT",
                    "evidence_plan": evidence_plan.to_dict(),
                    "fact_cache_hit": bool(cached_fact),
                    "contradiction_report": contradiction_report,
                }

            fallback_answer = self._public_fact_fallback(resolved_question, creator_name, evidence_plan=evidence_plan, entity=resolved_entity)
            logger.info(f"{SEARCH_TRACE_PREFIX} handle_return: move=DIRECT_TO_OFFICIAL_SOURCE answer={fallback_answer[:240]!r}")
            return {
                "answer": fallback_answer,
                "confidence": "LOW",
                "sources": all_evidence,
                "move": "DIRECT_TO_OFFICIAL_SOURCE",
                "evidence_plan": evidence_plan.to_dict(),
                "fact_cache_hit": bool(cached_fact),
                "contradiction_report": contradiction_report,
            }
        
        # 3. Confidence Scoring (Basic logic for routing)
        confidence = "LOW"
        if all_evidence:
             max_sim = max([e.get("sim", 0) for e in all_evidence if "sim" in e] or [0.8])
             if max_sim > 0.85: confidence = "HIGH"
             elif max_sim > 0.7: confidence = "MEDIUM"

        # 4. Decision Router
        move = decision_service.choose_move(decision_policy, q_type, topic, confidence, sufficiency=sufficiency)
        logger.info(f"PersonalBioService: Decision Move = {move} (Topic: {topic}, Confidence: {confidence})")

        # 5. Synthesis
        synthesis = self._synthesize_answer(
            resolved_question, 
            all_evidence, 
            voice_profile, 
            creator_name, 
            move,
            topic
        )
        synthesis["move"] = move
        synthesis["evidence_plan"] = evidence_plan.to_dict()
        synthesis["fact_cache_hit"] = bool(cached_fact)
        synthesis["contradiction_report"] = contradiction_report
        logger.info(f"{SEARCH_TRACE_PREFIX} handle_return: move={move} answer={str(synthesis.get('answer') or '')[:240]!r}")
        
        return synthesis

    def _contextualize_search_question(
        self,
        question: str,
        creator_name: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        entity: Optional[Dict[str, Any]] = None,
        evidence_plan: Optional[Any] = None,
    ) -> str:
        contextual = build_live_search_query(
            question,
            conversation_history,
            creator_name=creator_name,
        )
        if len(re.findall(r"[a-z0-9']+", contextual.lower())) < 3:
            contextual = question
        entity_name = str((entity or {}).get("name") or "").strip()
        if entity_name and entity_name.lower() not in contextual.lower():
            contextual = f"{contextual} {entity_name}".strip()
        if evidence_plan and getattr(evidence_plan, "primary_world", "") == "live_world":
            contextual = f"{contextual} current".strip()
        return contextual

    def _derive_entity_subject(
        self,
        question: str,
        creator_name: str,
        *,
        evidence_plan: Optional[Any] = None,
        entity: Optional[Dict[str, Any]] = None,
    ) -> str:
        query_goal = str(getattr(evidence_plan, "query_goal", "") or "").lower()
        entity_type = str(getattr(evidence_plan, "entity_type", "") or "").lower()
        policy = classify_creator_fact_query(question, entity_type=entity_type, query_goal=query_goal)
        explicit = str(getattr(evidence_plan, "entity_subject", "") or "").strip()
        if policy.kind == "creator_start_timeline":
            focus = _normalize_creator_start_focus(
                policy.focus
                or explicit
                or str((entity or {}).get("name") or "")
                or self._extract_subject(question, [])
            )
            if focus and focus not in {"it", "this", "that", "one"}:
                return focus
            return creator_name
        if explicit:
            return explicit
        resolved_name = str((entity or {}).get("name") or "").strip()
        if resolved_name:
            return resolved_name
        extracted = self._extract_subject(question, [])
        if extracted and extracted.lower() not in {"it", "that", "this", "one", "book", "course", "podcast"}:
            return extracted
        lowered = str(question or "").lower()
        if query_goal in {"stat_lookup", "current_stat_lookup"}:
            return creator_name
        if entity_type == "book" or any(token in lowered for token in ("book", "published", "publication", "release", "write", "wrote", "written")):
            return ""
        return creator_name

    def _merge_entity_catalog(
        self,
        internal_entities: List[Dict[str, Any]],
        web_entities: List[Dict[str, Any]],
        entity_type: str,
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for entity in list(internal_entities or []) + list(web_entities or []):
            name = re.sub(r"\s+", " ", str(entity.get("name") or "").strip())
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "name": name,
                    "type": str(entity.get("type") or entity_type or "entity").strip().lower(),
                    "official_urls": list(entity.get("official_urls") or []),
                    "source_title": str(entity.get("source_title") or "").strip(),
                    "source_snippet": str(entity.get("source_snippet") or "").strip(),
                }
            )
        return merged

    def _catalog_sources(self, entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sources: List[Dict[str, Any]] = []
        for entity in entities:
            urls = [str(url or "").strip() for url in (entity.get("official_urls") or []) if str(url or "").strip()]
            snippet = str(entity.get("source_snippet") or "").strip()
            title = str(entity.get("source_title") or entity.get("name") or "").strip()
            if title or urls or snippet:
                sources.append(
                    {
                        "text": snippet or title,
                        "source": "entity_catalog",
                        "url": urls[0] if urls else "",
                        "title": title,
                        "sim": 0.88,
                    }
                )
        return sources

    def _answer_entity_catalog(
        self,
        entities: List[Dict[str, Any]],
        creator_name: str,
        entity_type: str,
        *,
        question: str = "",
    ) -> str:
        names = [str(entity.get("name") or "").strip() for entity in entities if str(entity.get("name") or "").strip()]
        count_requested = bool(re.search(r"\bhow many\b", str(question or "").lower()))
        if not names:
            if entity_type == "book":
                return "Yeah, I've written books."
            return "Yeah, I do."
        if len(names) == 1:
            if entity_type == "book":
                if count_requested:
                    return f"I've written 1 book: {names[0]}."
                return f"Yeah. I've written {names[0]}."
            return f"Yeah. I've got {names[0]}."

        if len(names) == 2:
            joined = f"{names[0]} and {names[1]}"
        else:
            joined = ", ".join(names[:-1]) + f", and {names[-1]}"

        if entity_type == "book":
            if count_requested:
                return f"I've written {len(names)} books: {joined}."
            return f"Yeah. I've written {joined}."
        if entity_type == "podcast":
            if count_requested:
                return f"I've got {len(names)} main podcasts or shows: {joined}."
            return f"Yeah. The main ones are {joined}."
        if entity_type == "course":
            if count_requested:
                return f"I've got {len(names)} main programs: {joined}."
            return f"Yeah. The main programs are {joined}."
        return f"Yeah. I've got {joined}."

    def _is_public_creator_fact_query(
        self,
        question: str,
        creator_name: str,
        creator_profile: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> bool:
        creator_payload = dict(creator_profile or {})
        creator_payload.setdefault("name", creator_name)
        decision = SearchDecisionEngine(creator_payload).pre_retrieval_decision(
            question,
            conversation_history=conversation_history,
        )
        return bool(decision.should_search)

    def _search_internal_knowledge(self, creator_id: int, question: str, creator_profile: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        emb = rag.create_embedding(question)
        retrieved = rag.retrieve_chunks(
            creator_id=creator_id,
            query_embedding=emb,
            top_k=5,
            max_distance=0.35,
        )

        facts = []
        for chunk in retrieved:
            facts.append({
                "text": chunk.get("content", ""),
                "source": "internal",
                "title": chunk.get("title"),
                "url": chunk.get("url"),
                "sim": max(0.0, 1.0 - float(chunk.get("distance", 1.0)))
            })

        profile = creator_profile or {}
        identity = profile.get("identity_fingerprint") or {}
        research = profile.get("research_summary") or {}
        if isinstance(identity, str):
            try:
                identity = json.loads(identity)
            except Exception:
                identity = {}
        if isinstance(research, str):
            try:
                research = json.loads(research)
            except Exception:
                research = {}

        def _push_fact(label: str, value: Any):
            if not value:
                return
            if isinstance(value, list):
                if not value:
                    return
                value_text = "; ".join(str(v) for v in value[:5] if v)
            elif isinstance(value, dict):
                value_text = json.dumps(value)
            else:
                value_text = str(value)
            value_text = value_text.strip()
            if not value_text:
                return
            facts.append({
                "text": f"{label}: {value_text}",
                "source": "profile",
                "sim": 0.9,
            })

        _push_fact("Bio", identity.get("bio"))
        _push_fact("Mission", identity.get("mission"))
        _push_fact("Worldview", identity.get("worldview"))
        _push_fact("Verified facts", identity.get("verified_facts"))
        _push_fact("Public consensus", research.get("public_consensus"))
        _push_fact("Creator claims", research.get("creator_claims"))
        _push_fact("Themes", research.get("themes"))
        return facts

    def _search_web_evidence(
        self,
        creator_id: int,
        creator_name: str,
        question: str,
        creator_profile: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        evidence_plan: Optional[Any] = None,
        resolved_entity: Optional[Dict[str, Any]] = None,
        fact_field: str = "",
        entity_subject: str = "",
    ) -> Tuple[List[Dict[str, Any]], Optional[StructuredFactCandidate]]:
        profile = dict(creator_profile or {})
        profile.setdefault("id", creator_id)
        profile.setdefault("name", creator_name)
        query_goal = str(getattr(evidence_plan, "query_goal", "") or "").lower()
        policy = classify_creator_fact_query(
            question,
            entity_type=str((resolved_entity or {}).get("type") or ""),
            query_goal=query_goal,
        )
        queries = self._build_public_fact_search_queries(
            question,
            creator_name,
            evidence_plan=evidence_plan,
            resolved_entity=resolved_entity,
            conversation_history=conversation_history,
        )
        for official_url in (resolved_entity or {}).get("official_urls") or []:
            domain = re.sub(r"^www\.", "", (urlparse(official_url).netloc or "").lower())
            if domain:
                subject_hint = str((resolved_entity or {}).get("name") or self._extract_subject(question, []))
                queries.append(f"site:{domain} {creator_name} {subject_hint or question}".strip())

        best_evidence: List[Dict[str, Any]] = []
        best_score = 0.0
        best_fact: Optional[StructuredFactCandidate] = None
        seen_queries = set()
        max_query_attempts = self._max_query_attempts(query_goal, policy_kind=policy.kind)
        grounded_max_queries = self._grounded_query_plan_limit(query_goal)
        deadline = time.monotonic() + self._search_time_budget_seconds(query_goal, policy_kind=policy.kind)
        for query in queries:
            if len(seen_queries) >= max_query_attempts:
                break
            normalized_query = re.sub(r"\s+", " ", query).strip()
            if not normalized_query or normalized_query.lower() in seen_queries:
                continue
            seen_queries.add(normalized_query.lower())
            logger.info(f"{SEARCH_TRACE_PREFIX} query: {normalized_query}")
            try:
                raw_result: Any = None
                candidate: Optional[StructuredFactCandidate] = None
                use_fact_lookup = bool(
                    fact_field
                    and query_goal in {"timeline_lookup", "price_lookup", "stat_lookup", "current_stat_lookup"}
                    and callable(getattr(self.researcher, "lookup_public_fact", None))
                )
                if use_fact_lookup:
                    raw_result = self.researcher.lookup_public_fact(
                        normalized_query,
                        profile,
                        fact_field=fact_field or "",
                        entity_subject=entity_subject or str((resolved_entity or {}).get("name") or ""),
                        conversation_history=conversation_history,
                    ) or {}
                    logger.info(f"{SEARCH_TRACE_PREFIX} provider_raw: { _preview_text(raw_result) }")
                    evidence = self._normalize_fact_lookup_evidence(raw_result)
                    candidate = self._candidate_from_fact_lookup(
                        raw_result,
                        fact_field=fact_field,
                        entity_subject=entity_subject or str((resolved_entity or {}).get("name") or ""),
                    )
                elif callable(getattr(self.researcher, "grounded_overview", None)):
                    raw_result = self.researcher.grounded_overview(
                        normalized_query,
                        profile,
                        conversation_history=conversation_history,
                        max_queries=grounded_max_queries,
                    ) or {}
                    logger.info(f"{SEARCH_TRACE_PREFIX} provider_raw: { _preview_text(raw_result) }")
                    evidence = self._normalize_grounded_overview_evidence(raw_result)
                elif hasattr(self.researcher, "search_general"):
                    raw_result = self.researcher.search_general(normalized_query, creator_id, creator_profile=creator_profile)
                    logger.info(f"{SEARCH_TRACE_PREFIX} provider_raw: { _preview_text(raw_result) }")
                    evidence = self._normalize_web_evidence(raw_result)
                else:
                    raw_result = self.researcher.search(
                        normalized_query,
                        profile,
                        resource_type="any",
                        conversation_history=conversation_history,
                    )
                    logger.info(f"{SEARCH_TRACE_PREFIX} provider_raw: { _preview_text(raw_result) }")
                    evidence = self._normalize_web_evidence(raw_result)
            except Exception as e:
                logger.error(f"{SEARCH_TRACE_PREFIX} query_error: query={normalized_query!r} error={e}")
                continue
            logger.info(f"{SEARCH_TRACE_PREFIX} query_result_count: query={normalized_query!r} count={len(evidence)}")
            score = self._score_web_evidence_quality(evidence_plan, evidence)
            if candidate is None:
                candidate = self._extract_structured_fact_candidate(
                    question,
                    evidence,
                    fact_field=fact_field,
                    entity_subject=entity_subject or str((resolved_entity or {}).get("name") or ""),
                )
            if candidate:
                logger.info(
                    f"{SEARCH_TRACE_PREFIX} fact_candidate: query={normalized_query!r} "
                    f"field={candidate.fact_field} value={candidate.value!r} confidence={candidate.confidence}"
                )
            if score > best_score:
                best_score = score
                best_evidence = evidence
                best_fact = candidate
            if score >= 0.9:
                break
            if time.monotonic() >= deadline:
                logger.warning(f"{SEARCH_TRACE_PREFIX} query_budget_exceeded: goal={query_goal} best_score={best_score}")
                break

        logger.info(
            f"{SEARCH_TRACE_PREFIX} web_search_complete: attempts={len(seen_queries)} "
            f"best_score={best_score} fact_found={bool(best_fact)} evidence_count={len(best_evidence)}"
        )
        return best_evidence, best_fact

    def _max_query_attempts(self, query_goal: str, *, policy_kind: str = "") -> int:
        if query_goal == "timeline_lookup" and policy_kind == "creator_start_timeline":
            return 4
        if query_goal == "journey_lookup" and policy_kind == "creator_journey":
            return 3
        if query_goal in {"timeline_lookup", "price_lookup", "stat_lookup", "current_stat_lookup", "identity_lookup"}:
            return 2
        if query_goal in {"availability_lookup", "resource_lookup"}:
            return 2
        return 2

    def _grounded_query_plan_limit(self, query_goal: str) -> int:
        if query_goal == "journey_lookup":
            return 2
        if query_goal in {"timeline_lookup", "price_lookup", "stat_lookup", "current_stat_lookup", "identity_lookup"}:
            return 1
        if query_goal in {"availability_lookup", "resource_lookup"}:
            return 2
        return 1

    def _search_time_budget_seconds(self, query_goal: str, *, policy_kind: str = "") -> float:
        if query_goal == "timeline_lookup" and policy_kind == "creator_start_timeline":
            return 12.0
        if query_goal == "journey_lookup" and policy_kind == "creator_journey":
            return 10.0
        if query_goal in {"timeline_lookup", "price_lookup", "stat_lookup", "current_stat_lookup", "identity_lookup"}:
            return 9.0
        return 8.0

    def _normalize_web_evidence(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for result in results or []:
            title = (result.get("title") or "").strip() if isinstance(result, dict) else ""
            snippet = extract_search_text(result)
            url = (result.get("url") or "").strip() if isinstance(result, dict) else ""
            if not any([title, snippet, url]):
                continue
            normalized.append({
                "text": " | ".join(part for part in [title, snippet] if part)[:500],
                "source": "web",
                "url": url,
                "title": title,
                "sim": 0.82,
            })
        return normalized

    def _normalize_fact_lookup_evidence(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        normalized: List[Dict[str, Any]] = []
        response_text = re.sub(
            r"\s+",
            " ",
            str(payload.get("response_text") or payload.get("answer_text") or "").strip(),
        )
        if response_text:
            normalized.append(
                {
                    "text": response_text,
                    "source": "web_fact_lookup",
                    "url": str(payload.get("source_url") or ""),
                    "title": str(payload.get("source_title") or "Grounded Fact Lookup"),
                    "sim": float(payload.get("confidence") or 0.92),
                }
            )

        result_items = payload.get("results") or payload.get("sources") or []
        normalized.extend(self._normalize_web_evidence(list(result_items)))

        if not normalized and any(
            str(payload.get(field) or "").strip()
            for field in ("source_url", "source_title", "source_snippet")
        ):
            normalized.append(
                {
                    "text": " | ".join(
                        part
                        for part in [
                            str(payload.get("source_title") or "").strip(),
                            str(payload.get("source_snippet") or "").strip(),
                        ]
                        if part
                    )[:500],
                    "source": "web_fact_lookup",
                    "url": str(payload.get("source_url") or ""),
                    "title": str(payload.get("source_title") or ""),
                    "sim": float(payload.get("confidence") or 0.9),
                }
            )

        return normalized

    def _normalize_grounded_overview_evidence(self, overview: Dict[str, Any]) -> List[Dict[str, Any]]:
        evidence = self._normalize_web_evidence(list(overview.get("results") or []))
        response_text = re.sub(r"\s+", " ", extract_search_text(overview).strip())
        sources = list(overview.get("sources") or [])
        citations = list(overview.get("citations") or [])
        if response_text:
            primary_source = (sources[0] or {}) if sources else {}
            if not primary_source and citations:
                primary_source = citations[0] or {}
            source_url = str(primary_source.get("url") or "")
            source_title = str(primary_source.get("title") or "Grounded Web Summary")
            evidence.insert(
                0,
                {
                    "text": response_text,
                    "source": "web_grounded_summary",
                    "url": source_url,
                    "title": source_title,
                    "sim": 0.9,
                },
            )
        return evidence

    def _build_public_fact_search_queries(
        self,
        question: str,
        creator_name: str,
        *,
        evidence_plan: Optional[Any] = None,
        resolved_entity: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> List[str]:
        search_engine = SearchDecisionEngine({"name": creator_name})
        query_goal = str(getattr(evidence_plan, "query_goal", "") or "").lower()
        subject_hint = str((resolved_entity or {}).get("name") or self._extract_subject(question, []))
        entity_type = str((resolved_entity or {}).get("type") or "").lower()
        policy = classify_creator_fact_query(question, entity_type=entity_type, query_goal=query_goal)
        focus_hint = policy.focus or _extract_timeline_focus(question)
        queries: List[str] = []

        if query_goal == "timeline_lookup":
            if policy.kind == "publication_timeline" and subject_hint:
                queries.extend([
                    f'"{subject_hint}" publication date',
                    f'"{subject_hint}" release date',
                    f'{creator_name} "{subject_hint}" published',
                    f'{creator_name} "{subject_hint}" released',
                ])
            if policy.kind == "creator_start_timeline" and focus_hint:
                queries.extend([
                    f'{creator_name} when started {focus_hint}',
                    f'{creator_name} started {focus_hint}',
                    f'{creator_name} when got into {focus_hint}',
                    f'{creator_name} {focus_hint} since',
                    f'{creator_name} {focus_hint} journey',
                    f'{creator_name} how long has been {focus_hint}',
                    f'{creator_name} first got into {focus_hint}',
                ])
            if policy.kind == "publication_timeline" and entity_type == "book":
                queries.extend([
                    f'site:amazon.com "{subject_hint or creator_name}"',
                    f'site:audible.com "{subject_hint or creator_name}"',
                    f'site:goodreads.com "{subject_hint or creator_name}"',
                    f'site:penguinrandomhouse.com "{subject_hint or creator_name}"',
                ])
        elif query_goal == "journey_lookup" and focus_hint:
            queries.extend([
                f'{creator_name} why started {focus_hint}',
                f'{creator_name} why got into {focus_hint}',
                f'{creator_name} what got into {focus_hint}',
                f'{creator_name} {focus_hint} journey story',
                f'{creator_name} talks about why started {focus_hint}',
                f'{creator_name} explains why got into {focus_hint}',
            ])
        elif query_goal == "identity_lookup":
            queries.extend([
                f'{creator_name} full name',
                f'{creator_name} real name',
                f'{creator_name} legal name',
            ])

        if subject_hint and subject_hint.lower() not in {"it", "that", "this", "the book", "your book"}:
            queries.append(f'{creator_name} "{subject_hint}"')

        if query_goal == "timeline_lookup":
            if policy.kind == "publication_timeline" and entity_type == "book":
                queries.extend([
                    f"{creator_name} book published",
                    f"{creator_name} first book release date",
                ])
        elif query_goal == "journey_lookup":
            if focus_hint:
                queries.extend([
                    f'{creator_name} {focus_hint} journey',
                    f'{creator_name} {focus_hint} origin story',
                ])
        elif query_goal == "price_lookup":
            if subject_hint:
                queries.extend([
                    f'{creator_name} "{subject_hint}" price',
                    f'{creator_name} "{subject_hint}" cost',
                ])
        elif query_goal in {"stat_lookup", "current_stat_lookup"}:
            if subject_hint:
                queries.append(f'{creator_name} "{subject_hint}" current')
            queries.append(f"{creator_name} current public stats")
        elif query_goal in {"availability_lookup", "resource_lookup"} and subject_hint:
            queries.extend([
                f'{creator_name} "{subject_hint}" official',
                f'where to buy "{subject_hint}"',
            ])

        for term in search_engine.creator_terms:
            if len(term.split()) >= 2 and query_goal == "timeline_lookup" and policy.kind == "publication_timeline":
                queries.append(f'"{term}" publication date')
                queries.append(f'{creator_name} "{term}" published')

        queries.extend([question.strip(), f"{creator_name} {question}".strip()])

        deduped: List[str] = []
        seen = set()
        for candidate in queries:
            cleaned = re.sub(r"\s+", " ", str(candidate or "")).strip()
            key = cleaned.lower()
            if cleaned and key not in seen:
                deduped.append(cleaned)
                seen.add(key)
        return deduped

    def _score_web_evidence_quality(self, evidence_plan: Optional[Any], evidence: List[Dict[str, Any]]) -> float:
        if not evidence:
            return 0.0
        blob = self._evidence_blob(evidence)
        lowered_blob = blob.lower()
        query_goal = str(getattr(evidence_plan, "query_goal", "") or "").lower()
        resolved_query = str(getattr(evidence_plan, "resolved_query", "") or "")
        policy = classify_creator_fact_query(resolved_query, entity_type=str(getattr(evidence_plan, "entity_type", "") or ""), query_goal=query_goal)

        if query_goal == "timeline_lookup":
            if policy.kind == "creator_start_timeline":
                if _looks_like_bibliographic_timeline_result(blob):
                    return 0.15
                if re.search(r"\b(started|began|got into|first got into|since)\b", lowered_blob) and re.search(r"\b(20\d{2}|19\d{2})\b", blob):
                    return 0.98
                if re.search(r"\b(20\d{2}|19\d{2})\b", blob):
                    return 0.72
                return 0.3
            if re.search(rf"\b({MONTH_PATTERN})\s+\d{{1,2}},\s+\d{{4}}\b", blob, re.IGNORECASE):
                return 1.0
            if re.search(rf"\b({MONTH_PATTERN})\s+\d{{4}}\b", blob, re.IGNORECASE):
                return 0.95
            if re.search(r"\b(20\d{2}|19\d{2})\b", blob):
                return 0.9
            return 0.35

        if query_goal == "price_lookup":
            if re.search(r"\$\s?\d[\d,]*(?:\.\d{2})?", blob):
                return 0.95
            return 0.4

        if query_goal in {"stat_lookup", "current_stat_lookup"}:
            if re.search(r"\b\d[\d,]*(?:\.\d+)?(?:\s?[kKmM])?\b", blob):
                return 0.9
            return 0.35

        if query_goal in {"availability_lookup", "resource_lookup"}:
            if any(token in lowered_blob for token in ["amazon", "audible", "official", "website", "publisher"]):
                return 0.85
            if any(item.get("url") for item in evidence):
                return 0.75
            return 0.4

        return 0.6 if evidence else 0.0

    def _cached_fact_to_candidate(self, cached_fact) -> Optional[StructuredFactCandidate]:
        if not cached_fact or not getattr(cached_fact, "fact_value", ""):
            return None
        subject = str(getattr(cached_fact, "entity_subject", "") or "It").strip() or "It"
        fact_field = str(getattr(cached_fact, "fact_field", "") or "public_fact").strip()
        answer_text = str(getattr(cached_fact, "fact_value", "") or "").strip()
        if not answer_text:
            return None
        raw_value = (
            _extract_fact_value_from_text(fact_field, answer_text)
            or _extract_fact_value_from_text(fact_field, str(getattr(cached_fact, "source_snippet", "") or ""))
            or answer_text
        )
        return StructuredFactCandidate(
            fact_field=fact_field,
            subject=subject,
            value=raw_value,
            answer_text=answer_text,
            source_url=str(getattr(cached_fact, "source_url", "") or ""),
            source_title=str(getattr(cached_fact, "source_title", "") or ""),
            confidence=float(getattr(cached_fact, "confidence", 0.85) or 0.85),
        )

    def _candidate_from_fact_lookup(
        self,
        payload: Dict[str, Any],
        *,
        fact_field: str = "",
        entity_subject: str = "",
    ) -> Optional[StructuredFactCandidate]:
        if not isinstance(payload, dict) or not payload.get("found"):
            return None
        inferred_field = str(payload.get("fact_field") or fact_field or "public_fact").strip()
        value = str(payload.get("value") or "").strip()
        answer_text = str(payload.get("answer_text") or "").strip()
        if not value and not answer_text:
            return None
        normalized_value = value or _extract_fact_value_from_text(inferred_field, answer_text)
        subject = str(entity_subject or payload.get("subject") or "It").strip() or "It"
        return StructuredFactCandidate(
            fact_field=inferred_field,
            subject=subject,
            value=normalized_value or answer_text,
            answer_text=answer_text or value,
            source_url=str(payload.get("source_url") or "").strip(),
            source_title=str(payload.get("source_title") or "").strip(),
            confidence=float(payload.get("confidence") or 0.92),
        )

    def _render_structured_fact_answer(
        self,
        candidate: StructuredFactCandidate,
        question: str,
        creator_name: str,
        voice_profile: Optional[Dict[str, Any]] = None,
        *,
        entity: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not candidate:
            return ""
        fact_field = str(candidate.fact_field or "").strip().lower()
        subject = str((entity or {}).get("name") or candidate.subject or "it").strip() or "it"
        entity_type = str((entity or {}).get("type") or "").strip().lower()
        value = str(candidate.value or "").strip()
        existing = _repair_first_person_creator_reference(candidate.answer_text, creator_name)
        lowered_question = str(question or "").lower()
        policy = classify_creator_fact_query(question, entity_type=entity_type, query_goal="timeline_lookup" if _looks_like_timeline_question(question) else "")
        voice_blob = json.dumps(voice_profile or {}).lower()
        is_direct_voice = any(token in voice_blob for token in ["direct", "punchy", "intense", "high"])

        if policy.kind == "creator_journey":
            reason = _extract_journey_reason(
                " ".join(part for part in [existing, value, str(candidate.answer_text or "").strip()] if part),
                creator_name,
            )
            focus = _normalize_creator_start_focus(_extract_timeline_focus(question) or subject)
            if reason:
                if reason.lower().startswith("because ") or reason.lower().startswith("after "):
                    reason_text = reason
                else:
                    reason_text = f"because {reason}"
                if focus and focus not in {"it", "this", "that", "one"}:
                    return f"I got into {focus} {reason_text}."
                return f"I got into it {reason_text}."
            return ""

        if fact_field in {"publication_date", "launch_date", "start_date"} and value:
            if re.search(rf"\b({MONTH_PATTERN})\s+\d{{1,2}},\s+\d{{4}}\b", value, re.IGNORECASE):
                return _render_timeline_sentence(question, subject=subject, value=value, is_direct_voice=is_direct_voice)
            if re.search(rf"\b({MONTH_PATTERN})\s+\d{{4}}\b", value, re.IGNORECASE):
                return _render_timeline_sentence(question, subject=subject, value=value, is_direct_voice=is_direct_voice)
            if re.search(r"\b(20\d{2}|19\d{2})\b", value):
                return _render_timeline_sentence(question, subject=subject, value=value, is_direct_voice=is_direct_voice)

        if fact_field == "public_fact" and policy.kind == "creator_start_timeline" and value and not _looks_like_bibliographic_timeline_result(value):
            if re.search(r"\b(20\d{2}|19\d{2})\b", value):
                return _render_timeline_sentence(question, subject=subject, value=value, is_direct_voice=is_direct_voice)

        if fact_field == "full_name" and value:
            return f"My full name is {value}."

        if fact_field == "price" and value:
            return f"I've got {subject} at {value} right now."

        if fact_field in {"followers", "subscribers", "students", "members"} and value:
            label = fact_field.replace("_", " ")
            if label.endswith("s"):
                return f"I'm at {value} {label} right now."
            return f"I'm at {value} {label} right now."

        if fact_field == "latest_episode" and value:
            return f"My latest episode is {value}."

        if entity_type == "book" and any(token in lowered_question for token in TIMELINE_TOKENS) and value:
            phrase = "put out" if is_direct_voice else "published"
            return f"I {phrase} {subject} in {value}."

        return existing or str(candidate.answer_text or "").strip()

    def _cached_fact_to_evidence(self, cached_fact) -> List[Dict[str, Any]]:
        if not cached_fact:
            return []
        return [
            {
                "text": cached_fact.fact_value,
                "source": "fact_registry",
                "url": cached_fact.source_url,
                "title": cached_fact.source_title or cached_fact.entity_subject,
                "sim": float(cached_fact.confidence or 0.85),
            }
        ]

    def _cache_structured_fact(
        self,
        creator_id: int,
        entity_subject: str,
        entity_type: str,
        fact_field: str,
        candidate: StructuredFactCandidate,
        freshness_required: str,
        *,
        cache_key: str = "",
    ) -> None:
        if not candidate:
            return
        if cache_key:
            _set_hot_fact(cache_key, candidate)
        fact_registry.upsert_fact(
            creator_id,
            entity_subject=entity_subject or candidate.subject,
            entity_type=entity_type,
            fact_field=fact_field or candidate.fact_field,
            fact_value=candidate.value or candidate.answer_text,
            source_url=candidate.source_url,
            source_title=candidate.source_title,
            source_snippet=candidate.answer_text or candidate.value,
            confidence=candidate.confidence,
            freshness=freshness_required or "low",
            metadata={"cached_from": "personal_bio_service_structured_fact"},
        )

    def _cache_public_fact_answer(
        self,
        creator_id: int,
        entity_subject: str,
        entity_type: str,
        fact_field: str,
        answer_text: str,
        evidence: List[Dict[str, Any]],
        freshness_required: str,
    ) -> None:
        if not entity_subject or not fact_field or not answer_text:
            return
        primary = (evidence or [{}])[0] or {}
        fact_registry.upsert_fact(
            creator_id,
            entity_subject=entity_subject,
            entity_type=entity_type,
            fact_field=fact_field,
            fact_value=answer_text,
            source_url=str(primary.get("url") or ""),
            source_title=str(primary.get("title") or ""),
            source_snippet=str(primary.get("text") or ""),
            confidence=float(primary.get("sim") or 0.85),
            freshness=freshness_required or "low",
            metadata={"cached_from": "personal_bio_service"},
        )

    def _needs_more_evidence(self, facts: List[Dict[str, Any]]) -> bool:
        if not facts: return True
        max_sim = max(f["sim"] for f in facts) if facts else 0
        if max_sim < 0.75: return True
        return False

    def _evidence_blob(self, evidence: List[Dict[str, Any]]) -> str:
        return " ".join(str(item.get("text") or "") for item in evidence if item.get("text"))

    def _extract_structured_fact_candidate(
        self,
        question: str,
        evidence: List[Dict[str, Any]],
        fact_field: str,
        entity_subject: str,
    ) -> Optional[StructuredFactCandidate]:
        if not evidence:
            return None
        blob = self._evidence_blob(evidence)
        if not blob.strip():
            return None

        primary = (evidence or [{}])[0] or {}
        source_url = str(primary.get("url") or "")
        source_title = str(primary.get("title") or "")
        subject = str(entity_subject or self._extract_subject(question, evidence) or "It").strip() or "It"
        lowered_question = str(question or "").lower()
        policy = classify_creator_fact_query(question, query_goal="timeline_lookup" if _looks_like_timeline_question(question) else "")

        if policy.kind == "creator_journey":
            return None

        if policy.kind == "creator_start_timeline" and _looks_like_bibliographic_timeline_result(blob):
            return None

        if fact_field in {"publication_date", "launch_date", "public_fact", "start_date"} and any(token in lowered_question for token in TIMELINE_TOKENS):
            full_date = re.search(rf"\b({MONTH_PATTERN})\s+\d{{1,2}},\s+\d{{4}}\b", blob, re.IGNORECASE)
            month_year = re.search(rf"\b({MONTH_PATTERN})\s+\d{{4}}\b", blob, re.IGNORECASE)
            year = re.search(r"\b(20\d{2}|19\d{2})\b", blob)
            candidate_field = "start_date" if policy.kind == "creator_start_timeline" else (fact_field or "publication_date")
            if full_date:
                value = full_date.group(0)
                return StructuredFactCandidate(
                    fact_field=candidate_field,
                    subject=subject,
                    value=value,
                    answer_text=_render_timeline_sentence(question, subject=subject, value=value),
                    source_url=source_url,
                    source_title=source_title,
                    confidence=0.96,
                )
            if month_year:
                value = month_year.group(0)
                return StructuredFactCandidate(
                    fact_field=candidate_field,
                    subject=subject,
                    value=value,
                    answer_text=_render_timeline_sentence(question, subject=subject, value=value),
                    source_url=source_url,
                    source_title=source_title,
                    confidence=0.93,
                )
            if year:
                value = year.group(1)
                return StructuredFactCandidate(
                    fact_field=candidate_field,
                    subject=subject,
                    value=value,
                    answer_text=_render_timeline_sentence(question, subject=subject, value=value),
                    source_url=source_url,
                    source_title=source_title,
                    confidence=0.9,
                )

        if fact_field == "full_name":
            value = _extract_fact_value_from_text("full_name", blob)
            if value:
                return StructuredFactCandidate(
                    fact_field="full_name",
                    subject=subject,
                    value=value,
                    answer_text=f"My full name is {value}.",
                    source_url=source_url,
                    source_title=source_title,
                    confidence=0.94,
                )

        if fact_field == "price":
            match = re.search(r"\$\s?\d[\d,]*(?:\.\d{2})?", blob)
            if match:
                value = match.group(0)
                return StructuredFactCandidate(
                    fact_field="price",
                    subject=subject,
                    value=value,
                    answer_text=f"{subject} is listed at {value}.",
                    source_url=source_url,
                    source_title=source_title,
                    confidence=0.92,
                )

        if fact_field in {"followers", "latest_episode", "valuation", "net_worth"}:
            count_match = re.search(r"\b\d[\d,]*(?:\.\d+)?(?:\s?[kKmM])?\b", blob)
            if count_match:
                value = count_match.group(0)
                label = fact_field.replace("_", " ")
                return StructuredFactCandidate(
                    fact_field=fact_field,
                    subject=subject,
                    value=value,
                    answer_text=f"My {label} is {value}.",
                    source_url=source_url,
                    source_title=source_title,
                    confidence=0.88,
                )

        return None

    def _extract_subject(self, question: str, evidence: List[Dict[str, Any]]) -> str:
        patterns = [
            re.compile(r"(?:when|where|what year|what date|which month)\s+(?:was|did)\s+(.+?)\s+(?:published|launch(?:ed)?|release(?:d)?|come out)", re.IGNORECASE),
            re.compile(r"(?:when)\s+(?:did)\s+(?:you|u)\s+write\s+(.+)", re.IGNORECASE),
            re.compile(r"(?:when)\s+(?:did)\s+(?:you|u)\s+(?:start|begin|get\s+into)\s+(.+)", re.IGNORECASE),
            re.compile(r"how\s+long\s+(?:have|has)\s+(?:you|u)\s+been\s+(.+)", re.IGNORECASE),
            re.compile(r"where can i (?:buy|get|find|purchase)\s+(.+)", re.IGNORECASE),
        ]
        normalized_question = re.sub(r"\s+", " ", str(question or "")).strip(" ?!.")
        for pattern in patterns:
            match = pattern.search(normalized_question)
            if match:
                subject = re.sub(r"\s+", " ", match.group(1)).strip(" \"'")
                if subject:
                    return subject

        for item in evidence:
            title = str(item.get("title") or "").strip()
            if title:
                return title
        return ""

    def _answer_entity_confirmation(self, entity: Dict[str, Any]) -> str:
        return creator_entity_service.describe_entity_identity(entity) or "I do."

    def _answer_entity_availability(self, entity: Dict[str, Any]) -> str:
        entity_name = str(entity.get("name") or "").strip()
        entity_type = str(entity.get("type") or "entity").lower()
        official_urls = [str(url or "").strip() for url in (entity.get("official_urls") or []) if str(url or "").strip()]
        if not official_urls:
            return "I want to point you to the right place on that. Check my official website or verified profile links for the current listing."
        primary_url = official_urls[0]
        if entity_type == "profile":
            return f"You can find me on {primary_url}."
        if entity_type == "website":
            return f"My official site is {primary_url}."
        if entity_name:
            return f"You can find {entity_name} here: {primary_url}."
        return f"You can find it here: {primary_url}."

    def _answer_public_creator_fact(
        self,
        question: str,
        evidence: List[Dict[str, Any]],
        creator_name: str,
        *,
        entity: Optional[Dict[str, Any]] = None,
        voice_profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        blob = self._evidence_blob(evidence)
        lowered_question = str(question or "").lower()
        subject = str((entity or {}).get("name") or self._extract_subject(question, evidence) or "").strip()
        subject = subject or "It"
        voice_blob = json.dumps(voice_profile or {}).lower()
        policy = classify_creator_fact_query(question, entity_type=str((entity or {}).get("type") or ""), query_goal="timeline_lookup" if _looks_like_timeline_question(question) else "")
        is_direct_voice = any(token in voice_blob for token in ["direct", "punchy", "intense", "high"])

        full_date = re.search(rf"\b({MONTH_PATTERN})\s+\d{{1,2}},\s+\d{{4}}\b", blob, re.IGNORECASE)
        month_year = re.search(rf"\b({MONTH_PATTERN})\s+\d{{4}}\b", blob, re.IGNORECASE)
        year = re.search(r"\b(20\d{2}|19\d{2})\b", blob)

        if policy.kind == "creator_journey":
            reason = _extract_journey_reason(blob, creator_name)
            focus = _normalize_creator_start_focus(_extract_timeline_focus(question) or subject)
            if reason:
                if reason.lower().startswith("because ") or reason.lower().startswith("after "):
                    reason_text = reason
                else:
                    reason_text = f"because {reason}"
                if focus and focus not in {"it", "this", "that", "one"}:
                    return f"I got into {focus} {reason_text}."
                return f"I got into it {reason_text}."
            return ""

        if any(token in lowered_question for token in TIMELINE_TOKENS):
            if policy.kind == "creator_start_timeline" and _looks_like_bibliographic_timeline_result(blob):
                return ""
            if full_date:
                return _render_timeline_sentence(question, subject=subject, value=full_date.group(0), is_direct_voice=is_direct_voice)
            if month_year:
                return _render_timeline_sentence(question, subject=subject, value=month_year.group(0), is_direct_voice=is_direct_voice)
            if year:
                return _render_timeline_sentence(question, subject=subject, value=year.group(1), is_direct_voice=is_direct_voice)

        if any(token in lowered_question for token in ["where can i buy", "where do i buy", "where can i get", "where can i find", "purchase"]):
            domains = {
                (item.get("url") or "").lower(): item.get("url")
                for item in evidence
                if item.get("url")
            }
            mentions_amazon = "amazon" in blob.lower() or any("amazon." in key for key in domains)
            mentions_audible = "audible" in blob.lower() or any("audible." in key for key in domains)
            mentions_publisher = any(
                marker in blob.lower()
                for marker in ["penguin", "publisher", "harper", "random house", "simon", "press"]
            )
            options = []
            if mentions_amazon:
                options.append("Amazon")
            if mentions_audible:
                options.append("Audible")
            if mentions_publisher:
                options.append("the publisher page")
            if not options:
                options = ["Amazon", "Audible", "the publisher page"]
            if len(options) == 1:
                option_text = options[0]
            elif len(options) == 2:
                option_text = f"{options[0]} or {options[1]}"
            else:
                option_text = f"{options[0]}, {options[1]}, or {options[2]}"
            return f"You can get {subject} on {option_text}."

        return ""

    def _synthesize_public_fact_answer(
        self,
        question: str,
        evidence: List[Dict[str, Any]],
        voice_profile: Dict[str, Any],
        creator_name: str,
    ) -> str:
        if not evidence:
            return ""

        policy = classify_creator_fact_query(question)
        if policy.kind == "creator_journey" and not _extract_journey_reason(self._evidence_blob(evidence), creator_name):
            return ""

        evidence_text = "\n".join([f"- [{e.get('source', 'unknown')}]: {e.get('text', '')[:300]}" for e in evidence])
        vp_json = json.dumps(voice_profile, indent=2)

        system_prompt = f"""
You are {creator_name}.

This is a public factual question about your own public work, products, books, releases, platforms, stats, or creator journey.

Voice Profile:
{vp_json}

RULES:
1. Answer directly from the evidence in 1-2 sentences.
2. If the evidence contains a date, title, platform, availability detail, or a clear reason or motivation from your public story, lead with that concrete point.
3. Never say "I haven't talked about that publicly" about your own public work.
4. Never say "I don't have that in front of me" about your own book, product, or release.
5. Never invent facts. If the evidence is still insufficient, direct the user to a concrete official source.
6. Never mention evidence, sources, transcripts, search, verification steps, or that you pulled anything up.
7. Keep it natural and first-person, like a direct answer to the user.

Return JSON:
{{
  "answer": "string"
}}
"""
        user_prompt = f"""
User Question: {question}

Evidence:
{evidence_text}
"""
        try:
            resp = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=settings.FINAL_RESPONSE_MODEL,
                temperature=0.0,
                json_mode=True,
            )
            data = json.loads(resp)
            return str(data.get("answer") or "").strip()
        except Exception as e:
            logger.error(f"Public fact synthesis failed: {e}")
            return ""

    def _public_fact_fallback(
        self,
        question: str,
        creator_name: str,
        *,
        evidence_plan: Optional[Any] = None,
        entity: Optional[Dict[str, Any]] = None,
    ) -> str:
        lowered = str(question or "").lower()
        query_goal = str(getattr(evidence_plan, "query_goal", "") or "").lower()
        official_urls = [str(url or "").strip() for url in ((entity or {}).get("official_urls") or []) if str(url or "").strip()]
        policy = classify_creator_fact_query(question, entity_type=str((entity or {}).get("type") or ""), query_goal=query_goal)

        if _looks_like_catalog_question(lowered, query_goal):
            if query_goal == "entity_catalog_lookup" and official_urls:
                return f"I want to point you to the full current list. Start here: {official_urls[0]}"
            if "book" in lowered:
                return (
                    "I want to point you to the full current list. Check my Amazon author page or my official website "
                    "for the most up-to-date catalog."
                )
            return (
                "I want to point you to the full current list. Check my official website or verified profile links "
                "for the latest catalog."
            )
        if _looks_like_timeline_question(lowered, query_goal):
            if _is_publication_timeline_question(question):
                return (
                    "I want to give you the right date on that. Check my Amazon listing, Audible, "
                    "or the publisher page for the exact publication info."
                )
            timeline_focus = _extract_timeline_focus(question)
            if timeline_focus:
                return (
                    f"I don't want to fake the year on that. I couldn't pin down an exact date for when I started {timeline_focus}, so I won't make one up."
                )
            return (
                "I don't want to fake the year on that. I couldn't pin down an exact date yet, so I won't make one up."
            )
        if query_goal == "journey_lookup" or policy.kind == "creator_journey":
            journey_focus = _normalize_creator_start_focus(_extract_timeline_focus(question))
            if journey_focus:
                return (
                    f"I've talked about why I got into {journey_focus} in my content, but I couldn't pin down one clean public quote I'd trust enough to paraphrase."
                )
            return (
                "I've talked about that in my content, but I couldn't pin down one clean public quote I'd trust enough to paraphrase."
            )
        if query_goal in {"price_lookup"} or any(token in lowered for token in ["price", "pricing", "cost", "how much"]):
            return (
                "I want to give you the right pricing info there. Check my website or official checkout page for the current details."
            )
        if query_goal in {"availability_lookup", "resource_lookup"}:
            if official_urls:
                return f"I want to point you to the right place on that. Start here: {official_urls[0]}"
            return (
                "I want to point you to the right place on that. Check my official website, "
                "course page, or verified profile links for the current listing."
            )
        if query_goal in {"current_stat_lookup", "stat_lookup"} or any(token in lowered for token in ["followers", "subscribers", "members", "students", "employees"]):
            return (
                "I want to give you the right number on that. Check my live profiles or current public listings directly for the latest count."
            )
        return (
            "I want to give you the right answer on that. Check my official website, "
            "verified profiles, or the primary listing page for the exact current details."
        )

    def _synthesize_answer(
        self, 
        question: str, 
        evidence: List[Dict[str, Any]], 
        voice_profile: Dict[str, Any],
        creator_name: str,
        move: str,
        topic: str
    ) -> Dict[str, Any]:
        
        evidence_text = "\n".join([f"- [{e.get('source', 'unknown')}]: {e.get('text', '')[:300]}" for e in evidence])
        vp_json = json.dumps(voice_profile, indent=2)
        
        # Move specific guidance
        move_guidance = ""
        if move == "ANSWER_DIRECTLY":
            move_guidance = "Answer the question directly and concisely based on the evidence."
        elif move == "ANSWER_WITH_QUALIFIER":
            move_guidance = "Answer cautiously. Start with something like 'From what I've shared publicly...' or 'If I recall correctly...'"
        elif move == "DECLINE_PRIVATE":
            move_guidance = "Do NOT answer. Respectfully decline by saying you keep that part of your life private."
        elif move == "DEFLECT_WITH_HUMOR":
            move_guidance = "Do NOT answer. Make a short, creator-appropriate joke or playful remark and pivot away."
        elif move == "REFRAME_TO_DOMAIN":
            move_guidance = "Briefly acknowledge the question (if benign) but immediately turn it into a lesson or principle related to your domain (business/training)."
        elif move == "BOUNDARY_PUSHBACK":
            move_guidance = "Firmly refuse to answer. Don't be rude, but be very clear it's not something you share."
        elif move == "ASK_CLARIFY":
            move_guidance = "The question is too vague. Ask a short, creator-natural clarifying question to understand what they specifically want to know about you."

        system_prompt = f"""
You are {creator_name}. 
DECISION MOVE: {move}
TOPIC: {topic}

CONVERSATIONAL GOAL: {move_guidance}

Voice Profile:
{vp_json}

RULES:
1. MAX 3 sentences. No paragraphs. No lists.
2. NO system language, NO "AI", NO "Note:", NO "Based on content".
3. NEVER invent facts. If the move is to answer but evidence is missing, pivot to DECLINE_PRIVATE.
4. Stay strictly in the creator's identity.

Move-Specific Logic:
- DECLINE_PRIVATE: "I keep that side of my life private." (or creator equivalent)
- UNCERTAINTY: "I haven't really talked about that publicly, so I wouldn't want to guess."
- NO DISCLAIMERS.

OUTPUT format (JSON):
{{
    "answer": "string (in creator voice)",
    "confidence": "HIGH" | "MEDIUM" | "LOW",
    "reasoning": "internal move check"
}}
"""
        user_prompt = f"""
User Question: {question}

Available Evidence:
{evidence_text}

Draft your response following the DECISION MOVE: {move}
"""
        try:
            resp = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model=settings.FINAL_RESPONSE_MODEL,
                temperature=0.0,
                json_mode=True
            )
            data = json.loads(resp)
            return {
                "answer": data.get("answer", "I haven't really talked about that publicly."),
                "confidence": data.get("confidence", "LOW"),
                "sources": evidence
            }
        except Exception as e:
            logger.error(f"Personal bio synthesis failed: {e}")
            return {
                "answer": self._generate_uncertain_response(voice_profile),
                "confidence": "LOW",
                "sources": []
            }

    def _generate_uncertain_response(self, voice_profile: Dict[str, Any]) -> str:
        return "I haven't really talked about that publicly, so I wouldn't want to guess."

personal_bio_service = PersonalBioService()
