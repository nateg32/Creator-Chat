import json
import os
import re
from datetime import datetime

from backend.db import db
from backend.settings import settings
from backend.services.llm_provider import LLMProviderError, get_gemini_provider
from backend.services.persona_prompts import (
    CREATOR_CONTENT_ANALYSIS_SYSTEM_INSTRUCTION,
    PersonaSynthesisResult,
    build_creator_content_analysis_prompt,
)
from backend.services.style_signal_sanitizer import sanitize_style_fingerprint_for_storage


def _default_fingerprint() -> dict:
    return {
        "schema_version": 3,
        "traits": [],
        "summary": [],
        "signature_phrases": [],
        "recurring_themes": [],
        "teaching_style": [],
        "rhetorical_moves": [],
        "linguistic_dna": {
            "sentence_structure": "varied",
            "energy": "measured",
            "evidence_style": "hybrid",
            "analogy_style": "light",
            "swearing": "none",
            "emoji": "none",
        },
        "behavioral_patterns": {
            "pressure_response": "",
            "disagreement_handling": "",
            "confidence_level": "medium",
            "decision_style": "",
        },
        "cognitive_style": {
            "depth": "hybrid",
            "abstraction": "hybrid",
            "outlook": "realist",
            "orientation": "hybrid",
        },
        "worldview": {
            "core_beliefs": [],
            "values": [],
            "conceptual_enemies": [],
            "moral_hierarchy": [],
        },
        "audience_and_power": {
            "target_audience": "",
            "dynamic": "hybrid",
        },
        "emotional_signature": {
            "temperature": "hybrid",
            "validation_style": "",
            "praise_frequency": "medium",
        },
        "content_truth": {
            "milestones": [],
            "businesses": [],
            "products": [],
            "named_individuals": [],
            "quantified_claims": [],
        },
        "lexicon": [],
        "evidence_snippets": [],
        "identity_signature": {
            "self_concept": "",
            "mission_frame": "",
            "audience_model": "",
            "power_position": "hybrid",
            "public_role": "",
            "private_boundary_style": "",
        },
        "value_hierarchy": [],
        "signature_moves": [],
        "mode_matrix": {
            "greeting": {"opening_move": "", "energy": "", "question_style": "", "forbidden": []},
            "teaching": {"opening_move": "", "proof_style": "", "structure": "", "forbidden": []},
            "comfort": {"opening_move": "", "validation_style": "", "pivot_style": "", "forbidden": []},
            "rebuke": {"opening_move": "", "intensity": "", "boundary_style": "", "forbidden": []},
            "story": {"opening_move": "", "story_shape": "", "lesson_drop": "", "forbidden": []},
            "sales": {"opening_move": "", "trust_mechanism": "", "cta_style": "", "forbidden": []},
            "debate": {"opening_move": "", "friction_style": "", "evidence_posture": "", "forbidden": []},
            "uncertainty": {"admission_style": "", "what_they_never_say": []},
            "boundary": {"private_life_style": "", "moral_limit_style": "", "forbidden": []},
        },
        "pressure_map": {
            "challenged": "",
            "user_insecure": "",
            "user_needs_conviction": "",
            "user_needs_comfort": "",
            "asked_private_question": "",
            "outside_domain": "",
        },
        "analogy_families": [],
        "lexical_rules": {
            "signature_phrases": [],
            "high_signal_words": [],
            "banned_words": [],
            "banned_frames": [],
            "swearing_level": "none",
        },
        "cadence_rules": {
            "sentence_shape": "balanced",
            "question_rate": 0.2,
            "imperative_rate": 0.2,
            "story_vs_list": "hybrid",
            "pause_markers": [],
        },
        "anti_persona": {
            "sounds_like_someone_else_if": [],
            "forbidden_emotional_postures": [],
            "forbidden_generic_coach_lines": [],
            "confusable_with": [],
        },
        "disambiguation_markers": {
            "must_show": [],
            "must_avoid": [],
            "closest_neighbor_creators": [],
        },
        "golden_examples": {
            "greeting": [],
            "comfort": [],
            "rebuke": [],
            "teaching": [],
            "boundary": [],
            "uncertainty": [],
        },
        "belief_graph": {
            "core_beliefs": [],
            "value_hierarchy": [],
            "non_negotiables": [],
            "tension_points": [],
            "beliefs_they_attack": [],
            "beliefs_they_protect": [],
        },
        "domain_map": {
            "creator_lane": "",
            "strong_topics": [],
            "adjacent_topics": [],
            "weak_topics": [],
            "unsafe_topics": [],
        },
        "value_model": {
            "core_values": [],
            "tradeoff_preferences": [],
            "rejections": [],
            "decision_heuristics": [],
        },
        "product_profile": {
            "summary": "",
            "value_summary": "",
            "profile_bullets": [],
        },
        "language_profile": {
            "primary_language": "English",
            "primary_language_code": "en",
            "content_languages": ["English"],
            "script": "Latin",
            "default_reply_language": "match_user",
            "should_default_to_creator_language": False,
            "code_switching_style": "",
            "untranslated_terms": [],
            "confidence": 0.0,
        },
        "search_profile": {
            "primary_category": "",
            "creator_lane": "",
            "search_identity_terms": [],
            "topic_keywords": [],
            "disambiguation_terms": [],
            "negative_query_terms": [],
            "confidence": 0.0,
        },
        "reasoning_profile": {
            "framework_vs_story": "balanced",
            "premise_challenge_rate": "medium",
            "action_bias": "medium",
            "proof_style": "hybrid",
            "emotional_vs_analytical": "balanced",
            "default_problem_solving_pattern": [],
        },
        "unknown_topic_policy": {
            "allow_identity_fallback": True,
            "disclosure_threshold": 0.45,
            "max_assertiveness": 0.65,
            "boundary_style": "",
            "never_infer": [
                "exact facts without evidence",
                "private life",
                "personal history not grounded in content",
                "medical, legal, or financial claims without support",
            ],
        },
        "story_bank": [],
        "pressure_engine": {
            "challenged": {},
            "user_insecure": {},
            "user_ashamed": {},
            "user_flirty": {},
            "user_grieving": {},
            "user_confused": {},
            "user_needs_action": {},
            "user_needs_comfort": {},
            "asked_private_question": {},
            "outside_domain": {},
        },
        "speech_mechanics": {
            "sentence_shape": "balanced",
            "question_density": 0.2,
            "imperative_density": 0.2,
            "analogy_domains": [],
            "signature_openings": [],
            "signature_landings": [],
            "humor_profile": "light",
            "cadence_markers": [],
            "punctuation_rules": [],
        },
        "signature_response_moves": [],
        "contrastive_identity": {
            "nearest_neighbor_creators": [],
            "confusion_risks": [],
            "must_show": [],
            "must_avoid": [],
            "anti_persona": [],
        },
        "temporal_voice": {
            "eras": [],
            "current_voice_vs_old_voice": [],
            "stable_traits": [],
            "drift_signals": [],
        },
        "knowledge_boundaries": {
            "confirmed_public_facts": [],
            "inferred_only": [],
            "private_or_unknown": [],
            "must_verify_topics": [],
        },
        "golden_replies": {
            "teaching": [],
            "comfort": [],
            "rebuke": [],
            "boundary": [],
            "sales": [],
        },
        "scoring": {
            "identity_confidence": 0.5,
            "belief_confidence": 0.5,
            "mode_confidence": 0.5,
            "distinctiveness_score": 0.5,
        },
    }


def _merge_defaults(value, default):
    if isinstance(default, dict):
        merged = {}
        value = value if isinstance(value, dict) else {}
        for key, default_value in default.items():
            merged[key] = _merge_defaults(value.get(key), default_value)
        for key, extra_value in value.items():
            if key not in merged:
                merged[key] = extra_value
        return merged
    if isinstance(default, list):
        return value if isinstance(value, list) else list(default)
    return default if value is None else value


_PRODUCT_PROFILE_NOISE_PATTERNS = (
    re.compile(r"\b(if you['’]?re new to my channel|welcome back to my channel|without further ado|hey guys)\b", re.I),
    re.compile(r"\b(my name is|attached the link below|link below|click below)\b", re.I),
    re.compile(r"\b(forbidden|banned|raw key names|pause markers|lexical markers|high signal words)\b", re.I),
    re.compile(r"\breplies should move through\b", re.I),
    re.compile(r"\buses\s+(dashes|semicolons|commas|ellipses|capitalization)\b", re.I),
    re.compile(r"\b(transcript|yt channel|youtube channel|you can google|stuff you can google|watch this|watch below)\b", re.I),
    re.compile(r"\b(how i got here|want to scale faster|business owners:|include exact numbers and metrics)\b", re.I),
    re.compile(r":\s*(want to|how to|why|what|watch|business owners)\b", re.I),
    re.compile(r"^include\b", re.I),
)


def _dedupe_keep_order(values, limit=None):
    out = []
    seen = set()

    def walk(raw_values):
        if raw_values is None:
            return
        if isinstance(raw_values, (list, tuple, set)):
            for nested in raw_values:
                yield from walk(nested)
            return
        if isinstance(raw_values, dict):
            return
        text = re.sub(r"\s+", " ", str(raw_values)).strip()
        if not text:
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        yield text

    for value in walk(values):
        out.append(value)
        if limit and len(out) >= limit:
            break
    return out


def _clean_product_profile_items(values, limit=8):
    cleaned = []
    for text in _dedupe_keep_order(values):
        if len(text) < 4:
            continue
        if any(pattern.search(text) for pattern in _PRODUCT_PROFILE_NOISE_PATTERNS):
            continue
        cleaned.append(text)
        if limit and len(cleaned) >= limit:
            break
    return cleaned


def _sentence(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    return text if re.search(r"[.!?]$", text) else f"{text}."


def _join_phrase(values, limit=3):
    items = _clean_product_profile_items(values, limit=limit)
    items = [item[:130].rstrip(" ,.;:") for item in items if item]
    if not items:
        return ""
    lowered = [(item[0].lower() + item[1:]) if item else "" for item in items]
    if len(lowered) == 1:
        return lowered[0]
    if len(lowered) == 2:
        return f"{lowered[0]} and {lowered[1]}"
    return f"{', '.join(lowered[:-1])}, and {lowered[-1]}"


def _profile_sentence_candidates(values, limit=10):
    candidates = []
    for item in _dedupe_keep_order(values):
        text = _sentence(item)
        if len(text) < 28:
            continue
        if any(pattern.search(text) for pattern in _PRODUCT_PROFILE_NOISE_PATTERNS):
            continue
        if re.search(r"^[\"']?(if|when|how|why|what|watch|click)\b", text, re.I):
            continue
        if re.search(r"\b(link below|source below|attached)\b", text, re.I):
            continue
        if len(text.split()) < 7:
            continue
        candidates.append(text[:300])
        if limit and len(candidates) >= limit:
            break
    return candidates


def _build_product_profile_bullets(name: str, fingerprint: dict, product_profile: dict) -> list:
    name = name or "This creator"
    identity = fingerprint.get("identity_signature") or {}
    worldview = fingerprint.get("worldview") or {}
    belief_graph = fingerprint.get("belief_graph") or {}
    value_model = fingerprint.get("value_model") or {}
    domain_map = fingerprint.get("domain_map") or {}
    search_profile = fingerprint.get("search_profile") or {}
    reasoning_profile = fingerprint.get("reasoning_profile") or {}
    content_truth = fingerprint.get("content_truth") or {}
    knowledge = fingerprint.get("knowledge_boundaries") or {}
    persona = fingerprint.get("creator_persona") or {}

    bullets = []
    bullets.extend(_profile_sentence_candidates(product_profile.get("profile_bullets") or [], limit=10))
    bullets.append(f"{name}'s current profile is synthesized from approved creator content and verified public research.")

    facts = _join_phrase(
        list(content_truth.get("businesses") or [])
        + list(content_truth.get("products") or [])
        + list(content_truth.get("milestones") or [])
        + list(content_truth.get("quantified_claims") or [])
        + list(identity.get("public_role") and [identity.get("public_role")] or []),
        limit=3,
    )
    if facts:
        bullets.append(f"{name}'s public profile includes {facts}.")

    values = _join_phrase(
        list(product_profile.get("value_summary") and [product_profile.get("value_summary")] or [])
        + list(value_model.get("core_values") or [])
        + list(value_model.get("tradeoff_preferences") or [])
        + list(worldview.get("values") or [])
        + list(worldview.get("moral_hierarchy") or []),
        limit=3,
    )
    if values:
        bullets.append(f"{name} consistently frames decisions around {values}.")

    beliefs = _join_phrase(
        list(belief_graph.get("core_beliefs") or [])
        + list(belief_graph.get("non_negotiables") or [])
        + list(worldview.get("core_beliefs") or []),
        limit=3,
    )
    if beliefs:
        bullets.append(f"{name}'s worldview is built around {beliefs}.")

    teaching = _join_phrase(
        list(fingerprint.get("teaching_style") or [])
        + list(fingerprint.get("rhetorical_moves") or [])
        + list(reasoning_profile.get("default_problem_solving_pattern") or [])
        + list(value_model.get("decision_heuristics") or []),
        limit=3,
    )
    if teaching:
        bullets.append(f"{name}'s teaching pattern centers on {teaching}, keeping complex ideas easier to apply.")

    voice = _join_phrase(
        list(persona.get("voice_summary") and [persona.get("voice_summary")] or [])
        + list(persona.get("cadence") and [persona.get("cadence")] or [])
        + list(persona.get("advice_style") and [persona.get("advice_style")] or [])
        + list(fingerprint.get("traits") or []),
        limit=3,
    )
    if voice:
        bullets.append(f"{name}'s communication style is shaped by {voice}, so replies should feel specific rather than generic.")

    conversation = _join_phrase(
        list(persona.get("response_rules") or [])
        + list(fingerprint.get("signature_response_moves") or [])
        + list(fingerprint.get("signature_moves") or []),
        limit=3,
    )
    if conversation:
        bullets.append(f"{name}'s chat behavior combines {conversation}, keeping replies close to the creator's real content.")

    domain = _join_phrase(
        list(search_profile.get("primary_category") and [search_profile.get("primary_category")] or [])
        + list(search_profile.get("creator_lane") and [search_profile.get("creator_lane")] or [])
        + list(domain_map.get("creator_lane") and [domain_map.get("creator_lane")] or [])
        + list(domain_map.get("strong_topics") or [])
        + list(fingerprint.get("recurring_themes") or []),
        limit=3,
    )
    if domain:
        bullets.append(f"{name}'s strongest domain signal is {domain}.")

    boundaries = _join_phrase(
        list(knowledge.get("must_verify_topics") or [])
        + list(knowledge.get("private_or_unknown") or []),
        limit=3,
    )
    if boundaries:
        bullets.append(f"{name}'s profile needs verification around {boundaries}, especially before stating exact public facts.")

    return _profile_sentence_candidates(bullets, limit=10)


def _backfill_v3_fields(fingerprint: dict, creator_name: str = "This creator") -> dict:
    worldview = fingerprint.get("worldview") or {}
    identity = fingerprint.get("identity_signature") or {}
    audience = fingerprint.get("audience_and_power") or {}
    cadence = fingerprint.get("cadence_rules") or {}
    linguistic = fingerprint.get("linguistic_dna") or {}
    anti = fingerprint.get("anti_persona") or {}
    markers = fingerprint.get("disambiguation_markers") or {}
    content_truth = fingerprint.get("content_truth") or {}
    pressure_map = fingerprint.get("pressure_map") or {}

    if not fingerprint.get("value_hierarchy"):
        fingerprint["value_hierarchy"] = list(worldview.get("moral_hierarchy") or [])
    if not fingerprint.get("signature_moves"):
        fingerprint["signature_moves"] = list(fingerprint.get("rhetorical_moves") or [])
    if not fingerprint.get("signature_response_moves"):
        fingerprint["signature_response_moves"] = list(fingerprint.get("signature_moves") or fingerprint.get("rhetorical_moves") or [])

    lexical_rules = fingerprint.get("lexical_rules") or {}
    if not lexical_rules.get("signature_phrases"):
        lexical_rules["signature_phrases"] = list(fingerprint.get("signature_phrases") or [])
    if not lexical_rules.get("high_signal_words"):
        lexical_rules["high_signal_words"] = list(fingerprint.get("lexicon") or [])
    if not lexical_rules.get("swearing_level"):
        lexical_rules["swearing_level"] = linguistic.get("swearing", "none")
    fingerprint["lexical_rules"] = lexical_rules

    if not identity.get("power_position"):
        identity["power_position"] = audience.get("dynamic", "hybrid")
    if not identity.get("audience_model"):
        identity["audience_model"] = audience.get("target_audience", "")
    fingerprint["identity_signature"] = identity

    belief_graph = fingerprint.get("belief_graph") or {}
    if not belief_graph.get("core_beliefs"):
        belief_graph["core_beliefs"] = list(worldview.get("core_beliefs") or [])
    if not belief_graph.get("value_hierarchy"):
        belief_graph["value_hierarchy"] = list(fingerprint.get("value_hierarchy") or worldview.get("moral_hierarchy") or [])
    if not belief_graph.get("beliefs_they_attack"):
        belief_graph["beliefs_they_attack"] = list(worldview.get("conceptual_enemies") or [])
    fingerprint["belief_graph"] = belief_graph

    domain_map = fingerprint.get("domain_map") or {}
    if not domain_map.get("creator_lane"):
        domain_map["creator_lane"] = ", ".join((fingerprint.get("recurring_themes") or [])[:2])
    if not domain_map.get("strong_topics"):
        domain_map["strong_topics"] = list(fingerprint.get("recurring_themes") or [])[:8]
    if not domain_map.get("unsafe_topics"):
        domain_map["unsafe_topics"] = list((fingerprint.get("knowledge_boundaries") or {}).get("must_verify_topics") or [])
    fingerprint["domain_map"] = domain_map

    search_profile = fingerprint.get("search_profile") or {}
    if not search_profile.get("primary_category"):
        search_profile["primary_category"] = (
            domain_map.get("creator_lane")
            or ", ".join((domain_map.get("strong_topics") or [])[:2])
            or ", ".join((fingerprint.get("recurring_themes") or [])[:2])
        )
    if not search_profile.get("creator_lane"):
        search_profile["creator_lane"] = search_profile.get("primary_category") or domain_map.get("creator_lane") or ""
    if not search_profile.get("topic_keywords"):
        search_profile["topic_keywords"] = _dedupe_keep_order(
            list(domain_map.get("strong_topics") or [])
            + list(fingerprint.get("recurring_themes") or [])
            + list((fingerprint.get("content_truth") or {}).get("products") or [])
            + list((fingerprint.get("content_truth") or {}).get("businesses") or []),
            limit=16,
        )
    if not search_profile.get("search_identity_terms"):
        search_profile["search_identity_terms"] = _dedupe_keep_order(
            [search_profile.get("primary_category"), search_profile.get("creator_lane")]
            + list(search_profile.get("topic_keywords") or [])[:8]
            + list((fingerprint.get("identity_signature") or {}).get("public_role") and [(fingerprint.get("identity_signature") or {}).get("public_role")] or []),
            limit=10,
        )
    if not search_profile.get("disambiguation_terms"):
        search_profile["disambiguation_terms"] = _dedupe_keep_order(
            list(search_profile.get("search_identity_terms") or [])
            + list(domain_map.get("strong_topics") or [])[:4],
            limit=8,
        )
    if not search_profile.get("negative_query_terms"):
        search_profile["negative_query_terms"] = []
    try:
        search_profile["confidence"] = max(
            0.0,
            min(1.0, float(search_profile.get("confidence") or (fingerprint.get("scoring") or {}).get("identity_confidence") or 0.0)),
        )
    except Exception:
        search_profile["confidence"] = 0.0
    fingerprint["search_profile"] = search_profile

    language_profile = fingerprint.get("language_profile") or {}
    creator_persona = fingerprint.get("creator_persona") or {}
    persona_language = creator_persona.get("primary_language") if isinstance(creator_persona, dict) else ""
    language_name = str(language_profile.get("primary_language") or persona_language or "English").strip() or "English"
    is_english_language = language_name.lower() in {"english", "en"}
    if not language_profile.get("primary_language"):
        language_profile["primary_language"] = language_name
    if not language_profile.get("primary_language_code"):
        language_profile["primary_language_code"] = "en" if is_english_language else ""
    if not language_profile.get("content_languages"):
        language_profile["content_languages"] = [language_name]
    if not language_profile.get("script"):
        language_profile["script"] = "Latin" if (language_profile.get("primary_language_code") or "en").startswith("en") else ""
    if not language_profile.get("default_reply_language"):
        language_profile["default_reply_language"] = "match_user" if is_english_language else language_name
    if language_profile.get("should_default_to_creator_language") is None:
        language_profile["should_default_to_creator_language"] = not is_english_language
    if not isinstance(language_profile.get("untranslated_terms"), list):
        language_profile["untranslated_terms"] = []
    try:
        language_profile["confidence"] = max(0.0, min(1.0, float(language_profile.get("confidence") or 0.0)))
    except Exception:
        language_profile["confidence"] = 0.0
    fingerprint["language_profile"] = language_profile

    value_model = fingerprint.get("value_model") or {}
    if not value_model.get("core_values"):
        value_model["core_values"] = list(worldview.get("values") or fingerprint.get("value_hierarchy") or worldview.get("moral_hierarchy") or [])
    if not value_model.get("rejections"):
        value_model["rejections"] = list(worldview.get("conceptual_enemies") or belief_graph.get("beliefs_they_attack") or [])
    if not value_model.get("decision_heuristics"):
        value_model["decision_heuristics"] = list(fingerprint.get("signature_moves") or fingerprint.get("rhetorical_moves") or [])[:8]
    fingerprint["value_model"] = value_model

    product_profile = fingerprint.get("product_profile") or {}
    if not product_profile.get("value_summary"):
        values_preview = _dedupe_keep_order(
            (value_model.get("core_values") or [])
            + (belief_graph.get("core_beliefs") or [])
            + (value_model.get("tradeoff_preferences") or []),
            limit=3,
        )
        if values_preview:
            product_profile["value_summary"] = "; ".join(values_preview)
    if not product_profile.get("summary"):
        summary_bits = _dedupe_keep_order(
            list(fingerprint.get("summary") or [])
            + list(fingerprint.get("traits") or [])
            + list(product_profile.get("value_summary") and [product_profile["value_summary"]] or []),
            limit=2,
        )
        product_profile["summary"] = " ".join(summary_bits)
    product_profile["profile_bullets"] = _build_product_profile_bullets(
        str(fingerprint.get("creator_persona", {}).get("creator_name") or creator_name or "This creator"),
        fingerprint,
        product_profile,
    )
    product_profile.pop("trait_cards", None)
    fingerprint["product_profile"] = product_profile

    reasoning_profile = fingerprint.get("reasoning_profile") or {}
    if not reasoning_profile.get("framework_vs_story"):
        reasoning_profile["framework_vs_story"] = cadence.get("story_vs_list", "balanced")
    if not reasoning_profile.get("proof_style"):
        reasoning_profile["proof_style"] = fingerprint.get("mode_matrix", {}).get("teaching", {}).get("proof_style") or linguistic.get("evidence_style", "hybrid")
    if not reasoning_profile.get("action_bias"):
        reasoning_profile["action_bias"] = fingerprint.get("behavioral_patterns", {}).get("decision_style") or "medium"
    if not reasoning_profile.get("emotional_vs_analytical"):
        temperature = (fingerprint.get("emotional_signature") or {}).get("temperature", "hybrid")
        reasoning_profile["emotional_vs_analytical"] = "emotional" if temperature in {"warm", "hot"} else "balanced"
    if not reasoning_profile.get("default_problem_solving_pattern"):
        reasoning_profile["default_problem_solving_pattern"] = list(fingerprint.get("signature_response_moves") or fingerprint.get("signature_moves") or [])[:6]
    fingerprint["reasoning_profile"] = reasoning_profile

    speech_mechanics = fingerprint.get("speech_mechanics") or {}
    if not speech_mechanics.get("sentence_shape"):
        speech_mechanics["sentence_shape"] = cadence.get("sentence_shape") or linguistic.get("sentence_structure") or "balanced"
    if not speech_mechanics.get("question_density"):
        speech_mechanics["question_density"] = cadence.get("question_rate", 0.2)
    if not speech_mechanics.get("imperative_density"):
        speech_mechanics["imperative_density"] = cadence.get("imperative_rate", 0.2)
    if not speech_mechanics.get("analogy_domains"):
        speech_mechanics["analogy_domains"] = list(fingerprint.get("analogy_families") or [])
    if not speech_mechanics.get("cadence_markers"):
        speech_mechanics["cadence_markers"] = list(cadence.get("pause_markers") or [])
    fingerprint["speech_mechanics"] = speech_mechanics

    pressure_engine = fingerprint.get("pressure_engine") or {}
    for key, text in pressure_map.items():
        if text and not pressure_engine.get(key):
            pressure_engine[key] = {
                "default_move": text,
                "tone_shift": "stay in character",
                "goal": text,
                "forbidden": [],
            }
    fingerprint["pressure_engine"] = pressure_engine

    contrastive = fingerprint.get("contrastive_identity") or {}
    if not contrastive.get("nearest_neighbor_creators"):
        contrastive["nearest_neighbor_creators"] = list(markers.get("closest_neighbor_creators") or anti.get("confusable_with") or [])
    if not contrastive.get("must_show"):
        contrastive["must_show"] = list(markers.get("must_show") or [])
    if not contrastive.get("must_avoid"):
        contrastive["must_avoid"] = list(markers.get("must_avoid") or [])
    if not contrastive.get("anti_persona"):
        contrastive["anti_persona"] = list(anti.get("sounds_like_someone_else_if") or [])
    fingerprint["contrastive_identity"] = contrastive

    knowledge_boundaries = fingerprint.get("knowledge_boundaries") or {}
    if not knowledge_boundaries.get("confirmed_public_facts"):
        confirmed = []
        confirmed.extend(content_truth.get("milestones") or [])
        confirmed.extend(content_truth.get("businesses") or [])
        confirmed.extend(content_truth.get("products") or [])
        knowledge_boundaries["confirmed_public_facts"] = confirmed[:12]
    if not knowledge_boundaries.get("must_verify_topics"):
        knowledge_boundaries["must_verify_topics"] = [
            "age",
            "net worth",
            "family",
            "private life",
            "medical advice",
            "legal advice",
            "financial advice",
            "current events",
        ]
    fingerprint["knowledge_boundaries"] = knowledge_boundaries

    unknown_topic_policy = fingerprint.get("unknown_topic_policy") or {}
    if "allow_identity_fallback" not in unknown_topic_policy:
        unknown_topic_policy["allow_identity_fallback"] = True
    if not isinstance(unknown_topic_policy.get("disclosure_threshold"), (int, float)):
        unknown_topic_policy["disclosure_threshold"] = 0.45
    if not isinstance(unknown_topic_policy.get("max_assertiveness"), (int, float)):
        unknown_topic_policy["max_assertiveness"] = 0.65
    if not unknown_topic_policy.get("boundary_style"):
        unknown_topic_policy["boundary_style"] = fingerprint.get("mode_matrix", {}).get("boundary", {}).get("private_life_style") or identity.get("private_boundary_style") or ""
    if not unknown_topic_policy.get("never_infer"):
        unknown_topic_policy["never_infer"] = list(knowledge_boundaries.get("private_or_unknown") or []) + list(knowledge_boundaries.get("must_verify_topics") or [])
    fingerprint["unknown_topic_policy"] = unknown_topic_policy

    temporal_voice = fingerprint.get("temporal_voice") or {}
    if not temporal_voice.get("stable_traits"):
        temporal_voice["stable_traits"] = list(fingerprint.get("traits") or [])[:5]
    fingerprint["temporal_voice"] = temporal_voice

    story_bank = fingerprint.get("story_bank") or []
    normalized_stories = []
    for idx, story in enumerate(story_bank, start=1):
        if not isinstance(story, dict):
            continue
        normalized_stories.append({
            "story_id": story.get("story_id") or f"story_{idx}",
            "title": story.get("title") or f"Story {idx}",
            "era": story.get("era") or "current",
            "trigger_topics": list(story.get("trigger_topics") or []),
            "summary": story.get("summary") or "",
            "lesson": story.get("lesson") or "",
            "emotion": story.get("emotion") or "measured",
            "proof_type": story.get("proof_type") or "lived_experience",
            "source_refs": list(story.get("source_refs") or []),
            "confidence": story.get("confidence") if isinstance(story.get("confidence"), (int, float)) else 0.5,
        })
    fingerprint["story_bank"] = normalized_stories

    golden_replies = fingerprint.get("golden_replies") or {}
    golden_examples = fingerprint.get("golden_examples") or {}
    for key in ("teaching", "comfort", "rebuke", "boundary"):
        if not golden_replies.get(key):
            golden_replies[key] = list(golden_examples.get(key) or [])
    if not golden_replies.get("sales"):
        golden_replies["sales"] = list(golden_examples.get("teaching") or [])[:2]
    fingerprint["golden_replies"] = golden_replies

    scoring = fingerprint.get("scoring") or {}
    for key in ("identity_confidence", "belief_confidence", "mode_confidence", "distinctiveness_score"):
        if not isinstance(scoring.get(key), (int, float)):
            scoring[key] = 0.5
    fingerprint["scoring"] = scoring

    fingerprint["schema_version"] = 3
    return fingerprint


def _content_coverage_from_docs(docs) -> dict:
    platforms = {}
    content_types = {}
    transcript_statuses = {}
    source_titles = []
    seen_titles = set()

    for doc in docs or []:
        metadata = doc.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}

        platform = str(metadata.get("platform") or doc.get("source") or "unknown").strip().lower() or "unknown"
        platforms[platform] = platforms.get(platform, 0) + 1

        content_type = str(metadata.get("content_type") or metadata.get("type") or "content").strip().lower() or "content"
        content_types[content_type] = content_types.get(content_type, 0) + 1

        transcript_status = str(metadata.get("transcript_status") or "unknown").strip().lower() or "unknown"
        transcript_statuses[transcript_status] = transcript_statuses.get(transcript_status, 0) + 1

        title = str(doc.get("title") or metadata.get("title") or "").strip()
        title_key = title.lower()
        if title and title_key not in seen_titles:
            seen_titles.add(title_key)
            source_titles.append(title)

    return {
        "analyzed_documents": len(docs or []),
        "platform_counts": platforms,
        "content_type_counts": content_types,
        "transcript_status_counts": transcript_statuses,
        "source_titles": source_titles[:20],
    }


class PersonalityAnalyzer:
    """Extract a deeper style fingerprint from ingested creator content."""

    @staticmethod
    def _load_corpus(creator_id: int, *, limit: int = 32, since: datetime | None = None):
        query = """
            SELECT
                COALESCE(chunked.chunk_text, d.content, '') AS content,
                d.metadata,
                d.source,
                d.source_id,
                d.title
            FROM documents d
            LEFT JOIN LATERAL (
                SELECT string_agg(c.chunk_text, E'\n\n' ORDER BY c.chunk_index) AS chunk_text
                FROM (
                    SELECT chunk_index, chunk_text
                    FROM chunks
                    WHERE document_id = d.id
                    ORDER BY chunk_index
                    LIMIT 10
                ) c
            ) chunked ON TRUE
            WHERE d.creator_id = %s AND d.source != 'persona'
        """
        params = [creator_id]
        if since is not None:
            query += """
            AND COALESCE(d.updated_at, d.created_at) > %s
            """
            params.append(since)

        query += """
            ORDER BY d.updated_at DESC NULLS LAST, d.created_at DESC NULLS LAST
            LIMIT %s
        """
        params.append(limit)
        return db.execute_query(query, tuple(params))

    @staticmethod
    def _build_corpus(docs):
        samples = []
        for idx, doc in enumerate(docs, start=1):
            metadata = doc.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}

            title = doc.get("title") or metadata.get("title") or doc.get("source") or f"Sample {idx}"
            platform = metadata.get("platform") or doc.get("source") or "unknown"
            published = metadata.get("published_at") or metadata.get("publishedAt") or ""
            content = (doc.get("content") or "").strip()
            if not content:
                continue
            excerpt = content[:2200]
            header = f"[Sample {idx}] {title} | platform={platform}"
            if published:
                header += f" | published={published}"
            samples.append(f"{header}\n{excerpt}")
        return "\n\n---\n\n".join(samples)

    @staticmethod
    def analyze_creator(creator_id: int, *, limit: int = 32, since: datetime | None = None):
        print(f"[IDENTITY] Re-analyzing fingerprint for creator {creator_id}...")
        limit = max(8, min(int(os.getenv("PERSONA_ANALYSIS_DOC_LIMIT", str(limit or 32))), 96))
        docs = PersonalityAnalyzer._load_corpus(creator_id, limit=limit, since=since)
        if not docs:
            print(f"[IDENTITY] No content found (outside persona) for creator {creator_id}. Cannot analyze.")
            return _default_fingerprint()

        corpus = PersonalityAnalyzer._build_corpus(docs)
        name_row = db.execute_one("SELECT name, handle FROM creators WHERE id = %s", (creator_id,))
        display_name = name_row.get("name") or name_row.get("handle") or "The Creator"

        legacy_system_prompt = """
You are an elite creator intelligence analyst.
Analyze the provided creator corpus and output a DEEP, contrastive style fingerprint for __CREATOR_NAME__.

RULES:
- Use the creator name exactly as provided: __CREATOR_NAME__.
- Ground every trait in the corpus. Do not invent facts.
- Prefer specificity over generic praise.
- If a fact is uncertain, omit it rather than soften it.
- Capture how the creator thinks, teaches, frames problems, uses evidence, and presents identity.
- Most important: identify what makes this creator DISTINCTIVE, not just competent.
- Extract stories they repeat, pressures that change their tone, and beliefs they defend or attack.
- Infer a practical domain map, value model, reasoning profile, and unknown-topic policy from recurring patterns in the corpus.
- Fill contrastive_identity, anti_persona, and disambiguation_markers aggressively.
- Preserve the older fields for compatibility, but prioritize the v3 fields.

Return JSON only with this schema:
{
  "schema_version": 3,
  "traits": ["5-8 concrete trait statements using exact creator name"],
  "summary": ["3-5 dense summary bullets about identity and thinking"],
  "signature_phrases": ["exact or near-exact repeated phrases"],
  "recurring_themes": ["themes they repeatedly return to"],
  "teaching_style": ["how they teach or persuade"],
  "rhetorical_moves": ["repeatable response moves or sequencing patterns"],
  "identity_signature": {
    "self_concept": "",
    "mission_frame": "",
    "audience_model": "",
    "power_position": "mentor|challenger|friend|authority|hybrid",
    "public_role": "",
    "private_boundary_style": ""
  },
  "belief_graph": {
    "core_beliefs": [""],
    "value_hierarchy": [""],
    "non_negotiables": [""],
    "tension_points": ["where they contain real contradiction or evolution"],
    "beliefs_they_attack": [""],
    "beliefs_they_protect": [""]
  },
  "domain_map": {
    "creator_lane": "short phrase for their main lane",
    "strong_topics": ["topics they can answer with high confidence"],
    "adjacent_topics": ["topics they can discuss through worldview and reasoning"],
    "weak_topics": ["topics they touch lightly"],
    "unsafe_topics": ["topics where inference should usually stop"]
  },
  "value_model": {
    "core_values": [""],
    "tradeoff_preferences": ["what they would prioritize over what"],
    "rejections": ["ideas, habits, or mindsets they reject"],
    "decision_heuristics": ["repeatable rules they use to make decisions"]
  },
  "product_profile": {
    "summary": "2 concise sentences for the Persona page explaining the strongest usable creator signals",
    "value_summary": "1 concise sentence focused on values, beliefs, and decision standards",
    "profile_bullets": ["8-10 polished user-facing Current Profile bullets covering public profile, values, worldview, teaching, voice, conversation behavior, domain, audience relationship, proof mechanisms, and boundaries"]
  },
  "search_profile": {
    "primary_category": "2-5 word actual field/category from approved content, not a generic platform label",
    "creator_lane": "short phrase describing what this creator is publicly known for",
    "search_identity_terms": ["durable terms to add to web searches for this creator"],
    "topic_keywords": ["strong content topics from approved content and source titles"],
    "disambiguation_terms": ["terms that distinguish this creator from same-name people"],
    "negative_query_terms": ["terms likely to mean a different creator/person"],
    "confidence": 0.0
  },
  "reasoning_profile": {
    "framework_vs_story": "framework|story|balanced",
    "premise_challenge_rate": "low|medium|high",
    "action_bias": "low|medium|high",
    "proof_style": "anecdotal|analytical|hybrid",
    "emotional_vs_analytical": "emotional|balanced|analytical",
    "default_problem_solving_pattern": ["how they usually structure an answer or solve a problem"]
  },
  "unknown_topic_policy": {
    "allow_identity_fallback": true,
    "disclosure_threshold": 0.0,
    "max_assertiveness": 0.0,
    "boundary_style": "how they should set limits on unsupported topics",
    "never_infer": ["facts or stance categories that should never be guessed"]
  },
  "story_bank": [
    {
      "story_id": "short id",
      "title": "canonical short title",
      "era": "old|current|timeless",
      "trigger_topics": ["topics that should retrieve this story"],
      "summary": "story summary",
      "lesson": "lesson they extract from it",
      "emotion": "tone of the story",
      "proof_type": "lived_experience|client_result|warning|origin_story|failure_story",
      "source_refs": ["sample ids or titles"],
      "confidence": 0.0
    }
  ],
  "mode_matrix": {
    "greeting": {"opening_move": "", "energy": "", "question_style": "", "forbidden": []},
    "teaching": {"opening_move": "", "proof_style": "", "structure": "", "forbidden": []},
    "comfort": {"opening_move": "", "validation_style": "", "pivot_style": "", "forbidden": []},
    "rebuke": {"opening_move": "", "intensity": "", "boundary_style": "", "forbidden": []},
    "story": {"opening_move": "", "story_shape": "", "lesson_drop": "", "forbidden": []},
    "sales": {"opening_move": "", "trust_mechanism": "", "cta_style": "", "forbidden": []},
    "debate": {"opening_move": "", "friction_style": "", "evidence_posture": "", "forbidden": []},
    "uncertainty": {"admission_style": "", "what_they_never_say": []},
    "boundary": {"private_life_style": "", "moral_limit_style": "", "forbidden": []}
  },
  "pressure_engine": {
    "challenged": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_insecure": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_ashamed": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_flirty": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_grieving": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_confused": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_needs_action": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_needs_comfort": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "asked_private_question": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "outside_domain": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []}
  },
  "speech_mechanics": {
    "sentence_shape": "short_bursts|balanced|flowing",
    "question_density": 0.0,
    "imperative_density": 0.0,
    "analogy_domains": [""],
    "signature_openings": [""],
    "signature_landings": [""],
    "humor_profile": "",
    "cadence_markers": [""],
    "punctuation_rules": [""]
  },
  "signature_moves": [""],
  "signature_response_moves": [""],
  "contrastive_identity": {
    "nearest_neighbor_creators": [""],
    "confusion_risks": [""],
    "must_show": [""],
    "must_avoid": [""],
    "anti_persona": [""]
  },
  "temporal_voice": {
    "eras": [""],
    "current_voice_vs_old_voice": [""],
    "stable_traits": [""],
    "drift_signals": [""]
  },
  "knowledge_boundaries": {
    "confirmed_public_facts": [""],
    "inferred_only": [""],
    "private_or_unknown": [""],
    "must_verify_topics": [""]
  },
  "analogy_families": [""],
  "lexical_rules": {
    "signature_phrases": [""],
    "high_signal_words": [""],
    "banned_words": [""],
    "banned_frames": [""],
    "swearing_level": "none|low|medium|high"
  },
  "cadence_rules": {
    "sentence_shape": "short_bursts|balanced|flowing",
    "question_rate": 0.0,
    "imperative_rate": 0.0,
    "story_vs_list": "story|list|hybrid",
    "pause_markers": [""]
  },
  "anti_persona": {
    "sounds_like_someone_else_if": [""],
    "forbidden_emotional_postures": [""],
    "forbidden_generic_coach_lines": [""],
    "confusable_with": [""]
  },
  "disambiguation_markers": {
    "must_show": [""],
    "must_avoid": [""],
    "closest_neighbor_creators": [""]
  },
  "golden_examples": {
    "greeting": [""],
    "comfort": [""],
    "rebuke": [""],
    "teaching": [""],
    "boundary": [""],
    "uncertainty": [""]
  },
  "golden_replies": {
    "teaching": [""],
    "comfort": [""],
    "rebuke": [""],
    "boundary": [""],
    "sales": [""]
  },
  "scoring": {
    "identity_confidence": 0.0,
    "belief_confidence": 0.0,
    "mode_confidence": 0.0,
    "distinctiveness_score": 0.0
  },
  "content_truth": {
    "milestones": [""],
    "businesses": [""],
    "products": [""],
    "named_individuals": [""],
    "quantified_claims": [""]
  },
  "lexicon": [""],
  "evidence_snippets": ["3-6 short evidence-backed observations"]
}
""".replace("__CREATOR_NAME__", display_name)

        prompt = build_creator_content_analysis_prompt(
            creator_name=display_name,
            corpus=corpus,
            existing_schema_hint=_default_fingerprint(),
        )

        try:
            result = get_gemini_provider().generate_json(
                system_instruction=CREATOR_CONTENT_ANALYSIS_SYSTEM_INSTRUCTION,
                prompt=prompt,
                schema_model=PersonaSynthesisResult,
                model=settings.GEMINI_ANALYSIS_MODEL,
                temperature=0.2,
                repair_label="creator persona synthesis",
            )
            fingerprint = _merge_defaults(result.style_fingerprint, _default_fingerprint())
            fingerprint["analysis_md"] = result.analysis_markdown
            content_truth = fingerprint.get("content_truth") or {}
            coverage = _content_coverage_from_docs(docs)
            content_truth["coverage"] = coverage
            content_truth["source_titles"] = list(dict.fromkeys(
                list(content_truth.get("source_titles") or []) + list(coverage.get("source_titles") or [])
            ))[:20]
            fingerprint["content_truth"] = content_truth
            persona_payload = (
                result.creator_persona.model_dump()
                if hasattr(result.creator_persona, "model_dump")
                else result.creator_persona.dict()
            )
            fingerprint["creator_persona"] = persona_payload
            fingerprint = _backfill_v3_fields(fingerprint, display_name)
            fingerprint = sanitize_style_fingerprint_for_storage(fingerprint)
            db.execute_update(
                "UPDATE creators SET style_fingerprint = %s WHERE id = %s",
                (json.dumps(fingerprint), creator_id),
            )
            print(f"Successfully updated style fingerprint for creator {creator_id}")
            return fingerprint
        except LLMProviderError as e:
            print(f"Gemini persona analysis failed: {e}")
            return _default_fingerprint()
        except Exception as e:
            print(f"Failed to analyze personality: {e}")
            return _default_fingerprint()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        PersonalityAnalyzer.analyze_creator(int(sys.argv[1]))
    else:
        print("Usage: python personality_analyzer.py <creator_id>")
