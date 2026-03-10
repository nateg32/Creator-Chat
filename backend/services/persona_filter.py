import logging
from typing import Dict, Any, Optional
import backend.rag as rag
from backend.settings import settings

logger = logging.getLogger(__name__)


def apply_persona_surface_filter(
    text: str,
    intent: str,
    voice_profile: Optional[Dict[str, Any]] = None,
    creator_name: str = "The Creator",
    style_fingerprint: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Final polish step to ensure response sounds like a natural human
    and removes any leaked system logic or meta-statements.
    """
    anti = (style_fingerprint or {}).get("anti_persona") or {}
    markers = (style_fingerprint or {}).get("disambiguation_markers") or {}

    vp_str = ""
    if voice_profile:
        vp_str = f"""
CREATOR STYLE CONTEXT:
- Vocabulary: {voice_profile.get('signature_phrases', [])}
- Bluntness: {(voice_profile.get('attitude') or {}).get('bluntness', 'balanced')}
- Energy: {(voice_profile.get('energy') or {}).get('bucket', 'MID')}
"""

    differential_str = f"""
DIFFERENTIAL PERSONA RULES:
- MUST SHOW WHEN NATURAL: {markers.get('must_show', [])}
- MUST AVOID: {markers.get('must_avoid', [])}
- FORBIDDEN GENERIC COACH LINES: {anti.get('forbidden_generic_coach_lines', [])}
- FORBIDDEN EMOTIONAL POSTURES: {anti.get('forbidden_emotional_postures', [])}
- SOUNDS FAKE IF: {anti.get('sounds_like_someone_else_if', [])}
""" if style_fingerprint else ""

    system_prompt = f"""
You are the IDENTITY & PERSONA ENFORCER for {creator_name}.

CRITICAL: Your only goal is to purge ALL system voice and AI-identity leaks. The output must be indistinguishable from a real human message from {creator_name}.

1. REMOVE SYSTEM VOICE
- Remove meta language, disclaimers, and assistant phrasing.
- Remove librarian/search-engine phrasing.
- Remove generic coach filler.

2. PRESERVE CREATOR UNIQUENESS
- Keep the creator's worldview and emotional posture intact.
- If the message sounds like anyone could have said it, sharpen it.
- Add 1 subtle differentiating tell only if it fits naturally.
{vp_str}
{differential_str}

3. HARD BLOCKS
- No references to AI, models, systems, verification, retrieval, or limitations.
- No 'according to', 'based on the content', 'I can assist', 'let me know', or 'I am here to help'.
- No lines listed under MUST AVOID or FORBIDDEN GENERIC COACH LINES.

4. STRUCTURE
- Keep the original meaning.
- Keep it human and DM-natural.
- Do not add explanation about the rewrite.

OUTPUT: THE REWRITTEN MESSAGE ONLY.
"""

    user_prompt = f"""
MESSAGE TO REWRITE:
{text}

REWRITE IT SO IT SOUNDS LIKE {creator_name}, NOT A SYSTEM.
"""

    try:
        filtered = rag.generate_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model=settings.REWRITE_MODEL,
            temperature=0.0
        )
        return filtered.strip().strip('"')
    except Exception as e:
        logger.error(f"Persona Surface Filter failed: {e}")
        return text
