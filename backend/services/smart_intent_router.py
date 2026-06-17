from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional

import backend.rag as rag
from backend.settings import settings
from backend.services.creator_fact_policy import (
    classify_creator_fact_query,
    is_creator_journey_turning_point_question,
)
from backend.services.conversation_memory_packet import (
    build_conversation_memory_packet as build_clean_conversation_memory_packet,
)

logger = logging.getLogger(__name__)


_CACHE_TTL_SECONDS = 600
_CACHE_MAX_ITEMS = 256
_CACHE: Dict[str, tuple[float, "SmartIntentDecision"]] = {}

_ROUTES = {"ROUTE_0_GREETING", "ROUTE_1_SMALL_TALK", "ROUTE_2_TASK"}
_QUESTION_TYPES = {
    "greeting",
    "small_talk",
    "domain_advice",
    "creator_fact",
    "personal_bio",
    "user_memory",
    "private_sensitive",
    "self_harm",
    "harmful_request",
    "out_of_scope",
    "meta",
    "opinion",
}
_QUERY_GOALS = {
    "general",
    "journey_lookup",
    "timeline_lookup",
    "role_lookup",
    "identity_lookup",
    "entity_confirmation",
    "entity_overview",
    "entity_catalog_lookup",
    "availability_lookup",
    "resource_lookup",
    "price_lookup",
    "stat_lookup",
    "current_stat_lookup",
    "crisis_support",
}
_SOURCE_POLICIES = {"none", "cite_if_used", "attach_resource", "must_cite"}

@dataclass(frozen=True)
class SmartIntentDecision:
    intent: str = "general"
    route: str = "ROUTE_2_TASK"
    question_type: str = "domain_advice"
    query_goal: str = "general"
    needs_memory: bool = False
    needs_corpus: bool = True
    needs_web: bool = False
    needs_sources: bool = False
    is_creator_fact: bool = False
    entity_subject: str = ""
    query_plan: List[str] = field(default_factory=list)
    resolved_user_message: str = ""
    source_policy: str = "none"
    response_mode: str = "answer"
    confidence: float = 0.0
    reason: str = ""
    source: str = "fallback"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", str(question or "").strip().lower())


def _history_digest(history: Optional[List[Dict[str, str]]]) -> str:
    recent = []
    for item in (history or [])[-4:]:
        resource_titles: List[str] = []
        for bucket in ("cards", "citations"):
            values = item.get(bucket) or []
            if isinstance(values, list):
                for value in values[:3]:
                    if isinstance(value, dict):
                        title = re.sub(r"\s+", " ", str(value.get("title") or value.get("text") or "")).strip()
                        if title:
                            resource_titles.append(title[:120])
        recent.append(
            {
                "role": str(item.get("role") or "")[:16],
                "content": re.sub(r"\s+", " ", str(item.get("content") or item.get("text") or ""))[:240],
                "resources": resource_titles[:4],
            }
        )
    return hashlib.sha1(json.dumps(recent, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _cache_key(question: str, history: Optional[List[Dict[str, str]]]) -> str:
    return f"{_normalize_question(question)}::{_history_digest(history)}"


def _get_cached(key: str) -> Optional[SmartIntentDecision]:
    cached = _CACHE.get(key)
    if not cached:
        return None
    expires_at, decision = cached
    if expires_at < time.time():
        _CACHE.pop(key, None)
        return None
    return decision


def _set_cached(key: str, decision: SmartIntentDecision) -> None:
    if len(_CACHE) >= _CACHE_MAX_ITEMS:
        oldest_key = min(_CACHE.items(), key=lambda item: item[1][0])[0]
        _CACHE.pop(oldest_key, None)
    _CACHE[key] = (time.time() + _CACHE_TTL_SECONDS, decision)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return parsed


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def _safe_string_list(value: Any, *, limit: int = 5, item_limit: int = 140) -> List[str]:
    if not isinstance(value, list):
        return []
    items: List[str] = []
    for item in value:
        text = re.sub(r"\s+", " ", str(item or "").strip())
        if text and text not in items:
            items.append(text[:item_limit])
        if len(items) >= limit:
            break
    return items


def _safe_text(value: Any, *, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].strip()
    return text


_USER_BUSINESS_TERMS = {
    "business",
    "agency",
    "startup",
    "company",
    "client",
    "clients",
    "customer",
    "customers",
    "lead",
    "leads",
    "sales",
    "marketing",
    "offer",
    "offers",
}
_USER_BUSINESS_PROBLEM_TERMS = {
    "convert",
    "converting",
    "conversion",
    "close",
    "closing",
    "struggle",
    "struggling",
    "stuck",
    "start",
    "starting",
    "scale",
    "scaling",
    "grow",
    "growing",
    "sell",
    "selling",
    "paying",
}
_USER_SELF_TERMS = {"i", "im", "i'm", "ive", "i've", "me", "my", "we", "our", "us"}
_CREATOR_REF_TERMS = {"you", "your", "yours", "u", "ur"}
_BUSINESS_FOLLOWUP_ASSISTANT_PATTERNS = [
    re.compile(r"\b(?:sales\s+script|winging\s+the\s+calls?|close\s+rate|qualified\s+leads?|got\s+on\s+a\s+call|said\s+yes|offer\s+you\s+are\s+pitching)\b", re.IGNORECASE),
    re.compile(r"\b(?:are|were)\s+you\s+(?:using|running|following)\b.*\b(?:script|calls?|sales)\b", re.IGNORECASE),
]
_BUSINESS_FOLLOWUP_USER_PATTERNS = [
    re.compile(r"\b(?:i'?m|im|we'?re|were|i\s+am|we\s+are)\s+(?:using|running|following|winging)\b.*\b(?:sales\s+)?script\b", re.IGNORECASE),
    re.compile(r"\b(?:using|running|following)\s+(?:a\s+)?(?:sales\s+)?script\b", re.IGNORECASE),
    re.compile(r"\b(?:just\s+)?winging\s+(?:it|the\s+calls?)\b", re.IGNORECASE),
    re.compile(r"\b(?:yes|yeah|yep|nah|nope|no)\b", re.IGNORECASE),
]

_PUBLIC_RELATIONSHIP_RE = re.compile(
    r"\b(?:"
    r"(?:do|does|did)\s+(?:you|u|he|she|they)\s+have\s+(?:a\s+)?"
    r"(?:wife|wifey|husband|girlfriend|boyfriend|partner|spouse|missus|misus|mrs|missis)"
    r"|(?:are|is|was|were)\s+(?:you|u|he|she|they)\s+(?:married|dating|single)"
    r"|who(?:'s|\s+is|s)?\s+(?:your|ur|his|her|their)\s+"
    r"(?:wife|wifey|husband|girlfriend|boyfriend|partner|spouse|missus|misus|mrs|missis)"
    r")\b",
    re.IGNORECASE,
)

_SOURCE_REQUEST_RE = re.compile(
    r"\b(?:source|sources|proof|prove|citation|cite|reference|references|link|links|where\s+(?:did|do)\s+(?:you|u)\s+(?:get|find)|show\s+me)\b",
    re.IGNORECASE,
)
_RESOURCE_BREAKDOWN_FOLLOWUP_RE = re.compile(
    r"\b(?:deep|full|detailed|proper|complete)?\s*(?:break\s*down|breakdown|summary|summari[sz]e|recap|"
    r"walk\s+(?:me\s+)?through|go\s+through|takeaways?|main\s+points|key\s+points|lessons?)\b"
    r"|\b(?:don'?t|dont|do\s+not|can't|cant)\s+(?:wanna|want\s+to|have\s+time\s+to)?\s*"
    r"(?:watch|listen|read)\b",
    re.IGNORECASE,
)


def _looks_like_public_relationship_lookup(question: str) -> bool:
    return bool(_PUBLIC_RELATIONSHIP_RE.search(str(question or "")))


def _explicitly_requests_source(question: str) -> bool:
    return bool(_SOURCE_REQUEST_RE.search(str(question or "")))


def _latest_resource_title_from_history(history: Optional[List[Dict[str, str]]]) -> str:
    for item in reversed(list(history or [])[-8:]):
        if str(item.get("role") or "").lower() != "assistant":
            continue
        for bucket in ("cards", "citations"):
            values = item.get(bucket) or []
            if isinstance(values, list):
                for value in values:
                    if not isinstance(value, dict):
                        continue
                    title = _safe_text(value.get("title"), limit=140).strip(" \"'.,:;!?")
                    if title:
                        return title
        text = str(item.get("content") or item.get("text") or "")
        for quoted in re.findall(r'"([^"\n]{4,140})"', text):
            clean = _safe_text(quoted, limit=140).strip(" \"'.,:;!?")
            if clean:
                return clean
    return ""


def _looks_like_resource_breakdown_followup(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> bool:
    if not _RESOURCE_BREAKDOWN_FOLLOWUP_RE.search(str(question or "")):
        return False
    return bool(_latest_resource_title_from_history(history) or re.search(
        r"\b(?:video|episode|podcast|resource|post|reel|clip|source|link|watch|listen|read)\b",
        _latest_assistant_history_text(history),
        re.IGNORECASE,
    ))


def _looks_like_user_business_problem(question: str) -> bool:
    words = set(re.findall(r"[a-z0-9']+", str(question or "").lower()))
    if not (words & _USER_SELF_TERMS):
        return False
    if words & _CREATOR_REF_TERMS and not (words & _USER_BUSINESS_TERMS):
        return False
    return bool((words & _USER_BUSINESS_TERMS) and (words & _USER_BUSINESS_PROBLEM_TERMS))


def _latest_assistant_history_text(history: Optional[List[Dict[str, str]]]) -> str:
    for item in reversed(history or []):
        if str(item.get("role") or "").lower() == "assistant":
            return re.sub(r"\s+", " ", str(item.get("content") or item.get("text") or "")).strip()
    return ""


def _latest_user_history_text(history: Optional[List[Dict[str, str]]]) -> str:
    for item in reversed(history or []):
        if str(item.get("role") or "").lower() == "user":
            return re.sub(r"\s+", " ", str(item.get("content") or item.get("text") or "")).strip()
    return ""


def _last_question_from_text(text: str) -> str:
    questions = re.findall(r"([^?]{6,180}\?)", str(text or ""))
    if not questions:
        return ""
    return _safe_text(questions[-1], limit=180)


def _split_memory_sentences(text: str, *, limit: int = 3) -> List[str]:
    out: List[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", str(text or "")):
        clean = _safe_text(sentence, limit=220)
        if len(clean.split()) < 5:
            continue
        if re.search(r"\b(?:copy|source|sources|link below|attached below)\b", clean, re.IGNORECASE):
            continue
        out.append(clean)
        if len(out) >= limit:
            break
    return out


def _extract_memory_entities(*texts: str, limit: int = 6) -> List[str]:
    entities: List[str] = []
    seen = set()

    def _add(value: str) -> None:
        clean = _safe_text(value, limit=90).strip(" \"'.,:;!?")
        if not clean or len(clean) < 3:
            return
        key = clean.lower()
        if key in seen:
            return
        seen.add(key)
        entities.append(clean)

    for text in texts:
        raw = str(text or "")
        for quoted in re.findall(r'"([^"]{3,90})"', raw):
            _add(quoted)
        for titled in re.findall(
            r"\b([A-Z][A-Za-z0-9$&'.-]+(?:\s+[A-Z][A-Za-z0-9$&'.-]+){1,7})\b",
            raw,
        ):
            if titled.lower() in {"You Tube", "Apple Podcasts", "The Game"}:
                continue
            _add(titled)
        if len(entities) >= limit:
            break
    return entities[:limit]


def _looks_like_contextual_turnaround_followup(question: str, assistant_text: str) -> bool:
    current = str(question or "")
    assistant = str(assistant_text or "")
    if not re.search(r"\bturn\s+(?:it|that|this|things)\s+around\b", current, re.IGNORECASE):
        return False
    return bool(
        re.search(
            r"\b(?:turn(?:ed|ing)?\s+(?:my|your|his|her|their|a)?\s*(?:life|path|career|future)?\s*around|journey|background|story|dark\s+place|rock\s+bottom|legal\s+system|convict|stolen\s+cars|trauma|changed\s+(?:my|his|her|their)\s+life)\b",
            assistant,
            re.IGNORECASE,
        )
    )


def _build_conversation_memory_packet(question: str, history: Optional[List[Dict[str, str]]]) -> Dict[str, Any]:
    """Give Gemini a compact, explicit packet before it classifies the turn."""
    return build_clean_conversation_memory_packet(question, history or [])


def _looks_like_user_business_contextual_followup(question: str, history: Optional[List[Dict[str, str]]]) -> bool:
    assistant_text = _latest_assistant_history_text(history).lower()
    if not assistant_text:
        return False
    if not any(pattern.search(assistant_text) for pattern in _BUSINESS_FOLLOWUP_ASSISTANT_PATTERNS):
        return False

    normalized = _normalize_question(question)
    words = set(re.findall(r"[a-z0-9']+", normalized))
    if len(words) > 18:
        return False
    if any(pattern.search(normalized) for pattern in _BUSINESS_FOLLOWUP_USER_PATTERNS):
        return True
    return bool(words & _USER_SELF_TERMS and words & {"script", "calls", "call", "leads", "sales", "close", "closing"})


def _resolve_business_followup_message(question: str, history: Optional[List[Dict[str, str]]]) -> str:
    assistant_text = _latest_assistant_history_text(history)
    normalized = _normalize_question(question)
    if "sales script" in normalized or "script" in normalized:
        return "The user says they are using a sales script for their lead-conversion calls."
    if "winging" in normalized:
        return "The user says they are winging their sales calls instead of using a script."
    if normalized in {"yes", "yeah", "yep"}:
        return f"The user answered yes to the previous sales-process question: {assistant_text}"
    if normalized in {"no", "nah", "nope"}:
        return f"The user answered no to the previous sales-process question: {assistant_text}"
    return _safe_text(question)


def _coerce_decision(
    raw: Dict[str, Any],
    *,
    source: str,
    question: str = "",
    history: Optional[List[Dict[str, str]]] = None,
) -> SmartIntentDecision:
    route = str(raw.get("route") or "ROUTE_2_TASK").strip()
    if route not in _ROUTES:
        route = "ROUTE_2_TASK"

    question_type = str(raw.get("question_type") or "domain_advice").strip().lower()
    if question_type not in _QUESTION_TYPES:
        question_type = "domain_advice"

    query_goal = str(raw.get("query_goal") or "general").strip().lower()
    if query_goal not in _QUERY_GOALS:
        query_goal = "general"

    response_mode = str(raw.get("response_mode") or "answer").strip().lower()
    if response_mode not in {"answer", "ask_clarifying_question", "boundary", "small_talk", "crisis"}:
        response_mode = "answer"
    public_relationship_lookup = _looks_like_public_relationship_lookup(question)
    user_business_problem = _looks_like_user_business_problem(question)
    user_business_followup = _looks_like_user_business_contextual_followup(question, history)
    resource_breakdown_followup = _looks_like_resource_breakdown_followup(question, history)
    resource_title = _latest_resource_title_from_history(history) if resource_breakdown_followup else ""
    policy = classify_creator_fact_query(question)
    if public_relationship_lookup and not (user_business_problem or user_business_followup):
        route = "ROUTE_2_TASK"
        question_type = "personal_bio"
        query_goal = "identity_lookup"
        response_mode = "answer"
    if (
        policy.kind == "creator_journey"
        or is_creator_journey_turning_point_question(question)
        or _looks_like_contextual_turnaround_followup(question, _latest_assistant_history_text(history))
    ) and not (user_business_problem or user_business_followup):
        route = "ROUTE_2_TASK"
        question_type = "personal_bio"
        query_goal = "journey_lookup"
        response_mode = "answer"
    if (user_business_problem or user_business_followup) and question_type not in {"self_harm", "harmful_request"}:
        route = "ROUTE_2_TASK"
        question_type = "domain_advice"
        query_goal = "general"
        response_mode = "answer"
    if resource_breakdown_followup and not (user_business_problem or user_business_followup):
        route = "ROUTE_2_TASK"
        question_type = "domain_advice"
        query_goal = "resource_lookup"
        response_mode = "answer"
    is_light_route = route in {"ROUTE_0_GREETING", "ROUTE_1_SMALL_TALK"}
    is_crisis_route = question_type == "self_harm"
    if is_crisis_route:
        route = "ROUTE_2_TASK"
        query_goal = "crisis_support"
        response_mode = "crisis"
    creator_fact_goals = {
        "journey_lookup",
        "timeline_lookup",
        "role_lookup",
        "identity_lookup",
        "entity_confirmation",
        "entity_overview",
        "entity_catalog_lookup",
        "availability_lookup",
        "price_lookup",
        "stat_lookup",
        "current_stat_lookup",
    }
    if query_goal in creator_fact_goals and not is_light_route and not is_crisis_route:
        route = "ROUTE_2_TASK"
    force_web_goals = {
        "journey_lookup",
        "timeline_lookup",
        "role_lookup",
        "identity_lookup",
        "entity_catalog_lookup",
        "price_lookup",
        "stat_lookup",
        "current_stat_lookup",
    }
    forced_needs_web = query_goal in force_web_goals and not is_light_route and not is_crisis_route
    forced_needs_sources = forced_needs_web or (
        query_goal in {"availability_lookup"} and _safe_bool(raw.get("needs_web"))
    )
    forced_creator_fact = False if (user_business_problem or user_business_followup) else (
        query_goal in creator_fact_goals or _safe_bool(raw.get("is_creator_fact"))
    )
    needs_corpus = _safe_bool(raw.get("needs_corpus", True))
    if query_goal in {"price_lookup", "stat_lookup", "current_stat_lookup", "identity_lookup"}:
        needs_corpus = _safe_bool(raw.get("needs_corpus", False))
    if resource_breakdown_followup and not (user_business_problem or user_business_followup):
        needs_corpus = True
    source_policy = str(raw.get("source_policy") or "").strip().lower()
    if source_policy not in _SOURCE_POLICIES:
        source_policy = "none"
    if user_business_problem or user_business_followup:
        source_policy = "none"
    elif public_relationship_lookup and not _explicitly_requests_source(question):
        source_policy = "cite_if_used"
    elif is_light_route or is_crisis_route or response_mode in {"small_talk", "crisis", "boundary"}:
        source_policy = "none"
    elif query_goal == "resource_lookup":
        source_policy = "attach_resource"
    elif forced_needs_sources or _safe_bool(raw.get("needs_sources")):
        source_policy = "must_cite" if query_goal in {
            "timeline_lookup",
            "price_lookup",
            "stat_lookup",
            "current_stat_lookup",
            "role_lookup",
            "identity_lookup",
            "journey_lookup",
        } else "cite_if_used"

    query_plan = _safe_string_list([] if public_relationship_lookup else raw.get("query_plan"))
    resolved_user_message = _safe_text(raw.get("resolved_user_message"))
    if resource_breakdown_followup and not (user_business_problem or user_business_followup):
        resolved_user_message = (
            f'Give me a detailed breakdown of your video "{resource_title}".'
            if resource_title
            else "Give me a detailed breakdown of the previously mentioned creator resource."
        )
        if resource_title:
            anchored_query = f'Give me a detailed breakdown of your video "{resource_title}".'
            query_plan = [anchored_query, *[q for q in query_plan if resource_title.lower() in q.lower()]][:5]

    return SmartIntentDecision(
        intent=str(raw.get("intent") or "general").strip().lower()[:80],
        route=route,
        question_type=question_type,
        query_goal=query_goal,
        needs_memory=True if (user_business_problem or user_business_followup) else (False if route == "ROUTE_0_GREETING" or is_crisis_route else _safe_bool(raw.get("needs_memory"))),
        needs_corpus=True if (user_business_problem or user_business_followup) else (False if is_light_route or is_crisis_route else needs_corpus),
        needs_web=False if user_business_problem or user_business_followup or is_light_route or is_crisis_route else (_safe_bool(raw.get("needs_web")) or forced_needs_web or resource_breakdown_followup),
        needs_sources=False if user_business_problem or user_business_followup or is_light_route or is_crisis_route else (_safe_bool(raw.get("needs_sources")) or forced_needs_sources or resource_breakdown_followup),
        is_creator_fact=forced_creator_fact,
        entity_subject="" if public_relationship_lookup else str(raw.get("entity_subject") or "").strip()[:120],
        query_plan=query_plan,
        resolved_user_message=_resolve_business_followup_message(question, history) if user_business_followup else resolved_user_message,
        source_policy=source_policy,
        response_mode=response_mode,
        confidence=_safe_float(raw.get("confidence"), 0.0),
        reason=str(raw.get("reason") or "").strip()[:240],
        source=source,
    )


def _json_loads(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
    parsed = json.loads(text)
    return parsed if isinstance(parsed, dict) else {}


def should_use_smart_router(
    question: str,
    *,
    route: str = "",
    question_type: str = "",
    rule_intent: str = "",
    history: Optional[List[Dict[str, str]]] = None,
) -> bool:
    # Keep deterministic fast lanes fast. Gemini is still the semantic brain for
    # task/public-fact turns, and it can repair a light turn if legacy routing
    # incorrectly left it as ROUTE_2_TASK.
    if not _normalize_question(question):
        return False
    if route in {"ROUTE_0_GREETING", "ROUTE_1_SMALL_TALK"}:
        return False
    return True


def _build_prompt(question: str, history: Optional[List[Dict[str, str]]]) -> str:
    recent_history = [
        {
            "role": item.get("role"),
            "content": re.sub(r"\s+", " ", str(item.get("content") or item.get("text") or ""))[:300],
        }
        for item in (history or [])[-6:]
    ]
    return json.dumps(
        {
            "task": "Classify the user's latest message for a creator-style chat backend. Decide meaning from context, not keyword matching.",
            "latest_user_message": question,
            "conversation_memory_packet": _build_conversation_memory_packet(question, history),
            "recent_history": recent_history,
            "allowed_routes": sorted(_ROUTES),
            "allowed_question_types": sorted(_QUESTION_TYPES),
            "allowed_query_goals": sorted(_QUERY_GOALS),
            "intent_examples": {
                "greeting": "yoooo broskki",
                "casual_greeting": "yo ma g",
                "casual_check_in": "nothing much wbu",
                "creator_background": "who are you, what's your story like how did u get rich?",
                "creator_motivation": "what inspired you to start Acquisition.com, why didnt u just retire after scaling Gym Launch",
                "creator_turning_point_followup": "what made u turn your life around? (after a broad creator-background answer)",
                "creator_background_prompt_only": "what's your background, how did you get started?",
                "creator_timeline_fact": "when did u sell your business?",
                "creator_current_financial_stat": "whats Acquisition.com's revenue now?",
                "creator_public_money_question": "what's the most you've ever made though?",
                "creator_business_history": "what did u do in Gym Launch?",
                "publication_timeline": "when was Buy Back Your Time published?",
                "creator_role": "what firms do you run?",
                "user_memory": "what's my name?",
                "small_talk": "what have you been up to?",
                "self_harm": "i just been feeling suicidal lately",
                "harmful_request": "how do I hurt someone without getting caught",
                "out_of_scope": "teach me a random unrelated topic this creator does not cover",
                "resource_lookup": "what did u talk about in the night in the life of a rich trader in miami",
                "domain_advice": "how do I grow my audience?",
                "contextual_short_answer": "like 2 (after the assistant asked how many of the last 10 qualified calls closed)",
                "sales_process_followup": "im using a sales script (after the assistant asked whether the user uses a sales script or wings calls)",
            },
            "output_contract": {
                "intent": "short snake_case label",
                "route": "one allowed route",
                "question_type": "one allowed question type",
                "query_goal": "one allowed query goal",
                "needs_memory": "boolean",
                "needs_corpus": "boolean",
                "needs_web": "boolean",
                "needs_sources": "boolean",
                "is_creator_fact": "boolean",
                "entity_subject": "specific subject if any",
                "query_plan": "0-5 search queries if web/corpus lookup would help",
                "resolved_user_message": "context-aware rewrite of the latest user message; if it is a short answer to the previous question, include the answered variable",
                "source_policy": "none | cite_if_used | attach_resource | must_cite",
                "response_mode": "answer | ask_clarifying_question | boundary | small_talk | crisis",
                "confidence": "0 to 1",
                "reason": "one short internal reason",
            },
        },
        ensure_ascii=True,
    )


_SYSTEM_PROMPT = """You are a fast intent router for a creator chat app.
Return only compact JSON. Do not answer the user.
Use semantic intent, not brittle keyword rules.
Local fast-lane routers usually handle pure greetings and small talk before you are called. If a casual reply reaches you anyway, classify it correctly and do not over-search it.
Always infer the latest message from recent_history. If the user gives a short answer to the assistant's previous question, resolve it instead of treating it as a new vague request.
Use conversation_memory_packet before raw recent_history. It is the clean short-term memory packet: what the assistant just asked, what it just claimed, and what entities/resources were just mentioned. If the latest user message is a follow-up, preserve that target in resolved_user_message and query_plan instead of rediscovering or repeating the whole previous answer.
Example: if the assistant asked "Out of the last ten qualified leads that got on a call, how many said yes?" and the user says "like 2", set resolved_user_message="The user says about 2 of the last 10 qualified sales calls converted." Use question_type=domain_advice, needs_memory=true, needs_corpus=true, needs_web=false, needs_sources=false, source_policy=none.
If the assistant asked about the user's sales process and the user answers "im using a sales script", "yes", "no", or "just winging it", resolve that as a continuation of the user's business coaching thread. Use question_type=domain_advice, needs_memory=true, needs_corpus=true, needs_web=false, needs_sources=false, source_policy=none. Do not treat "sales script" as a public sales-statistics question.
If the user describes THEIR OWN business problem (e.g. "I've been getting leads but I don't know how to convert them, I run a marketing agency"), classify it as domain_advice. Never treat user-owned business context as the creator's private life, personal biography, or out-of-scope.
If the user gives THEIR OWN business metrics, CAC, acquisition cost, price they charge, margins, LTV, churn, MRR/ARR, revenue, conversion numbers, or cost per customer, keep it as domain_advice with query_goal=general, needs_memory=true, needs_corpus=true, needs_web=false, needs_sources=false, source_policy=none. Do not use price_lookup/stat_lookup unless the user asks for the creator's public price, the creator/company's public stats, or current/latest public facts.
Use creator_business_history/journey for questions about what the creator did inside a company.
Use publication_timeline only when the user asks for when a book/product/content was published, released, or launched.
If the user asks why a creator wrote/created/built/launched something, classify it as motivation/story or resource context, not publication_timeline. If the user asks multiple things in one message, preserve all parts in resolved_user_message and choose the route that can answer the full question, not only the easiest factual subpart.
For casual reciprocal messages like "nothing much wbu", use ROUTE_1_SMALL_TALK, question_type=small_talk, needs_corpus=false, needs_web=false, needs_sources=false.
For broad creator story/background questions like "who are you, what's your story", "how did you get rich", or "how did you get started", use question_type=personal_bio, query_goal=journey_lookup, needs_web=true, needs_sources=true when public biography evidence would improve the answer.
For creator motivation/story questions like "what inspired you to start Acquisition.com" or "why didn't you retire after Gym Launch", use question_type=personal_bio, query_goal=journey_lookup, needs_web=true, needs_sources=true. Do not treat "Gym Launch" as a launch-date question.
For contextual creator turning-point follow-ups like "what made you turn your life around?", "what was your turning point?", or "what made you change?" after a broad biography answer, use the memory packet to resolve the target, then use question_type=personal_bio, query_goal=journey_lookup, needs_web=true, needs_sources=true. The resolved_user_message should ask for the catalyst/turning point specifically, not the full biography again.
For exact creator timeline/business-sale/date questions like "when did you sell your business?", use question_type=creator_fact, query_goal=timeline_lookup, needs_web=true, needs_sources=true, is_creator_fact=true.
For creator/company financial public facts like revenue, portfolio revenue, ARR, valuation, net worth, income claims, "how much did you make", or "what's the most you've made", use question_type=creator_fact or personal_bio, query_goal=current_stat_lookup when the user asks now/current/latest and stat_lookup otherwise, needs_web=true, needs_sources=true, is_creator_fact=true. Only use private_sensitive when they ask for non-public bank, tax, account, or private personal financial details.
For public-profile facts that are commonly verifiable, like age, birthday, spouse/marriage status, children/family facts, hometown, or where the creator is publicly based, use question_type=creator_fact or personal_bio, query_goal=identity_lookup, needs_web=true, needs_sources=true, is_creator_fact=true. Treat slang or misspellings like "missus", "misus", "mrs", and "wifey" as spouse/wife questions, not website/entity questions. Only use private_sensitive for home address, private contact info, private accounts, sexual/body details, or non-public family/location details.
For pure social openers like "yo", "yoooo broskki", or "hey my g", use ROUTE_0_GREETING and set corpus/web/sources false.
For direct self-harm or suicide messages like "should I kill myself", "I want to die", or "I'm going to hurt myself", use ROUTE_2_TASK, question_type=self_harm, query_goal=crisis_support, response_mode=crisis, needs_corpus=false, needs_web=false, needs_sources=false. This must bypass normal creator facts and search.
For softer first-person crisis language like "I've been feeling suicidal lately", also use question_type=self_harm and response_mode=crisis. Do not treat it as small talk or personal biography.
For follow-ups inside a crisis context like "did you ever feel like that", keep response_mode=crisis. The answer should not become a normal private biography answer; it should redirect back to immediate user safety in the creator's voice.
For harmful instructions or unsafe requests, use response_mode=boundary, needs_corpus=false, needs_web=false, needs_sources=false. The final reply should refuse the harmful action in the creator's voice and redirect to a safe alternative.
For clearly out-of-scope domain questions, use response_mode=boundary and avoid sources unless the user asks for a public/current fact that should be verified before redirecting.
For creator check-ins like "what have you been up to", use ROUTE_1_SMALL_TALK and set corpus/web/sources false unless the user asks for a specific factual update.
For questions asking what was covered in a named video/episode/podcast/post title, use query_goal=resource_lookup, needs_corpus=true, needs_web=true, needs_sources=true, source_policy=attach_resource. Public content is allowed to be summarized when verified; do not turn public creator content into a privacy boundary.
For follow-ups like "give me a deep breakdown", "summarize it", or "I don't want to watch/listen/read" after a resource/video/episode was just attached or mentioned, use query_goal=resource_lookup, needs_corpus=true, needs_web=true, needs_sources=true, source_policy=attach_resource. resolved_user_message and query_plan must include the exact prior resource title from conversation_memory_packet.
For public relationship facts or stories the creator has shared in content (spouse name, marriage status, how they met, first date, relationship lessons from a named episode), use query_goal=identity_lookup or resource_lookup, needs_web=true, needs_sources=true. Only use private_sensitive for non-public intimate details, home address/current private location, private contact info, sexual/body details, bank/tax/account info, or private family/location details.
For business/advice/coaching questions, use question_type=domain_advice, needs_corpus=true, needs_web=false unless the user asks for a specific public fact or latest/current info.
Set needs_web true for public creator facts, exact dates, sale/acquisition questions, stats, current facts, or when citations/proof are expected.
Set needs_corpus true when creator content or memory could answer.
Set source_policy=none for greetings, small talk, clarifying questions, coaching/advice turns, short answers, and any turn where a source card would feel random.
Set source_policy=attach_resource only when the user asks for a link/video/resource, asks what was in a specific piece of content, or the best answer should intentionally attach one resource.
Set source_policy=must_cite for current/public factual claims, stats, timelines, exact dates, sales/acquisitions, and creator/company facts that should be verified.
Set source_policy=cite_if_used when a source may help but should only show if the final answer explicitly uses it.
"""


class SmartIntentRouter:
    def classify(
        self,
        question: str,
        *,
        history: Optional[List[Dict[str, str]]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Optional[SmartIntentDecision]:
        key = _cache_key(question, history)
        cached = _get_cached(key)
        if cached:
            return cached

        timeout = float(timeout_seconds or getattr(settings, "SMART_INTENT_ROUTER_TIMEOUT_SECONDS", 3.5))

        def _call() -> SmartIntentDecision:
            raw = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _build_prompt(question, history)},
                ],
                model=getattr(settings, "SMART_INTENT_ROUTER_MODEL", settings.MODEL_CLASSIFICATION),
                temperature=0.0,
                max_tokens=320,
                json_mode=True,
                allow_fallback=False,
            )
            return _coerce_decision(_json_loads(raw), source="smart_router", question=question, history=history)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(_call)
            decision = future.result(timeout=timeout)
        except Exception as exc:
            executor.shutdown(wait=False, cancel_futures=True)
            logger.info("Gemini turn brain classification failed: %s", exc)
            return None
        executor.shutdown(wait=False)

        _set_cached(key, decision)
        return decision

    async def classify_async(
        self,
        question: str,
        *,
        history: Optional[List[Dict[str, str]]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Optional[SmartIntentDecision]:
        key = _cache_key(question, history)
        cached = _get_cached(key)
        if cached:
            return cached

        timeout = float(timeout_seconds or getattr(settings, "SMART_INTENT_ROUTER_TIMEOUT_SECONDS", 3.5))
        try:
            raw = await asyncio.wait_for(
                rag.generate_chat_completion_async(
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": _build_prompt(question, history)},
                    ],
                    model=getattr(settings, "SMART_INTENT_ROUTER_MODEL", settings.MODEL_CLASSIFICATION),
                    temperature=0.0,
                    max_tokens=320,
                    json_mode=True,
                    allow_fallback=False,
                ),
                timeout=timeout,
            )
            decision = _coerce_decision(_json_loads(raw), source="smart_router", question=question, history=history)
        except Exception as exc:
            logger.info("Gemini turn brain classification failed: %s", exc)
            return None

        _set_cached(key, decision)
        return decision


smart_intent_router = SmartIntentRouter()
