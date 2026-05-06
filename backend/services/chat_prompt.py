"""Chat prompt helpers for persona-aware creator-style delivery."""

from __future__ import annotations

import json
from typing import Any, Dict


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
        return persona
    research = _load_dict(creator_profile.get("research_summary"))
    artifacts = _load_dict(research.get("persona_artifacts"))
    runtime_prompt_md = str(artifacts.get("runtime_prompt_md") or "").strip()
    if runtime_prompt_md:
        return {"runtime_prompt_md": runtime_prompt_md}
    persona_seed = _load_dict(research.get("persona_seed"))
    if persona_seed:
        return persona_seed
    return {}


def build_creator_style_disclosure_prompt(creator_profile: Dict[str, Any], creator_name: str) -> str:
    """Build a compact prompt layer from stored persona JSON or soul.md."""
    persona = extract_creator_persona(creator_profile)
    soul_md = str(creator_profile.get("soul_md") or "").strip()
    payload = persona if persona else {"soul_profile": soul_md[:2400]}
    return f"""
AUTHORIZED CREATOR-STYLE ASSISTANT:
- You are an AI assistant authorized to answer in {creator_name}'s style from stored creator profile data.
- Do not claim to literally be the real person, have their body, private memories, inbox, finances, family access, or current offline experiences.
- Speak in first person only as a product convention when summarizing the creator's public ideas or content.
- If the user asks what you are, say plainly that this is an AI creator-style assistant trained from approved creator content.
- Preserve the creator's cadence, worldview, vocabulary, and advice style from this persona data:
{json.dumps(payload, ensure_ascii=False)}
"""
