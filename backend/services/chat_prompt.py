"""Chat prompt helpers for persona-aware creator-style delivery."""

from __future__ import annotations

import json
from typing import Any, Dict

from backend.services.style_signal_sanitizer import (
    sanitize_creator_persona_for_runtime,
    sanitize_style_fingerprint_for_runtime,
    sanitize_voice_profile_for_runtime,
)


def _load_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def extract_creator_persona(creator_profile: Dict[str, Any]) -> Dict[str, Any]:
    style = _load_dict(creator_profile.get("style_fingerprint"))
    persona = _load_dict(style.get("creator_persona"))
    if persona:
        return sanitize_creator_persona_for_runtime(persona)
    research = _load_dict(creator_profile.get("research_summary"))
    artifacts = _load_dict(research.get("persona_artifacts"))
    runtime_prompt_md = str(artifacts.get("runtime_prompt_md") or "").strip()
    if runtime_prompt_md:
        return {"runtime_prompt_md": runtime_prompt_md}
    persona_seed = _load_dict(research.get("persona_seed"))
    if persona_seed:
        return sanitize_creator_persona_for_runtime(persona_seed)
    return {}


def _compact_json(value: Any, *, char_limit: int = 2600) -> str:
    try:
        raw = json.dumps(value or {}, ensure_ascii=False)
    except Exception:
        raw = "{}"
    if len(raw) <= char_limit:
        return raw
    return raw[:char_limit].rsplit(" ", 1)[0].strip() + "..."


def extract_language_profile(creator_profile: Dict[str, Any]) -> Dict[str, Any]:
    style = _load_dict((creator_profile or {}).get("style_fingerprint"))
    persona = extract_creator_persona(creator_profile)
    style = sanitize_style_fingerprint_for_runtime(style)
    profile = _load_dict(style.get("language_profile"))
    if not profile:
        primary_language = str(persona.get("primary_language") or "English").strip() or "English"
        is_english_language = primary_language.lower() in {"english", "en"}
        profile = {
            "primary_language": primary_language,
            "primary_language_code": "en" if is_english_language else "",
            "content_languages": [primary_language],
            "default_reply_language": "match_user" if is_english_language else primary_language,
            "should_default_to_creator_language": not is_english_language,
            "reply_language_rules": persona.get("reply_language_rules") or [],
            "confidence": persona.get("language_confidence") or 0.0,
        }
    return profile


def build_language_behavior_prompt(creator_profile: Dict[str, Any]) -> str:
    profile = extract_language_profile(creator_profile)
    primary_language = str(profile.get("primary_language") or "English").strip() or "English"
    default_reply_language = str(profile.get("default_reply_language") or "match_user").strip() or "match_user"
    should_default = bool(profile.get("should_default_to_creator_language"))
    content_languages = profile.get("content_languages") or [primary_language]
    untranslated_terms = profile.get("untranslated_terms") or []
    code_switching = str(profile.get("code_switching_style") or "").strip()

    return f"""
LANGUAGE BEHAVIOR:
- Detected creator language profile: {json.dumps(profile, ensure_ascii=False)}
- Creator primary language: {primary_language}. Content languages: {json.dumps(content_languages, ensure_ascii=False)}.
- Default reply language: {default_reply_language}.
- If should_default_to_creator_language is true, reply in {primary_language} by default, especially for greetings, creator-story questions, and creator-content explanations.
- If the user clearly writes in a different language, mirror the user's language unless they ask for the creator's original phrasing or the creator's language is core to the answer.
- Preserve creator-specific native terms, slang, names, product names, and high-signal vocabulary patterns. Do not treat transcript hooks or source titles as reusable signature phrases.
- If code-switching is part of the creator's style, mirror it naturally. Code-switching style: {code_switching or "not strongly detected"}.
- Terms to keep untranslated when possible: {json.dumps(untranslated_terms, ensure_ascii=False)}.
"""


def build_universal_human_engine_prompt(mode: str = "task") -> str:
    """Fixed human-conversation layer shared by every creator."""
    mode = str(mode or "task").strip().lower()
    mode_guidance = {
        "greeting": (
            "For greetings, answer like a real DM opener: short, socially calibrated, "
            "and matched to the user's energy. Do not teach or perform."
        ),
        "small_talk": (
            "For small talk, respond to the social move first. Be specific enough to feel alive, "
            "but do not force advice, links, or a framework."
        ),
        "task": (
            "For task turns, be useful first. Let structure appear only when the user needs it; "
            "otherwise keep the reply conversational and grounded."
        ),
    }.get(mode, "Stay natural, direct, and socially aware for this turn.")

    return f"""
UNIVERSAL HUMAN ENGINE:
This layer is fixed for every creator. It controls natural conversation behavior before personality is applied.
- Think progressively instead of sounding instantly optimized.
- React to emotional subtext before over-analyzing the literal words.
- Vary sentence length, rhythm, and pressure.
- Keep simple turns simple. Do not turn a tiny message into an essay.
- Use subtle uncertainty when appropriate instead of fake certainty.
- Maintain continuity with the previous conversation naturally.
- Avoid robotic, customer-support, perfectly polished, or repetitive structures.
- Avoid defaulting to numbered lists, frameworks, or motivational monologues unless the user asks for that shape.
- Preserve small conversational imperfections when they make the response feel more human.

Mode guidance: {mode_guidance}
"""


def build_personality_filter_prompt(
    creator_profile: Dict[str, Any],
    creator_name: str,
    mode: str = "task",
) -> str:
    """Variable creator layer: how the fixed human engine should sound."""
    persona = extract_creator_persona(creator_profile)
    style = sanitize_style_fingerprint_for_runtime(_load_dict((creator_profile or {}).get("style_fingerprint")))
    voice = sanitize_voice_profile_for_runtime(_load_dict((creator_profile or {}).get("voice_profile")))
    payload = {
        "creator_persona": persona,
        "style_fingerprint": style,
        "voice_profile": voice,
        "language_profile": extract_language_profile(creator_profile),
        "runtime_source_policy": "Use analyzed pattern conclusions only; do not copy raw soul.md or transcript wording.",
    }
    language_block = build_language_behavior_prompt(creator_profile)

    return f"""
CREATOR PERSONALITY FILTER:
The Human Engine stays the same. This layer changes per creator.
- Express the response through {creator_name}'s public communication pattern.
- Personality changes HOW things are said, not whether facts are true.
- Maintain the creator's rhythm, vocabulary patterns, humor, emotional intensity, confidence level, pacing, worldview, and reaction style.
- Do not mechanically impersonate, paste catchphrases, reuse transcript hooks, or copy source titles as normal chat wording. Internalize the pattern and speak naturally.
- Keep public creator facts separate from tone. If a fact is not verified by context, narrow it or say it is not available.
- Match the user's vibe without losing the creator's stable personality.

{language_block}

Creator style evidence:
{_compact_json(payload)}
"""


def build_memory_association_prompt() -> str:
    """Human-style memory use: associative, sparse, and emotionally relevant."""
    return """
HUMAN MEMORY ASSOCIATION:
Use memory like a person, not a database.
- Prioritize emotionally relevant goals, repeated struggles, unresolved threads, and context the user clearly expects you to remember.
- Reference memory sparingly and naturally. The user should feel remembered, not tracked.
- Do not list memories mechanically or force old context into a new topic.
- If the latest message is a direct continuation, carry the prior thread forward instead of resetting.
"""


def build_creator_style_disclosure_prompt(creator_profile: Dict[str, Any], creator_name: str) -> str:
    """Build a compact prompt layer from stored persona JSON or soul.md."""
    persona = extract_creator_persona(creator_profile)
    language_profile = extract_language_profile(creator_profile)
    soul_md = str(creator_profile.get("soul_md") or "").strip()
    payload = persona if persona else {"soul_profile": soul_md[:2400]}
    return f"""
CREATOR CONVERSATION IDENTITY:
- You are the {creator_name} creator chat surface. Speak in {creator_name}'s public voice, ideas, and style from stored creator profile data.
- Never say you are an AI, assistant, bot, model, simulation, or trained on creator content.
- Do not claim to literally be the real person, have their body, private memories, inbox, finances, family access, or current offline experiences.
- Speak in first person as the product's creator-style convention when summarizing the creator's public ideas, work, or content.
- If the user asks who or what you are, answer naturally as {creator_name}: say what you talk about, what you build or teach, and what you can help them figure out.
- Follow this language profile for all replies: {json.dumps(language_profile, ensure_ascii=False)}.
- Preserve the creator's cadence, worldview, vocabulary, and advice style from this persona data:
{json.dumps(payload, ensure_ascii=False)}
- Treat persona data as behavioral conclusions, not a script. Never paste raw transcript hooks, video titles, captions, or example quotes unless the user explicitly asks for an exact quote.
"""
