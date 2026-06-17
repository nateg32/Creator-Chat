import re
from typing import Any, Dict, List, Optional


_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "how", "i", "if", "in", "is", "it", "me", "my", "of", "on", "or", "so",
    "that", "the", "their", "they", "this", "to", "what", "when", "where",
    "who", "why", "with", "you", "your",
}

_HIGH_STAKES_TERMS = {
    "medical", "diagnosis", "diagnose", "symptom", "symptoms", "treatment", "prescription",
    "dosage", "dosages", "medicine", "medication", "legal", "lawsuit", "contract", "tax",
    "irs", "attorney", "lawyer", "financial", "investment", "investing", "equity", "debt",
}


def _normalize_terms(text: str) -> List[str]:
    return [term for term in re.findall(r"[a-z0-9']+", (text or "").lower()) if term not in _STOP_WORDS]


def _collect_text(value: Any) -> List[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            out.extend(_collect_text(item))
        return out
    if isinstance(value, dict):
        out: List[str] = []
        for item in value.values():
            out.extend(_collect_text(item))
        return out
    return []


def _match_rank(question_terms: List[str], candidates: List[str], limit: int = 4, allow_fallback: bool = True) -> List[str]:
    scored = []
    for idx, candidate in enumerate(candidates):
        candidate_terms = set(_normalize_terms(candidate))
        if not candidate_terms:
            continue
        overlap = len(candidate_terms.intersection(question_terms))
        scored.append((overlap, idx, candidate))
    scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    selected = [candidate for score, _, candidate in scored if score > 0][:limit]
    if selected:
        return selected
    if allow_fallback:
        return [candidate for _, _, candidate in scored[:limit]]
    return []


def _support_overlap(question_terms: List[str], support_set: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    support_set = support_set or []
    if not question_terms or not support_set:
        return {"count": len(support_set), "matching": 0, "top_overlap": 0.0}

    matching = 0
    top_overlap = 0.0
    for chunk in support_set[:8]:
        text = " ".join(
            [
                chunk.get("title") or "",
                chunk.get("content") or "",
                ((chunk.get("source_ref") or {}).get("title") or ""),
            ]
        )
        chunk_terms = set(_normalize_terms(text))
        if not chunk_terms:
            continue
        overlap = len(chunk_terms.intersection(question_terms))
        if overlap > 0:
            matching += 1
        density = overlap / max(1, min(len(question_terms), 6))
        top_overlap = max(top_overlap, density)
    return {"count": len(support_set), "matching": matching, "top_overlap": round(top_overlap, 3)}


def _domain_score(question_terms: List[str], domain_map: Dict[str, Any], fallback_topics: List[str]) -> Dict[str, Any]:
    strong = _collect_text(domain_map.get("strong_topics"))
    adjacent = _collect_text(domain_map.get("adjacent_topics"))
    weak = _collect_text(domain_map.get("weak_topics"))
    unsafe = _collect_text(domain_map.get("unsafe_topics"))
    if not strong and fallback_topics:
        strong = fallback_topics[:8]

    strong_hits = _match_rank(question_terms, strong, limit=3)
    adjacent_hits = _match_rank(question_terms, adjacent, limit=3)
    weak_hits = _match_rank(question_terms, weak, limit=3)
    unsafe_hits = _match_rank(question_terms, unsafe, limit=3, allow_fallback=False)

    if unsafe_hits:
        score = 0.1
    elif strong_hits:
        score = 0.9
    elif adjacent_hits:
        score = 0.65
    elif weak_hits:
        score = 0.35
    else:
        score = 0.25 if strong else 0.1

    return {
        "score": score,
        "strong_hits": strong_hits,
        "adjacent_hits": adjacent_hits,
        "weak_hits": weak_hits,
        "unsafe_hits": unsafe_hits,
    }


def _identity_score(
    question_terms: List[str],
    value_model: Dict[str, Any],
    belief_graph: Dict[str, Any],
    reasoning_profile: Dict[str, Any],
) -> Dict[str, Any]:
    activated_values = _match_rank(question_terms, _collect_text(value_model.get("core_values")), limit=3)
    activated_tradeoffs = _match_rank(question_terms, _collect_text(value_model.get("tradeoff_preferences")), limit=2)
    activated_rejections = _match_rank(question_terms, _collect_text(value_model.get("rejections")), limit=2)
    activated_heuristics = _match_rank(question_terms, _collect_text(value_model.get("decision_heuristics")), limit=4)
    activated_beliefs = _match_rank(question_terms, _collect_text(belief_graph.get("core_beliefs")), limit=3)
    activated_pattern = _match_rank(question_terms, _collect_text(reasoning_profile.get("default_problem_solving_pattern")), limit=3)

    buckets = [
        activated_values,
        activated_tradeoffs,
        activated_rejections,
        activated_heuristics,
        activated_beliefs,
        activated_pattern,
    ]
    active_bucket_count = sum(1 for bucket in buckets if bucket)
    score = min(1.0, 0.15 + active_bucket_count * 0.14)

    return {
        "score": round(score, 3),
        "activated_values": activated_values,
        "activated_tradeoffs": activated_tradeoffs,
        "activated_rejections": activated_rejections,
        "activated_heuristics": activated_heuristics,
        "activated_beliefs": activated_beliefs,
        "activated_pattern": activated_pattern,
    }


def _knowledge_score(question_terms: List[str], support_stats: Dict[str, Any], identity_facts: List[str]) -> float:
    fact_hits = _match_rank(question_terms, identity_facts, limit=3)
    score = 0.0
    score += min(0.35, support_stats.get("count", 0) * 0.08)
    score += min(0.35, support_stats.get("matching", 0) * 0.14)
    score += min(0.2, float(support_stats.get("top_overlap", 0.0)) * 0.4)
    if fact_hits:
        score += 0.1
    return round(min(1.0, score), 3)


def _build_predicted_stance(identity: Dict[str, Any], domain: Dict[str, Any], reasoning_profile: Dict[str, Any]) -> str:
    fragments: List[str] = []
    if identity.get("activated_values"):
        fragments.append("values " + ", ".join(identity["activated_values"][:2]))
    if identity.get("activated_heuristics"):
        fragments.append("heuristics " + ", ".join(identity["activated_heuristics"][:2]))
    if identity.get("activated_beliefs"):
        fragments.append("beliefs " + ", ".join(identity["activated_beliefs"][:2]))
    if domain.get("strong_hits") or domain.get("adjacent_hits"):
        topic_hits = domain.get("strong_hits") or domain.get("adjacent_hits")
        fragments.append("domain lens " + ", ".join(topic_hits[:2]))
    pattern = _collect_text(reasoning_profile.get("default_problem_solving_pattern"))
    if pattern:
        fragments.append("response shape " + ", ".join(pattern[:2]))
    return "; ".join(fragments[:4])


def select_stance(
    question: str,
    creator_profile: Optional[Dict[str, Any]] = None,
    support_set: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    creator_profile = creator_profile or {}
    style_fp = creator_profile.get("style_fingerprint") or creator_profile
    identity_fp = creator_profile.get("identity_fingerprint") or {}

    domain_map = style_fp.get("domain_map") or {}
    value_model = style_fp.get("value_model") or {}
    belief_graph = style_fp.get("belief_graph") or {}
    reasoning_profile = style_fp.get("reasoning_profile") or {}
    unknown_topic_policy = style_fp.get("unknown_topic_policy") or {}
    fallback_topics = _collect_text(style_fp.get("recurring_themes"))
    identity_facts = _collect_text(
        [
            identity_fp.get("verified_facts"),
            identity_fp.get("businesses"),
            identity_fp.get("products"),
            identity_fp.get("themes"),
            identity_fp.get("public_consensus"),
        ]
    )

    question_terms = _normalize_terms(question)
    support_stats = _support_overlap(question_terms, support_set)
    domain = _domain_score(question_terms, domain_map, fallback_topics)
    identity = _identity_score(question_terms, value_model, belief_graph, reasoning_profile)
    knowledge_score = _knowledge_score(question_terms, support_stats, identity_facts)
    high_stakes_hit = bool(set(question_terms).intersection(_HIGH_STAKES_TERMS))

    allow_identity_fallback = bool(unknown_topic_policy.get("allow_identity_fallback", True))
    disclosure_threshold = float(unknown_topic_policy.get("disclosure_threshold", 0.45) or 0.45)
    max_assertiveness = float(unknown_topic_policy.get("max_assertiveness", 0.65) or 0.65)

    if (domain["unsafe_hits"] or high_stakes_hit) and knowledge_score < 0.7:
        response_mode = "BOUNDARY"
    elif knowledge_score >= 0.65:
        response_mode = "KNOWLEDGE"
    elif knowledge_score >= 0.3 and (domain["score"] >= 0.45 or identity["score"] >= 0.4):
        response_mode = "KNOWLEDGE_PLUS_IDENTITY"
    elif allow_identity_fallback and domain["score"] >= 0.35 and identity["score"] >= disclosure_threshold:
        response_mode = "IDENTITY_FALLBACK"
    else:
        response_mode = "BOUNDARY"

    disclaimer_required = response_mode in {"KNOWLEDGE_PLUS_IDENTITY", "IDENTITY_FALLBACK", "BOUNDARY"} and knowledge_score < 0.55
    support_basis = []
    if knowledge_score >= 0.3:
        support_basis.append("retrieval")
    if identity["activated_values"] or identity["activated_heuristics"] or identity["activated_beliefs"]:
        support_basis.append("value_model")
    if domain["strong_hits"] or domain["adjacent_hits"]:
        support_basis.append("domain_map")
    if reasoning_profile.get("default_problem_solving_pattern"):
        support_basis.append("reasoning_profile")

    framing = {
        "KNOWLEDGE": "grounded and direct",
        "KNOWLEDGE_PLUS_IDENTITY": "grounded with inferred framing",
        "IDENTITY_FALLBACK": "opinionated but cautious",
        "BOUNDARY": "limit-setting and redirecting",
    }[response_mode]

    return {
        "response_mode": response_mode,
        "knowledge_score": knowledge_score,
        "scope_score": round(domain["score"], 3),
        "identity_score": round(identity["score"], 3),
        "domain_hits": {
            "strong": domain["strong_hits"],
            "adjacent": domain["adjacent_hits"],
            "weak": domain["weak_hits"],
            "unsafe": domain["unsafe_hits"],
        },
        "activated_values": identity["activated_values"],
        "activated_tradeoffs": identity["activated_tradeoffs"],
        "activated_rejections": identity["activated_rejections"],
        "activated_heuristics": identity["activated_heuristics"],
        "activated_beliefs": identity["activated_beliefs"],
        "activated_pattern": identity["activated_pattern"],
        "predicted_stance": _build_predicted_stance(identity, domain, reasoning_profile),
        "disclaimer_required": disclaimer_required,
        "framing": framing,
        "support_basis": support_basis,
        "max_assertiveness": round(max_assertiveness, 3),
        "boundary_style": unknown_topic_policy.get("boundary_style") or "",
        "never_infer": _collect_text(unknown_topic_policy.get("never_infer")),
        "support_stats": support_stats,
        "high_stakes_hit": high_stakes_hit,
    }
