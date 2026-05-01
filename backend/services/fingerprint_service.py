import logging
import json
import asyncio
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from backend.db import db
from backend.personality_analyzer import PersonalityAnalyzer
from backend.services.research_provider import GeminiResearchProvider
from backend.services.corpus_state import compute_creator_corpus_checksum
from backend.settings import settings
from backend.rag import get_chat_client, get_async_chat_client

logger = logging.getLogger(__name__)


def _set_fingerprint_progress(
    creator_id: int,
    *,
    status: str,
    percent: int,
    stage: str,
    message: str,
    error: Optional[str] = None,
):
    payload = {
        "status": status,
        "percent": max(0, min(int(percent), 100)),
        "stage": stage,
        "message": message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        payload["error"] = error
    db.execute_update(
        """
        UPDATE creators
        SET fingerprint_status = %s,
            fingerprint_progress = %s
        WHERE id = %s
        """,
        (status, json.dumps(payload), creator_id),
    )


def _flatten_strings(value):
    out = []
    if isinstance(value, str) and value.strip():
        out.append(value.strip())
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_flatten_strings(v))
    elif isinstance(value, list):
        for item in value:
            out.extend(_flatten_strings(item))
    return out


def _dedupe_keep_order(values, limit=None):
    seen = set()
    result = []
    for value in values:
        key = str(value).strip()
        if not key:
            continue
        low = key.lower()
        if low in seen:
            continue
        seen.add(low)
        result.append(key)
        if limit and len(result) >= limit:
            break
    return result


def _build_identity_fingerprint(name: str, link_identity: Dict[str, Any], investigative_dossier: Dict[str, Any], voice_fingerprint: Dict[str, Any]) -> Dict[str, Any]:
    identity = (link_identity or {}).get("identity") or {}
    brand = (link_identity or {}).get("brand") or {}
    platforms = (link_identity or {}).get("platforms") or {}
    biography = (investigative_dossier or {}).get("biography") or {}
    business_evolution = (investigative_dossier or {}).get("business_evolution") or []
    specific_wins = (investigative_dossier or {}).get("specific_wins") or []
    consensus = (investigative_dossier or {}).get("public_consensus_facts") or {}

    bio_bits = []
    early_life = biography.get("early_life")
    location = identity.get("location") or biography.get("birthplace")
    if identity.get("full_name"):
        bio_bits.append(identity.get("full_name"))
    if identity.get("job_titles"):
        bio_bits.append(", ".join(identity.get("job_titles")[:3]))
    if location:
        bio_bits.append(f"based around {location}")
    if early_life:
        bio_bits.append(str(early_life))

    verified_facts = []
    verified_facts.extend(identity.get("verified_background") or [])
    verified_facts.extend(consensus.values() if isinstance(consensus, dict) else [])
    verified_facts.extend((investigative_dossier or {}).get("net_worth_milestones") or [])

    businesses = []
    for item in business_evolution:
        if isinstance(item, dict):
            name_part = item.get("name")
            outcome_part = item.get("outcome")
            if name_part and outcome_part:
                businesses.append(f"{name_part}: {outcome_part}")
            elif name_part:
                businesses.append(name_part)
    businesses.extend((voice_fingerprint.get("content_truth") or {}).get("businesses") or [])

    products = []
    for item in specific_wins:
        if isinstance(item, dict):
            product = item.get("product")
            impact = item.get("revenue_or_impact")
            if product and impact:
                products.append(f"{product}: {impact}")
            elif product:
                products.append(product)
    products.extend((voice_fingerprint.get("content_truth") or {}).get("products") or [])
    products.extend(brand.get("products_services") or [])

    themes = []
    for platform_info in platforms.values() if isinstance(platforms, dict) else []:
        if isinstance(platform_info, dict):
            themes.extend(platform_info.get("themes") or [])
    themes.extend(voice_fingerprint.get("recurring_themes") or [])

    mission = brand.get("mission") or next(iter(voice_fingerprint.get("worldview", {}).get("core_beliefs", []) or []), None)

    return {
        "bio": ". ".join(_dedupe_keep_order(bio_bits, limit=4)) or f"{name} public profile synthesized from ingested content and verified web research.",
        "mission": mission,
        "is_verified": bool(verified_facts or identity.get("verified_background") or consensus),
        "job_titles": _dedupe_keep_order(identity.get("job_titles") or [], limit=5),
        "verified_facts": _dedupe_keep_order(verified_facts, limit=8),
        "businesses": _dedupe_keep_order(businesses, limit=8),
        "products": _dedupe_keep_order(products, limit=8),
        "themes": _dedupe_keep_order(themes, limit=8),
        "affiliations": _dedupe_keep_order((investigative_dossier or {}).get("affiliations") or [], limit=6),
        "controversies": _dedupe_keep_order((investigative_dossier or {}).get("controversies_and_boundaries") or [], limit=6),
        "creator_claims": _dedupe_keep_order((link_identity or {}).get("creator_claims") or [], limit=6),
        "public_consensus": _dedupe_keep_order(_flatten_strings(consensus), limit=8),
    }


def _has_meaningful_dossier(dossier: Dict[str, Any]) -> bool:
    if not isinstance(dossier, dict) or not dossier:
        return False
    for key in (
        "biography",
        "business_evolution",
        "specific_wins",
        "net_worth_milestones",
        "controversies_and_boundaries",
        "affiliations",
        "public_consensus_facts",
    ):
        value = dossier.get(key)
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and any(str(v).strip() for v in value):
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _load_cached_dossier_from_creator(creator_id: int) -> Dict[str, Any]:
    row = db.execute_one("SELECT research_summary FROM creators WHERE id = %s", (creator_id,))
    summary = (row or {}).get("research_summary") or {}
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except Exception:
            summary = {}
    if not isinstance(summary, dict):
        return {}
    dossier = summary.get("investigative_dossier") or {}
    return dossier if _has_meaningful_dossier(dossier) else {}

def _load_cached_research_summary(creator_id: int) -> Dict[str, Any]:
    row = db.execute_one("SELECT research_summary FROM creators WHERE id = %s", (creator_id,))
    summary = (row or {}).get("research_summary") or {}
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except Exception:
            summary = {}
    return summary if isinstance(summary, dict) else {}


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


def _merge_incremental_value(base: Any, delta: Any) -> Any:
    if isinstance(base, dict) or isinstance(delta, dict):
        merged: Dict[str, Any] = {}
        base = base if isinstance(base, dict) else {}
        delta = delta if isinstance(delta, dict) else {}
        for key in set(base.keys()) | set(delta.keys()):
            merged[key] = _merge_incremental_value(base.get(key), delta.get(key))
        return merged

    if isinstance(base, list) or isinstance(delta, list):
        base_list = base if isinstance(base, list) else []
        delta_list = delta if isinstance(delta, list) else []
        merged_list: List[Any] = []
        seen = set()
        for item in delta_list + base_list:
            key = json.dumps(item, sort_keys=True, default=str) if isinstance(item, (dict, list)) else str(item).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            merged_list.append(item)
        return merged_list

    if isinstance(base, (int, float)) or isinstance(delta, (int, float)):
        if isinstance(base, (int, float)) and isinstance(delta, (int, float)):
            return max(base, delta)
        return delta if isinstance(delta, (int, float)) else base

    if isinstance(delta, str) and delta.strip():
        if not isinstance(base, str) or not base.strip():
            return delta
        return base

    return base if base is not None else delta


def _merge_incremental_fingerprint(existing: Dict[str, Any], delta: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge_incremental_value(existing or {}, delta or {})
    return merged if isinstance(merged, dict) else (existing or delta or {})



def _normalize_search_hits(results: List[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    normalized = []
    seen = set()
    for item in results or []:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        snippet = (item.get("snippet") or "").strip()
        key = (url or title).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append({
            "title": title,
            "url": url,
            "snippet": snippet,
            "source": item.get("source"),
        })
        if len(normalized) >= limit:
            break
    return normalized


def _question_rate_from_label(label: str) -> float:
    normalized = str(label or "").strip().lower()
    if normalized == "high":
        return 0.6
    if normalized == "medium":
        return 0.35
    if normalized == "low":
        return 0.15
    return 0.25


def _merge_voice_pattern_packet(voice_fingerprint: Dict[str, Any], packet: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(voice_fingerprint, dict):
        voice_fingerprint = {}
    if not isinstance(packet, dict) or not packet:
        return voice_fingerprint

    merged = _merge_incremental_fingerprint(voice_fingerprint, {"voice_patterns": packet})
    lexical_rules = merged.get("lexical_rules") or {}
    speech_mechanics = merged.get("speech_mechanics") or {}
    cadence_rules = merged.get("cadence_rules") or {}
    behavior = merged.get("behavioral_patterns") or {}
    mode_matrix = merged.get("mode_matrix") or {}
    greeting_mode = mode_matrix.get("greeting") or {}
    anti_persona = merged.get("anti_persona") or {}
    golden_examples = merged.get("golden_examples") or {}

    sentence = packet.get("sentence_structure") or {}
    rhythm = packet.get("rhythm") or {}
    rhetorical = packet.get("rhetorical_moves") or {}
    interaction = packet.get("interaction_style") or {}
    lexical = packet.get("lexical_markers") or {}
    behavioral = packet.get("behavioral_patterns") or {}
    greeting = packet.get("greeting_signals") or {}

    signature_phrases = _dedupe_keep_order(
        list(merged.get("signature_phrases") or [])
        + list(lexical_rules.get("signature_phrases") or [])
        + list(lexical.get("signature_phrases") or [])
        + list(greeting.get("opening_hooks") or []),
        limit=12,
    )
    merged["signature_phrases"] = signature_phrases
    lexical_rules["signature_phrases"] = signature_phrases
    lexical_rules["high_signal_words"] = _dedupe_keep_order(
        list(lexical_rules.get("high_signal_words") or [])
        + list(lexical.get("high_signal_words") or []),
        limit=18,
    )
    lexical_rules["banned_frames"] = _dedupe_keep_order(
        list(lexical_rules.get("banned_frames") or [])
        + list(lexical.get("banned_frames") or [])
        + list(greeting.get("forbidden_generic_frames") or []),
        limit=14,
    )
    merged["lexical_rules"] = lexical_rules

    speech_mechanics["sentence_shape"] = speech_mechanics.get("sentence_shape") or sentence.get("sentence_shape") or rhythm.get("pacing") or ""
    speech_mechanics["question_density"] = max(
        float(speech_mechanics.get("question_density", 0.0) or 0.0),
        _question_rate_from_label(sentence.get("question_frequency")),
    )
    speech_mechanics["signature_openings"] = _dedupe_keep_order(
        list(speech_mechanics.get("signature_openings") or [])
        + list(rhetorical.get("signature_openings") or [])
        + list(greeting.get("opening_hooks") or []),
        limit=10,
    )
    speech_mechanics["signature_landings"] = _dedupe_keep_order(
        list(speech_mechanics.get("signature_landings") or [])
        + list(rhetorical.get("signature_landings") or []),
        limit=10,
    )
    speech_mechanics["cadence_markers"] = _dedupe_keep_order(
        list(speech_mechanics.get("cadence_markers") or [])
        + list(rhythm.get("pause_markers") or []),
        limit=8,
    )
    speech_mechanics["punctuation_rules"] = _dedupe_keep_order(
        list(speech_mechanics.get("punctuation_rules") or [])
        + list(rhythm.get("punctuation_rules") or []),
        limit=10,
    )
    merged["speech_mechanics"] = speech_mechanics

    cadence_rules["sentence_shape"] = cadence_rules.get("sentence_shape") or sentence.get("sentence_shape") or rhythm.get("pacing") or "balanced"
    cadence_rules["question_rate"] = max(
        float(cadence_rules.get("question_rate", 0.0) or 0.0),
        _question_rate_from_label(sentence.get("question_frequency")),
    )
    cadence_rules["pause_markers"] = _dedupe_keep_order(
        list(cadence_rules.get("pause_markers") or [])
        + list(rhythm.get("pause_markers") or []),
        limit=6,
    )
    cadence_rules["story_vs_list"] = cadence_rules.get("story_vs_list") or rhetorical.get("story_vs_list") or "hybrid"
    merged["cadence_rules"] = cadence_rules

    behavior["confidence_level"] = behavior.get("confidence_level") or behavioral.get("confidence_level") or ""
    behavior["decision_style"] = behavior.get("decision_style") or behavioral.get("decision_style") or ""
    behavior["pushback_style"] = behavior.get("pushback_style") or behavioral.get("pushback_style") or interaction.get("disagreement_style") or ""
    behavior["always_do"] = _dedupe_keep_order(
        list(behavior.get("always_do") or [])
        + list(behavioral.get("always_do") or []),
        limit=10,
    )
    behavior["never_do"] = _dedupe_keep_order(
        list(behavior.get("never_do") or [])
        + list(behavioral.get("never_do") or []),
        limit=10,
    )
    behavior["excitement_triggers"] = _dedupe_keep_order(
        list(behavior.get("excitement_triggers") or [])
        + list(behavioral.get("excitement_triggers") or []),
        limit=10,
    )
    merged["behavioral_patterns"] = behavior

    greeting_mode["opening_move"] = greeting_mode.get("opening_move") or next(iter(greeting.get("opening_hooks") or rhetorical.get("signature_openings") or []), "")
    greeting_mode["question_style"] = greeting_mode.get("question_style") or next(iter(greeting.get("check_in_questions") or []), "")
    greeting_mode["forbidden"] = _dedupe_keep_order(
        list(greeting_mode.get("forbidden") or [])
        + list(greeting.get("forbidden_generic_frames") or []),
        limit=10,
    )
    mode_matrix["greeting"] = greeting_mode
    merged["mode_matrix"] = mode_matrix

    anti_persona["forbidden_generic_coach_lines"] = _dedupe_keep_order(
        list(anti_persona.get("forbidden_generic_coach_lines") or [])
        + list(greeting.get("forbidden_generic_frames") or []),
        limit=12,
    )
    merged["anti_persona"] = anti_persona

    golden_examples["greeting"] = _dedupe_keep_order(
        list(golden_examples.get("greeting") or [])
        + list(greeting.get("opening_hooks") or [])
        + list(greeting.get("check_in_questions") or []),
        limit=10,
    )
    merged["golden_examples"] = golden_examples
    merged["signature_moves"] = _dedupe_keep_order(
        list(merged.get("signature_moves") or [])
        + list(rhetorical.get("emphasis_patterns") or [])
        + list(behavioral.get("always_do") or []),
        limit=12,
    )
    merged["signature_response_moves"] = _dedupe_keep_order(
        list(merged.get("signature_response_moves") or [])
        + list(rhetorical.get("storytelling_triggers") or [])
        + list(behavioral.get("always_do") or []),
        limit=12,
    )
    return merged


def _fallback_openai_dossier(
    creator_id: int,
    creator_name: str,
    creator_profile: Dict[str, Any],
    initial_clues: Dict[str, Any],
) -> Dict[str, Any]:
    if not settings.OPENAI_API_KEY:
        return {}

    try:
        from backend.services.research_provider import OpenAIResearchProvider
    except Exception as exc:
        logger.warning(f"FingerprintService: OpenAI dossier fallback unavailable: {exc}")
        return {}

    provider = OpenAIResearchProvider()
    if not getattr(provider, "enabled", False):
        return {}

    queries = [
        f"{creator_name} biography age birthplace background",
        f"{creator_name} business history companies brands",
        f"{creator_name} products programs offers course business",
        f"{creator_name} controversy criticism lawsuit podcast interview",
    ]
    aggregated = []
    for query in queries:
        try:
            aggregated.extend(provider.search(query, creator_profile, resource_type="web"))
        except Exception as exc:
            logger.warning(f"FingerprintService: OpenAI dossier query failed for '{query}': {exc}")

    hits = _normalize_search_hits(aggregated, limit=14)
    if not hits:
        return {}

    client = get_chat_client(settings.MODEL_MEMORY)
    prompt = f"""
You are building a public-domain investigative dossier for {creator_name}.

Use only the evidence provided below plus the initial clues. Do not invent facts.
If a field is unknown, leave it blank or return an empty list/object.

INITIAL CLUES:
{json.dumps(initial_clues)}

SEARCH EVIDENCE:
{json.dumps(hits)}

Return JSON only:
{{
  "biography": {{
    "age": "...",
    "birthplace": "...",
    "early_life": "summarized",
    "certainty": "low|med|high"
  }},
  "business_evolution": [
    {{
      "name": "...",
      "year": "...",
      "outcome": "...",
      "role": "..."
    }}
  ],
  "specific_wins": [
    {{
      "product": "...",
      "niche": "...",
      "revenue_or_impact": "..."
    }}
  ],
  "net_worth_milestones": ["..."],
  "controversies_and_boundaries": ["..."],
  "affiliations": ["..."],
  "public_consensus_facts": {{
    "fact_name": "value"
  }}
}}
"""
    response = client.chat.completions.create(
        model=settings.MODEL_CLASSIFICATION,
        messages=[
            {"role": "system", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    content = response.choices[0].message.content
    try:
        parsed = json.loads(content)
    except Exception:
        logger.warning("FingerprintService: OpenAI dossier fallback returned invalid JSON.")
        return {}
    return parsed if _has_meaningful_dossier(parsed) else {}

class FingerprintService:
    """
    Orchestrates the Style Fingerprint system:
    1. Public Identity & Background (Biographical research)
    2. Voice & Style Profile (Linguistic analysis)
    """

    def __init__(self):
        from backend.services.research_provider import get_research_provider, GeminiResearchProvider
        # Fingerprint research phases (links + dossier) are implemented on Gemini provider.
        # Prefer Gemini when GOOGLE_API_KEY is present; otherwise fall back to default provider factory.
        self.researcher = GeminiResearchProvider() if settings.GOOGLE_API_KEY else get_research_provider()
        self.analyzer = PersonalityAnalyzer()
        self.agent_max_iterations = 6

    @staticmethod
    def _build_creator_profile(creator_id: int, creator: Dict[str, Any], configs: Dict[str, Any], domains: List[str]) -> Dict[str, Any]:
        return {
            "id": creator_id,
            "name": creator.get("name"),
            "handle": creator.get("handle"),
            "platform_configs": configs or {},
            "official_domains": domains or [],
            "course_domains": creator.get("course_domains") or [],
            "course_base_urls": creator.get("course_base_urls") or [],
            "youtube_channel_id": creator.get("youtube_channel_id"),
            "youtube_handle": creator.get("youtube_handle"),
        }

    @staticmethod
    def _clip_text(text: Any, limit: int = 700) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        return cleaned[:limit]

    def _load_approved_content_samples(self, creator_id: int, *, limit: int = 8) -> List[Dict[str, Any]]:
        docs = self.analyzer._load_corpus(creator_id, limit=limit)
        samples: List[Dict[str, Any]] = []
        seen = set()
        for idx, doc in enumerate(docs or [], start=1):
            metadata = _load_jsonish(doc.get("metadata"))
            content = str(doc.get("content") or "").strip()
            if not content:
                continue
            title = (
                doc.get("title")
                or metadata.get("title")
                or metadata.get("canonical_title")
                or doc.get("source")
                or f"Sample {idx}"
            )
            key = str(doc.get("source_id") or title).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            samples.append(
                {
                    "sample_id": doc.get("source_id") or f"sample_{idx}",
                    "title": title,
                    "platform": metadata.get("platform") or doc.get("source") or "unknown",
                    "url": metadata.get("canonical_url") or metadata.get("source_url") or metadata.get("url") or "",
                    "excerpt": self._clip_text(content, limit=700),
                    "content": self._clip_text(content, limit=1800),
                }
            )
            if len(samples) >= limit:
                break
        return samples

    @staticmethod
    def _format_content_samples_for_prompt(samples: List[Dict[str, Any]], *, limit: int = 5) -> str:
        rendered: List[str] = []
        for idx, sample in enumerate(samples[:limit], start=1):
            rendered.append(
                f"[Sample {idx}] {sample.get('title')} | platform={sample.get('platform')}\n"
                f"{sample.get('excerpt')}"
            )
        return "\n\n---\n\n".join(rendered) if rendered else "No approved content samples available."

    @staticmethod
    def _format_voice_pattern_samples(samples: List[Dict[str, Any]], *, limit: int = 8) -> str:
        rendered: List[str] = []
        for idx, sample in enumerate(samples[:limit], start=1):
            rendered.append(
                f"[Sample {idx}] {sample.get('title')} | platform={sample.get('platform')} | url={sample.get('url') or 'n/a'}\n"
                f"{sample.get('content') or sample.get('excerpt')}"
            )
        return "\n\n---\n\n".join(rendered) if rendered else "No approved content samples available."

    async def _extract_voice_pattern_packet(
        self,
        creator_name: str,
        approved_content: List[Dict[str, Any]],
        voice_fingerprint: Dict[str, Any],
        persona_modifier: str = "",
    ) -> Dict[str, Any]:
        if not settings.OPENAI_API_KEY or not approved_content:
            return {}

        modifier_block = (
            f"\n\nARCHETYPE GUIDANCE (overrides default voice assumptions):\n{persona_modifier.strip()}\n"
            if persona_modifier and persona_modifier.strip()
            else ""
        )

        prompt = f"""
You are extracting a runtime voice pattern packet for creator {creator_name}.

Use only the approved creator content and existing fingerprint context below.
This packet will be merged into the creator's style fingerprint and used at runtime.
Be concrete, creator-specific, and avoid generic coaching language.{modifier_block}

Return JSON only with this exact shape:
{{
  "sentence_structure": {{
    "sentence_shape": "short_bursts|balanced|flowing",
    "question_frequency": "low|medium|high",
    "uses_fragments": true,
    "pattern_description": "",
    "evidence": []
  }},
  "rhythm": {{
    "pacing": "fast|measured|slow",
    "pause_markers": [],
    "punctuation_rules": [],
    "uses_dashes": true,
    "uses_ellipsis": false
  }},
  "rhetorical_moves": {{
    "signature_openings": [],
    "signature_landings": [],
    "emphasis_patterns": [],
    "storytelling_triggers": [],
    "story_vs_list": "story|list|hybrid"
  }},
  "interaction_style": {{
    "audience_address": "",
    "disagreement_style": "",
    "uncertainty_style": ""
  }},
  "lexical_markers": {{
    "signature_phrases": [],
    "high_signal_words": [],
    "banned_frames": [],
    "words_they_avoid": []
  }},
  "behavioral_patterns": {{
    "confidence_level": "",
    "decision_style": "",
    "pushback_style": "",
    "excitement_triggers": [],
    "always_do": [],
    "never_do": []
  }},
  "greeting_signals": {{
    "opening_hooks": [],
    "check_in_questions": [],
    "forbidden_generic_frames": []
  }}
}}

EXISTING FINGERPRINT CONTEXT:
{json.dumps({
    "signature_phrases": voice_fingerprint.get("signature_phrases") or [],
    "lexical_rules": voice_fingerprint.get("lexical_rules") or {},
    "speech_mechanics": voice_fingerprint.get("speech_mechanics") or {},
    "cadence_rules": voice_fingerprint.get("cadence_rules") or {},
    "behavioral_patterns": voice_fingerprint.get("behavioral_patterns") or {},
    "golden_examples": voice_fingerprint.get("golden_examples") or {},
    "mode_matrix": voice_fingerprint.get("mode_matrix") or {},
})}

APPROVED CONTENT:
{self._format_voice_pattern_samples(approved_content, limit=8)}
"""

        try:
            response = await get_async_chat_client(settings.MODEL_CLASSIFICATION).chat.completions.create(
                model=settings.MODEL_CLASSIFICATION,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an evidence-bound linguistic analyst. "
                            "Extract literal wording patterns, greeting signals, rhythm, and rhetorical moves. "
                            "Return only valid JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.15,
            )
            return _load_jsonish(response.choices[0].message.content)
        except Exception as exc:
            logger.warning(f"FingerprintService: voice pattern extraction failed: {exc}")
            return {}

    @staticmethod
    def _fingerprint_agent_tools() -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "analyze_content_style",
                    "description": "Inspect approved creator content and the existing style fingerprint to understand voice, tone, beliefs, and audience relationship. Start here before web research.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "focus": {
                                "type": "string",
                                "enum": [
                                    "voice_and_tone",
                                    "themes_and_topics",
                                    "communication_style",
                                    "values_and_beliefs",
                                    "audience_relationship",
                                ],
                            },
                            "notes": {"type": "string"},
                        },
                        "required": ["focus"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "Run grounded public web research. Prefer creator-owned sources and official surfaces first. Use this for identity, background, expertise, or public consensus.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "intent": {
                                "type": "string",
                                "enum": ["identity", "expertise", "background", "reputation", "content_topics"],
                            },
                        },
                        "required": ["query", "intent"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fetch_url",
                    "description": "Fetch one specific URL from grounded research when the page likely contains richer creator evidence than the search summary.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["url", "reason"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "record_finding",
                    "description": "Record a verified or uncertain finding before final synthesis. Use source references whenever possible.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": ["identity", "expertise", "style", "values", "audience", "background", "caution"],
                            },
                            "finding": {"type": "string"},
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                            "source_refs": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "evidence": {"type": "string"},
                        },
                        "required": ["category", "finding", "confidence"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "synthesize_persona",
                    "description": "FINAL STEP. Call only after at least three non-synthesis tool calls across content analysis and research. Produces the final persona seed bundle.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ready": {"type": "boolean"},
                            "gaps": {"type": "string"},
                        },
                        "required": ["ready", "gaps"],
                    },
                },
            },
        ]

    @staticmethod
    def _agent_message_to_payload(message: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "role": "assistant",
            "content": message.content or "",
        }
        tool_calls = []
        for tool_call in getattr(message, "tool_calls", []) or []:
            tool_calls.append(
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
            )
        if tool_calls:
            payload["tool_calls"] = tool_calls
        return payload

    async def _search_web_for_agent(self, query: str, intent: str, context: Dict[str, Any]) -> Dict[str, Any]:
        creator_profile = context["creator_profile"]
        if hasattr(self.researcher, "grounded_overview"):
            overview = self.researcher.grounded_overview(
                query,
                creator_profile,
                conversation_history=context.get("conversation_history") or [],
                max_queries=4,
            )
            packet = {
                "query": query,
                "intent": intent,
                "query_plan": overview.get("query_plan") or [query],
                "response_text": self._clip_text(overview.get("response_text"), limit=1800),
                "results": _normalize_search_hits(overview.get("results") or [], limit=8),
                "citations": [
                    {
                        "text": self._clip_text(citation.get("text"), limit=180),
                        "url": citation.get("url"),
                        "title": citation.get("title"),
                        "subquery": citation.get("subquery"),
                    }
                    for citation in (overview.get("citations") or [])[:12]
                ],
                "sources": [
                    {
                        "title": source.get("title"),
                        "url": source.get("url"),
                        "resource_type": source.get("resource_type"),
                        "platform": source.get("platform"),
                        "subquery": source.get("subquery"),
                    }
                    for source in (overview.get("sources") or [])[:12]
                ],
            }
        else:
            results = self.researcher.search_general(query, creator_profile.get("id"), creator_profile=creator_profile)
            packet = {
                "query": query,
                "intent": intent,
                "query_plan": [query],
                "response_text": "",
                "results": _normalize_search_hits(results, limit=8),
                "citations": [],
                "sources": _normalize_search_hits(results, limit=8),
            }
        context.setdefault("grounding_packets", []).append(packet)
        return packet

    async def _fetch_url_for_agent(self, url: str, reason: str, context: Dict[str, Any]) -> Dict[str, Any]:
        cached = context.setdefault("fetched_urls", {})
        if url in cached:
            return cached[url]

        # Reuse grounded search metadata before making a raw network fetch.
        for packet in context.get("grounding_packets") or []:
            for source in packet.get("sources") or []:
                if str(source.get("url") or "").strip() == url.strip():
                    result = {
                        "url": url,
                        "reason": reason,
                        "title": source.get("title"),
                        "excerpt": self._clip_text(packet.get("response_text"), limit=1500),
                        "source": "grounded_packet",
                    }
                    cached[url] = result
                    return result

        try:
            import requests

            response = requests.get(
                url,
                timeout=8,
                headers={"User-Agent": "Mozilla/5.0 CreatorBot/1.0"},
            )
            body = response.text or ""
            body = re.sub(r"(?is)<script.*?>.*?</script>", " ", body)
            body = re.sub(r"(?is)<style.*?>.*?</style>", " ", body)
            body = re.sub(r"(?s)<[^>]+>", " ", body)
            excerpt = self._clip_text(body, limit=2200)
            result = {
                "url": url,
                "reason": reason,
                "status_code": response.status_code,
                "excerpt": excerpt,
                "source": "live_fetch",
            }
        except Exception as exc:
            result = {
                "url": url,
                "reason": reason,
                "error": str(exc),
                "source": "live_fetch",
            }

        cached[url] = result
        return result

    async def _analyze_content_style_for_agent(self, focus: str, notes: str, context: Dict[str, Any]) -> Dict[str, Any]:
        voice = context.get("voice_fingerprint") or {}
        focus_map = {
            "voice_and_tone": {
                "traits": voice.get("traits") or [],
                "signature_phrases": voice.get("signature_phrases") or [],
                "linguistic_dna": voice.get("linguistic_dna") or {},
                "speech_mechanics": voice.get("speech_mechanics") or {},
                "voice_patterns": voice.get("voice_patterns") or {},
                "evidence_snippets": voice.get("evidence_snippets") or [],
            },
            "themes_and_topics": {
                "recurring_themes": voice.get("recurring_themes") or [],
                "content_truth": voice.get("content_truth") or {},
                "domain_map": voice.get("domain_map") or {},
            },
            "communication_style": {
                "teaching_style": voice.get("teaching_style") or [],
                "signature_moves": voice.get("signature_moves") or [],
                "behavioral_patterns": voice.get("behavioral_patterns") or {},
                "reasoning_profile": voice.get("reasoning_profile") or {},
                "mode_matrix": voice.get("mode_matrix") or {},
            },
            "values_and_beliefs": {
                "worldview": voice.get("worldview") or {},
                "belief_graph": voice.get("belief_graph") or {},
                "value_model": voice.get("value_model") or {},
                "story_bank": voice.get("story_bank") or [],
            },
            "audience_relationship": {
                "audience_and_power": voice.get("audience_and_power") or {},
                "pressure_engine": voice.get("pressure_engine") or {},
                "identity_signature": voice.get("identity_signature") or {},
                "golden_replies": voice.get("golden_replies") or {},
            },
        }
        analysis = {
            "focus": focus,
            "notes": notes,
            "analysis": focus_map.get(focus, focus_map["voice_and_tone"]),
            "samples": context.get("approved_content") or [],
        }
        context.setdefault("content_analysis", []).append({"focus": focus, "notes": notes})
        return analysis

    @staticmethod
    def _record_finding_for_agent(args: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        finding = {
            "category": args.get("category"),
            "finding": str(args.get("finding") or "").strip(),
            "confidence": args.get("confidence"),
            "source_refs": [str(item).strip() for item in (args.get("source_refs") or []) if str(item).strip()],
            "evidence": str(args.get("evidence") or "").strip(),
        }
        if finding["finding"]:
            registry = context.setdefault("gathered_facts", [])
            existing = {str(item.get("finding") or "").strip().lower() for item in registry}
            if finding["finding"].lower() not in existing:
                registry.append(finding)
        return {
            "recorded": bool(finding["finding"]),
            "fact_count": len(context.get("gathered_facts") or []),
        }

    async def _synthesize_persona_bundle(self, context: Dict[str, Any], gaps: str = "") -> Dict[str, Any]:
        facts = context.get("gathered_facts") or []
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for fact in facts:
            grouped.setdefault(str(fact.get("category") or "other"), []).append(fact)

        prompt = f"""
You are writing the evidence-backed persona seed for creator {context['creator_name']}.

Use only the supplied evidence. Do not invent facts. Favor the creator's own words over third-party summaries.

Return JSON only with this exact shape:
{{
  "identity_patch": {{
    "identity": {{}},
    "brand": {{}},
    "platforms": {{}},
    "creator_claims": [],
    "unknown_fields": []
  }},
  "dossier_patch": {{
    "biography": {{}},
    "business_evolution": [],
    "specific_wins": [],
    "net_worth_milestones": [],
    "controversies_and_boundaries": [],
    "affiliations": [],
    "public_consensus_facts": {{}}
  }},
  "creator_claims": [],
  "unknown_fields": [],
  "fact_registry": [],
  "style_summary": "",
  "identity_summary": "",
  "runtime_anchor_points": [],
  "verified_beliefs": [],
  "audience_contract": [],
  "lexical_markers": {{
    "signature_phrases": [],
    "high_signal_words": [],
    "banned_generic_phrases": []
  }},
  "soul_seed_markdown": ""
}}

KNOWN GAPS:
{gaps or "None noted."}

VOICE FINGERPRINT:
{json.dumps(context.get("voice_fingerprint") or {})}

GROUPED FACT REGISTRY:
{json.dumps(grouped)}

GROUNDED SEARCH PACKETS:
{json.dumps(context.get("grounding_packets") or [])}

APPROVED CONTENT SAMPLES:
{json.dumps((context.get("approved_content") or [])[:6])}
"""
        try:
            response = await get_async_chat_client(settings.MODEL_CLASSIFICATION).chat.completions.create(
                model=settings.MODEL_CLASSIFICATION,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You synthesize creator persona bundles. "
                            "Everything must be grounded to the supplied evidence."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            parsed = _load_jsonish(response.choices[0].message.content)
        except Exception as exc:
            logger.warning(f"FingerprintService: persona bundle synthesis failed: {exc}")
            parsed = {}

        if not parsed:
            parsed = {
                "identity_patch": {},
                "dossier_patch": {},
                "creator_claims": [],
                "unknown_fields": [gaps] if gaps else [],
                "fact_registry": facts,
                "style_summary": "",
                "identity_summary": "",
                "runtime_anchor_points": [],
                "verified_beliefs": [],
                "audience_contract": [],
                "lexical_markers": {
                    "signature_phrases": [],
                    "high_signal_words": [],
                    "banned_generic_phrases": [],
                },
                "soul_seed_markdown": "",
            }
        return parsed

    async def _execute_fingerprint_agent_tool(self, tool_name: str, args: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name == "search_web":
            return await self._search_web_for_agent(
                str(args.get("query") or "").strip(),
                str(args.get("intent") or "identity"),
                context,
            )
        if tool_name == "fetch_url":
            return await self._fetch_url_for_agent(
                str(args.get("url") or "").strip(),
                str(args.get("reason") or "").strip(),
                context,
            )
        if tool_name == "analyze_content_style":
            return await self._analyze_content_style_for_agent(
                str(args.get("focus") or "voice_and_tone"),
                str(args.get("notes") or "").strip(),
                context,
            )
        if tool_name == "record_finding":
            return self._record_finding_for_agent(args, context)
        if tool_name == "synthesize_persona":
            bundle = await self._synthesize_persona_bundle(context, gaps=str(args.get("gaps") or "").strip())
            context["persona_bundle"] = bundle
            return bundle
        return {"error": f"Unknown tool '{tool_name}'."}

    async def _run_fingerprint_agent(
        self,
        *,
        creator_id: int,
        creator_name: str,
        creator_profile: Dict[str, Any],
        link_identity: Dict[str, Any],
        voice_fingerprint: Dict[str, Any],
        creator_claims: List[str],
        unknown_fields: List[str],
    ) -> Dict[str, Any]:
        if not settings.OPENAI_API_KEY:
            return {}

        approved_content = self._load_approved_content_samples(creator_id, limit=8)
        context: Dict[str, Any] = {
            "creator_id": creator_id,
            "creator_name": creator_name,
            "creator_profile": creator_profile,
            "approved_content": approved_content,
            "voice_fingerprint": voice_fingerprint,
            "link_identity": link_identity,
            "creator_claims": list(creator_claims or []),
            "unknown_fields": list(unknown_fields or []),
            "gathered_facts": [],
            "grounding_packets": [],
            "tool_trace": [],
            "conversation_history": [],
        }

        initial_prompt = f"""
You are building an evidence-backed runtime persona fingerprint for creator: {creator_name}

Known public links and identity clues:
{json.dumps(link_identity or {})}

Creator-stated claims already observed:
{json.dumps(creator_claims or [])}

Known gaps:
{json.dumps(unknown_fields or [])}

Approved content samples:
{self._format_content_samples_for_prompt(approved_content, limit=5)}

Your job:
1. Start with approved content and inspect how this creator actually sounds.
2. Use grounded web research to verify public identity, business history, expertise, and public consensus.
3. Record findings as you go.
4. Only synthesize once you have enough coverage across identity, style, values, and audience.

Rules:
- Prefer creator-owned sources and the creator's own words.
- Never invent facts.
- Do not call synthesize_persona before at least three non-synthesis tool calls.
- Be skeptical of PR fluff and third-party summaries.
"""
        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are a persona research agent. Work methodically and use the tools. "
                    "Your final bundle must be grounded in evidence, not vibes."
                ),
            },
            {"role": "user", "content": initial_prompt},
        ]

        non_synthesis_calls = 0
        for iteration in range(1, self.agent_max_iterations + 1):
            _set_fingerprint_progress(
                creator_id,
                status="processing",
                percent=min(90, 58 + iteration * 5),
                stage="persona_agent",
                message=f"Persona agent iteration {iteration}: gathering evidence and checking creator-specific signals.",
            )
            response = await get_async_chat_client(settings.MODEL_CLASSIFICATION).chat.completions.create(
                model=settings.MODEL_VERIFY,
                messages=messages,
                tools=self._fingerprint_agent_tools(),
                tool_choice="auto",
                temperature=0.2,
            )
            message = response.choices[0].message
            messages.append(self._agent_message_to_payload(message))
            tool_calls = list(getattr(message, "tool_calls", []) or [])
            if not tool_calls:
                break

            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                args = _load_jsonish(tool_call.function.arguments)
                trace_entry = {
                    "iteration": iteration,
                    "tool": tool_name,
                    "args": args,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                context["tool_trace"].append(trace_entry)

                if tool_name == "synthesize_persona" and non_synthesis_calls < 3:
                    result = {
                        "deferred": True,
                        "reason": "Need at least three non-synthesis tool calls before synthesis.",
                        "non_synthesis_calls": non_synthesis_calls,
                    }
                else:
                    result = await self._execute_fingerprint_agent_tool(tool_name, args, context)
                    if tool_name != "synthesize_persona":
                        non_synthesis_calls += 1

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result),
                    }
                )

                if tool_name == "synthesize_persona" and not result.get("deferred"):
                    bundle = dict(result)
                    bundle["tool_trace"] = context.get("tool_trace") or []
                    bundle["grounding_packets"] = context.get("grounding_packets") or []
                    bundle["fact_registry"] = bundle.get("fact_registry") or context.get("gathered_facts") or []
                    bundle["research_quality"] = "agentic_grounded"
                    return bundle

        forced_bundle = await self._synthesize_persona_bundle(
            context,
            gaps="Agent reached the iteration ceiling before explicitly synthesizing.",
        )
        forced_bundle["tool_trace"] = context.get("tool_trace") or []
        forced_bundle["grounding_packets"] = context.get("grounding_packets") or []
        forced_bundle["fact_registry"] = forced_bundle.get("fact_registry") or context.get("gathered_facts") or []
        forced_bundle["research_quality"] = "agentic_grounded_forced"
        return forced_bundle

    async def generate_fingerprint_async(self, creator_id: int, refresh: bool = False, mode: str = "full"):
        """
        Main orchestration logic (Deep Research Upgrade).
        Phases: Link-First, Content Mining, Google Expansion, Synthesis.
        """
        try:
            _set_fingerprint_progress(
                creator_id,
                status="processing",
                percent=6,
                stage="preparing",
                message="Checking creator config, approved content, and reusable research before analysis starts.",
            )

            # 2. Get Creator Info & Setup Links
            creator = db.execute_one(
                """
                SELECT name, handle, platform_configs, official_domains, course_domains, course_base_urls,
                       youtube_channel_id, youtube_handle, research_summary,
                       style_fingerprint, identity_fingerprint, soul_md, fingerprint_updated_at,
                       content_corpus_checksum, fingerprint_corpus_checksum,
                       creator_archetype, archetype_distribution
                FROM creators
                WHERE id = %s
                """,
                (creator_id,)
            )
            if not creator:
                logger.error(f"FingerprintService: Creator {creator_id} not found.")
                return

            name = creator.get("name") or creator.get("handle") or "The Creator"
            incremental_mode = str(mode or "full").lower() == "incremental" and not refresh
            existing_style_fingerprint = _load_jsonish(creator.get("style_fingerprint"))
            existing_identity_fingerprint = _load_jsonish(creator.get("identity_fingerprint"))
            existing_soul_md = creator.get("soul_md") or ""
            fingerprint_updated_at = creator.get("fingerprint_updated_at")
            content_corpus_checksum = str(creator.get("content_corpus_checksum") or "").strip()
            fingerprint_corpus_checksum = str(creator.get("fingerprint_corpus_checksum") or "").strip()

            # Load archetype-derived policy. Drives whether we run web research
            # phases at all and how to weight transcripts. Falls back to a
            # safe "vlogger" policy if the archetype is missing or import fails.
            try:
                from backend.services.fingerprint_policy import get_policy
                arch_dist_raw = creator.get("archetype_distribution")
                arch_dist = _load_jsonish(arch_dist_raw) or {}
                policy = get_policy(
                    creator_archetype=creator.get("creator_archetype") or "vlogger",
                    confidence=float(arch_dist.get("confidence") or 0.0),
                    distribution=arch_dist.get("distribution") or {},
                    llm_profile=arch_dist.get("llm_profile"),
                )
                logger.info(
                    f"FingerprintService: Policy for {name} ({creator_id}) — "
                    f"archetype={policy.archetype} conf={policy.confidence} "
                    f"link_research={policy.enable_link_research} google={policy.enable_google_expansion} "
                    f"voice_w={policy.voice_signal_weight} register={policy.voice_register}"
                )
            except Exception as _pol_exc:  # noqa: BLE001
                logger.warning(f"FingerprintService: policy load failed, using defaults: {_pol_exc}")
                from backend.services.fingerprint_policy import FingerprintPolicy
                policy = FingerprintPolicy()


            if incremental_mode and existing_style_fingerprint and content_corpus_checksum and content_corpus_checksum == fingerprint_corpus_checksum:
                logger.info(f"FingerprintService: Corpus checksum unchanged for {name} ({creator_id}); skipping incremental rebuild.")
                _set_fingerprint_progress(
                    creator_id,
                    status="idle",
                    percent=100,
                    stage="complete",
                    message="Fingerprint already matches the current approved corpus.",
                )
                return
            
            # Gather all available links from config
            configs = creator.get("platform_configs") or {}
            links = []
            for p, cfg in configs.items():
                if isinstance(cfg, dict):
                    if cfg.get("url"): links.append(cfg["url"])
                    elif cfg.get("handle"): links.append(f"https://{p}.com/{cfg['handle'].strip('@')}")

            domains = creator.get("official_domains") or []
            for d in domains:
                if d.startswith("http"): links.append(d)
                else: links.append(f"https://{d}")
            creator_profile = self._build_creator_profile(creator_id, creator, configs, domains)

            cached_summary = _load_cached_research_summary(creator_id)
            reuse_cached_research = (
                not refresh
                and isinstance(cached_summary, dict)
                and bool(cached_summary)
            )

            if reuse_cached_research:
                _set_fingerprint_progress(
                    creator_id,
                    status="processing",
                    percent=24,
                    stage="research_cache",
                    message="Reloading cached dossier material and identity signals to skip duplicate work.",
                )
                logger.info(f"FingerprintService: Reusing cached research summary for {name} ({creator_id}).")
                link_identity = cached_summary.get("identity_research") or {}
                investigative_dossier = cached_summary.get("investigative_dossier") or {}
                creator_claims = cached_summary.get("creator_stated_claims") or []
                unknown_fields = cached_summary.get("unknown_fields") or []
                research_quality = "incremental_cached"
            else:
                # PHASE 1: Link-First Research (Identity & Surface)
                if not policy.enable_link_research:
                    logger.info(
                        f"FingerprintService Phase 1: SKIPPED for {name} per policy "
                        f"(archetype={policy.archetype}). Identity will rely on "
                        f"approved-content signals only."
                    )
                    _set_fingerprint_progress(
                        creator_id,
                        status="processing",
                        percent=22,
                        stage="link_scan_skipped",
                        message=f"Skipping web link research — {policy.archetype} archetype favors content-first analysis.",
                    )
                    link_identity = {}
                    investigative_dossier = {}
                    creator_claims = []
                    unknown_fields = []
                    research_quality = "policy_skipped"
                else:
                    _set_fingerprint_progress(
                        creator_id,
                        status="processing",
                        percent=22,
                        stage="link_scan",
                        message="Walking public links, domains, and profile surfaces for identity clues.",
                    )
                    logger.info(f"FingerprintService Phase 1: Deep link scan for {name}...")
                    if hasattr(self.researcher, "research_links"):
                        link_identity = self.researcher.research_links(links, name)
                        if not isinstance(link_identity, dict):
                            link_identity = {}
                    else:
                        logger.warning("FingerprintService: active research provider has no research_links(); skipping link scan.")
                        link_identity = {}
                    investigative_dossier = {}
                    creator_claims = link_identity.get("creator_claims", [])
                    unknown_fields = link_identity.get("unknown_fields", [])
                    research_quality = "full"

            # PHASE 2: Content-Truth Mining (Voice & Worldview)
            _set_fingerprint_progress(
                creator_id,
                status="processing",
                percent=46,
                stage="voice_analysis",
                message="Mining approved content for cadence, signature moves, values, and recurring beliefs.",
            )
            logger.info(f"FingerprintService Phase 2: Analyzing content truth...")
            if incremental_mode and existing_style_fingerprint and fingerprint_updated_at:
                delta_docs = self.analyzer._load_corpus(creator_id, limit=10, since=fingerprint_updated_at)
                if delta_docs:
                    delta_fingerprint = self.analyzer.analyze_creator(
                        creator_id,
                        limit=10,
                        since=fingerprint_updated_at,
                    )
                    voice_fingerprint = _merge_incremental_fingerprint(existing_style_fingerprint, delta_fingerprint)
                    research_quality = "incremental_cached"
                else:
                    logger.info(f"FingerprintService: No new content since last fingerprint for {name} ({creator_id}).")
                    voice_fingerprint = existing_style_fingerprint
                    research_quality = "incremental_cached"
            else:
                voice_fingerprint = self.analyzer.analyze_creator(creator_id)

            if not isinstance(voice_fingerprint, dict) or not voice_fingerprint:
                voice_fingerprint = existing_style_fingerprint or {
                    "traits": [],
                    "tone_intensity": "low",
                    "impact": "neutral",
                    "mechanical": "none",
                    "lexicon": [],
                    "content_truth": {},
                }

            approved_content_samples = self._load_approved_content_samples(creator_id, limit=8)
            if approved_content_samples:
                voice_pattern_packet = await self._extract_voice_pattern_packet(
                    name,
                    approved_content_samples,
                    voice_fingerprint,
                    persona_modifier=policy.persona_prompt_modifier,
                )
                if voice_pattern_packet:
                    voice_fingerprint = _merge_voice_pattern_packet(voice_fingerprint, voice_pattern_packet)

            # PHASE 3: Targeted Google Expansion (Fill Gaps)
            agentic_bundle: Dict[str, Any] = {}
            if not reuse_cached_research and not incremental_mode and policy.enable_persona_agent and policy.enable_google_expansion:
                _set_fingerprint_progress(
                    creator_id,
                    status="processing",
                    percent=58,
                    stage="persona_agent",
                    message="Running the persona research agent across approved content and grounded public sources.",
                )
                agentic_bundle = await self._run_fingerprint_agent(
                    creator_id=creator_id,
                    creator_name=name,
                    creator_profile=creator_profile,
                    link_identity=link_identity,
                    voice_fingerprint=voice_fingerprint,
                    creator_claims=creator_claims,
                    unknown_fields=unknown_fields,
                )
                if agentic_bundle:
                    link_identity = _merge_incremental_fingerprint(link_identity, agentic_bundle.get("identity_patch") or {})
                    investigative_dossier = _merge_incremental_fingerprint(
                        investigative_dossier,
                        agentic_bundle.get("dossier_patch") or {},
                    )
                    creator_claims = _dedupe_keep_order(
                        (creator_claims or []) + (agentic_bundle.get("creator_claims") or []),
                        limit=10,
                    )
                    unknown_fields = _dedupe_keep_order(
                        (unknown_fields or []) + (agentic_bundle.get("unknown_fields") or []),
                        limit=10,
                    )

                    runtime_anchor_points = agentic_bundle.get("runtime_anchor_points") or []
                    verified_beliefs = agentic_bundle.get("verified_beliefs") or []
                    lexical_markers = agentic_bundle.get("lexical_markers") or {}

                    if runtime_anchor_points:
                        voice_fingerprint["evidence_snippets"] = _dedupe_keep_order(
                            (voice_fingerprint.get("evidence_snippets") or []) + runtime_anchor_points,
                            limit=12,
                        )
                    if verified_beliefs:
                        belief_graph = voice_fingerprint.get("belief_graph") or {}
                        belief_graph["core_beliefs"] = _dedupe_keep_order(
                            (belief_graph.get("core_beliefs") or []) + verified_beliefs,
                            limit=12,
                        )
                        voice_fingerprint["belief_graph"] = belief_graph
                    if lexical_markers:
                        voice_fingerprint["signature_phrases"] = _dedupe_keep_order(
                            (voice_fingerprint.get("signature_phrases") or []) + (lexical_markers.get("signature_phrases") or []),
                            limit=10,
                        )
                        lexical_rules = voice_fingerprint.get("lexical_rules") or {}
                        lexical_rules["signature_phrases"] = _dedupe_keep_order(
                            (lexical_rules.get("signature_phrases") or []) + (lexical_markers.get("signature_phrases") or []),
                            limit=10,
                        )
                        lexical_rules["high_signal_words"] = _dedupe_keep_order(
                            (lexical_rules.get("high_signal_words") or []) + (lexical_markers.get("high_signal_words") or []),
                            limit=18,
                        )
                        lexical_rules["banned_frames"] = _dedupe_keep_order(
                            (lexical_rules.get("banned_frames") or []) + (lexical_markers.get("banned_generic_phrases") or []),
                            limit=12,
                        )
                        voice_fingerprint["lexical_rules"] = lexical_rules

                clues = {
                    "identity_hints": link_identity.get("identity", {}),
                    "content_milestones": voice_fingerprint.get("content_truth", {}),
                    "claims": creator_claims,
                }
                _set_fingerprint_progress(
                    creator_id,
                    status="processing",
                    percent=64,
                    stage="dossier",
                    message="Filling the remaining public identity gaps with targeted research.",
                )
                logger.info(f"FingerprintService Phase 3: Targeted Google expansion...")
                logger.info(f"FingerprintService: Launching Deep Dossier for {name}...")
                if hasattr(self.researcher, "research_dossier"):
                    sequential_dossier = self.researcher.research_dossier(name, clues)
                    if isinstance(sequential_dossier, dict):
                        investigative_dossier = _merge_incremental_fingerprint(investigative_dossier, sequential_dossier)
                else:
                    logger.warning("FingerprintService: active research provider has no research_dossier(); skipping dossier phase.")

                if not _has_meaningful_dossier(investigative_dossier):
                    cached_dossier = _load_cached_dossier_from_creator(creator_id)
                    if cached_dossier:
                        logger.info(f"FingerprintService: Reusing cached dossier for {name} ({creator_id}).")
                        investigative_dossier = cached_dossier
                        research_quality = "cached"

                if not _has_meaningful_dossier(investigative_dossier):
                    fallback_dossier = _fallback_openai_dossier(creator_id, name, creator_profile, clues)
                    if fallback_dossier:
                        logger.info(f"FingerprintService: OpenAI dossier fallback succeeded for {name}.")
                        investigative_dossier = fallback_dossier
                        research_quality = "fallback"

                if not _has_meaningful_dossier(investigative_dossier):
                    research_quality = "partial"
                    investigative_dossier = {}
                elif agentic_bundle:
                    research_quality = agentic_bundle.get("research_quality") or "agentic_grounded"
            elif incremental_mode and reuse_cached_research:
                logger.info(f"FingerprintService: Incremental mode reusing cached dossier for {name} ({creator_id}).")

            # 4. Phase 4: Synthesis (Research Summary)
            _set_fingerprint_progress(
                creator_id,
                status="processing",
                percent=82,
                stage="synthesis",
                message="Combining voice, worldview, and public facts into one runtime model.",
            )
            logger.info(f"FingerprintService Phase 4: Synthesizing research summary...")
            research_summary = {
                "identity_research": link_identity,
                "investigative_dossier": investigative_dossier,
                "creator_stated_claims": creator_claims,
                "content_milestones": voice_fingerprint.get("content_truth", {}),
                "unknown_fields": unknown_fields,
                "research_quality": research_quality,
                "agentic_research": {
                    "tool_trace": (agentic_bundle or {}).get("tool_trace") or [],
                    "fact_registry": (agentic_bundle or {}).get("fact_registry") or [],
                    "grounded_sources": [
                        source
                        for packet in ((agentic_bundle or {}).get("grounding_packets") or [])
                        for source in (packet.get("sources") or [])
                    ][:16],
                    "query_plan": [
                        subquery
                        for packet in ((agentic_bundle or {}).get("grounding_packets") or [])
                        for subquery in (packet.get("query_plan") or [])
                    ][:16],
                } if agentic_bundle else {},
                "persona_seed": {
                    "style_summary": (agentic_bundle or {}).get("style_summary") or "",
                    "identity_summary": (agentic_bundle or {}).get("identity_summary") or "",
                    "runtime_anchor_points": (agentic_bundle or {}).get("runtime_anchor_points") or [],
                    "verified_beliefs": (agentic_bundle or {}).get("verified_beliefs") or [],
                    "audience_contract": (agentic_bundle or {}).get("audience_contract") or [],
                    "soul_seed_markdown": (agentic_bundle or {}).get("soul_seed_markdown") or "",
                } if agentic_bundle else {},
                "last_updated": datetime.now(timezone.utc).isoformat()
            }

            # 5. Build a richer identity layer from web + content.
            identity_fingerprint = _build_identity_fingerprint(name, link_identity, investigative_dossier, voice_fingerprint)
            if incremental_mode and existing_identity_fingerprint:
                identity_fingerprint = _merge_incremental_fingerprint(existing_identity_fingerprint, identity_fingerprint)

            # 6. Phase 5: Persona Narrative Alignment (soul.md)
            if incremental_mode and existing_soul_md:
                _set_fingerprint_progress(
                    creator_id,
                    status="processing",
                    percent=93,
                    stage="finalizing",
                    message="Refreshing runtime fingerprint without regenerating soul.md.",
                )
                soul_md = existing_soul_md
            else:
                _set_fingerprint_progress(
                    creator_id,
                    status="processing",
                    percent=93,
                    stage="finalizing",
                    message="Writing soul.md and locking the final fingerprint for runtime use.",
                )
                soul_md = await self._generate_soul_md(name, creator_id, research_summary, voice_fingerprint, voice_fingerprint)

            # 7. Final DB Update
            current_corpus_checksum = content_corpus_checksum or compute_creator_corpus_checksum(creator_id)
            db.execute_update(
                """
                UPDATE creators 
                SET identity_fingerprint = %s,
                    style_fingerprint = %s,
                    research_summary = %s,
                    soul_md = %s,
                    content_corpus_checksum = %s,
                    fingerprint_corpus_checksum = %s,
                    fingerprint_status = 'idle',
                    fingerprint_progress = %s,
                    fingerprint_updated_at = %s
                WHERE id = %s
                """,
                (
                    json.dumps(identity_fingerprint),
                    json.dumps(voice_fingerprint),
                    json.dumps(research_summary),
                    soul_md,
                    current_corpus_checksum,
                    current_corpus_checksum,
                    json.dumps({
                        "status": "idle",
                        "percent": 100,
                        "stage": "complete",
                        "message": "Fingerprint ready.",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }),
                    datetime.now(timezone.utc),
                    creator_id
                )
            )
            logger.info(f"FingerprintService: Successfully completed deep research for {name} ({creator_id})")

        except Exception as e:
            logger.error(f"FingerprintService Deep Research Error for {creator_id}: {e}")
            import traceback
            traceback.print_exc()
            _set_fingerprint_progress(
                creator_id,
                status="error",
                percent=0,
                stage="error",
                message="Fingerprint generation failed.",
                error=str(e),
            )

    async def _generate_soul_md(self, name: str, creator_id: int, research_summary: Dict[str, Any], voice: Dict[str, Any], style_fingerprint: Dict[str, Any]) -> str:
        """Synthesizes the deep research summary and style fingerprint into a 24-section soul.md document."""
        logger.info(f"FingerprintService: Synthesizing 24-section soul.md for {name}...")

        agentic_research = research_summary.get("agentic_research") or {}
        persona_seed = research_summary.get("persona_seed") or {}

        context_text = f"""
        NAME: {name}
        RESEARCH_SUMMARY: {json.dumps(research_summary)}
        AGENTIC_RESEARCH_TRACE: {json.dumps(agentic_research)}
        PERSONA_SEED: {json.dumps(persona_seed)}
        VOICE_PROFILE: {json.dumps(voice)}
        STYLE_FINGERPRINT_V3: {json.dumps(style_fingerprint)}
        """

        prompt = f"""
        You are a master persona architect. Your goal is to create a 'soul.md' file that serves as the definitive persona anchor for {name}.

        This document will be used to keep an AI assistant strictly in character.

        KNOWLEDGE VS PERSONA RULE:
        - VOICE_PROFILE defines HOW they sound.
        - STYLE_FINGERPRINT_V3 defines what makes them DISTINCT from other creators, what they believe, what stories they repeat, what domains they own, how they reason, and how they behave under pressure.
        - RESEARCH_SUMMARY defines WHO they are in current reality.
        - CONFLICT RESOLUTION: if transcripts conflict with the search dossier on facts, the search dossier is the truth.
        - DIFFERENTIAL PRIORITY: make the creator feel uniquely identifiable, not just well-described.

        WRITE THESE 24 SECTIONS EXACTLY:
        1. CORE IDENTITY LAYER
        2. BEHAVIORAL PATTERNS LAYER
        3. LINGUISTIC DNA LAYER
        4. STRUCTURAL RESPONSE BLUEPRINT
        5. COGNITIVE STYLE
        6. HUMOR DETECTION LAYER
        7. CONFLICT & BOUNDARY RULES
        8. PUBLIC IDENTITY GUARDRAILS
        9. PERSONA INTEGRITY RULES
        10. EMOTIONAL SIGNATURE
        11. AUDIENCE PERCEPTION MODEL
        12. POWER DYNAMICS MODEL
        13. DIFFERENTIAL DNA
        14. ANTI-PERSONA RULES
        15. MODE MATRIX
        16. PRESSURE / STRESS BEHAVIOR
        17. DISTINGUISHING TELLS
        18. GOLDEN REPLIES
        19. BELIEF GRAPH
        20. STORY BANK
        21. TEMPORAL VOICE
        22. KNOWLEDGE BOUNDARIES
        23. CONTRASTIVE NEIGHBORS
        24. RUNTIME RESPONSE RULES

        SECTION REQUIREMENTS:
        - Sections 13-24 must draw heavily from STYLE_FINGERPRINT_V3.
        - Use AGENTIC_RESEARCH_TRACE and PERSONA_SEED as hard evidence for runtime anchors, lexical cues, audience contract, and verified beliefs.
        - DIFFERENTIAL DNA: what makes {name} unlike adjacent creators in the same niche.
        - ANTI-PERSONA RULES: what would make {name} sound fake, generic, or like somebody else.
        - MODE MATRIX: how they behave in greeting, teaching, comfort, rebuke, story, sales, debate, uncertainty, and boundary mode.
        - PRESSURE / STRESS BEHAVIOR: how their voice changes when challenged, when the user is ashamed, when they need to convict, when they need to comfort, and when they need to protect privacy.
        - DISTINGUISHING TELLS: specific worldview, cadence, analogy, and ending patterns that should appear naturally.
        - GOLDEN REPLIES: 1-2 short example replies for greeting, comfort, rebuke, teaching, boundary, uncertainty, and sales.
        - BELIEF GRAPH: what they believe, what they defend, what they attack, where they carry tension or contradiction, and which values or tradeoffs sit under those beliefs.
        - STORY BANK: canonical stories they repeatedly return to, when each should be used, and what lesson each story proves.
        - TEMPORAL VOICE: what stayed stable over time, what evolved, and what belongs to old versus current voice.
        - KNOWLEDGE BOUNDARIES: what is public fact, what is only inferred, what is private, which topics need external verification, and which domains are strong, adjacent, weak, or unsafe.
        - CONTRASTIVE NEIGHBORS: which adjacent creators they could be confused with and the exact cues that separate them.
        - RUNTIME RESPONSE RULES: how belief graph, value model, reasoning profile, story bank, pressure behavior, and boundaries should influence live replies, especially when a question is outside direct evidence.

        FINAL RULE:
        Do not write like a biography. Write like: 'This is who this creator is. This is how they think. This is how they speak. This is how they react.'
        It should feel like you reverse-engineered their brain.

        Output the result as a raw Markdown document.
        """

        try:
            from backend.rag import generate_chat_completion
            resp = generate_chat_completion(
                messages=[
                    {"role": "system", "content": "You are a master of persona synthesis. Create a deep, authentic, and strict soul.md document based on the provided research using the 24-section differential persona blueprint."},
                    {"role": "user", "content": f"Context Data:\n{context_text}\n\n{prompt}"}
                ],
                model=settings.CHAT_MODEL,
                json_mode=False
            )
            return resp
        except Exception as e:
            logger.error(f"Failed to generate soul.md: {e}")
            return f"# {name}\n\nPersona anchor pending analysis."

fingerprint_service = FingerprintService()
