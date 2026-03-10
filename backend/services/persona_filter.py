import json
import logging
from typing import Any, Dict, Optional

import backend.rag as rag
from backend.services.text_sanitizer import strip_mid_sentence_hyphens
from backend.settings import settings

logger = logging.getLogger(__name__)


def _enforce_distinctiveness(
    text: str,
    creator_name: str,
    style_fingerprint: Optional[Dict[str, Any]] = None,
) -> str:
    style_fingerprint = style_fingerprint or {}
    anti = style_fingerprint.get("anti_persona") or {}
    markers = style_fingerprint.get("disambiguation_markers") or {}
    contrastive = style_fingerprint.get("contrastive_identity") or {}
    golden = style_fingerprint.get("golden_replies") or {}

    if not any([markers, anti, contrastive, golden]):
        return strip_mid_sentence_hyphens(text)

    system_prompt = f"""
You are the CONTRASTIVE PERSONA JUDGE for {creator_name}.

Your job is to decide whether a message sounds uniquely like {creator_name}, not like a generic expert or adjacent creator.

DISTINCTIVENESS RULES:
- Reward worldview specificity, believable emotional posture, and creator specific response moves.
- Penalize generic coach language, safe filler, and anything that could belong to a dozen creators.
- Keep the same meaning. Do not add facts.

OUTPUT JSON WITH EXACTLY THESE KEYS:
{{
  "distinctiveness_score": 0.0,
  "generic_leaks": [],
  "missing_signals": [],
  "rewrite_needed": false,
  "final_text": ""
}}
"""

    user_prompt = f"""
CREATOR: {creator_name}
MUST SHOW: {json.dumps((contrastive.get('must_show') or markers.get('must_show') or [])[:8])}
MUST AVOID: {json.dumps((contrastive.get('must_avoid') or markers.get('must_avoid') or [])[:8])}
CONFUSION RISKS: {json.dumps((contrastive.get('confusion_risks') or [])[:5])}
NEAREST NEIGHBORS: {json.dumps((contrastive.get('nearest_neighbor_creators') or markers.get('closest_neighbor_creators') or [])[:5])}
ANTI PERSONA: {json.dumps((contrastive.get('anti_persona') or anti.get('sounds_like_someone_else_if') or [])[:6])}
FORBIDDEN GENERIC LINES: {json.dumps((anti.get('forbidden_generic_coach_lines') or [])[:6])}
FORBIDDEN POSTURES: {json.dumps((anti.get('forbidden_emotional_postures') or [])[:6])}
GOLDEN REPLIES: {json.dumps(golden)}

MESSAGE:
{text}

If the message is already distinct, return it as final_text unchanged.
If it is too generic, rewrite it so it feels more unmistakably like {creator_name}.
"""

    try:
        verdict = rag.generate_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=settings.REWRITE_MODEL,
            temperature=0.0,
            json_mode=True,
        )
        parsed = json.loads(verdict) if isinstance(verdict, str) else verdict
        final_text = (parsed or {}).get("final_text") or text
        return strip_mid_sentence_hyphens(final_text.strip().strip('"'))
    except Exception as e:
        logger.error(f"Distinctiveness enforcement failed: {e}")
        return strip_mid_sentence_hyphens(text)


def apply_persona_surface_filter(
    text: str,
    intent: str,
    voice_profile: Optional[Dict[str, Any]] = None,
    creator_name: str = "The Creator",
    style_fingerprint: Optional[Dict[str, Any]] = None,
) -> str:
    """Final polish step to ensure responses sound human and distinctly creator specific."""
    anti = (style_fingerprint or {}).get("anti_persona") or {}
    markers = (style_fingerprint or {}).get("disambiguation_markers") or {}
    contrastive = (style_fingerprint or {}).get("contrastive_identity") or {}

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
- MUST SHOW WHEN NATURAL: {contrastive.get('must_show') or markers.get('must_show', [])}
- MUST AVOID: {contrastive.get('must_avoid') or markers.get('must_avoid', [])}
- CONFUSION RISKS: {contrastive.get('confusion_risks', [])}
- NEAREST NEIGHBORS: {contrastive.get('nearest_neighbor_creators') or markers.get('closest_neighbor_creators', [])}
- FORBIDDEN GENERIC COACH LINES: {anti.get('forbidden_generic_coach_lines', [])}
- FORBIDDEN EMOTIONAL POSTURES: {anti.get('forbidden_emotional_postures', [])}
- SOUNDS FAKE IF: {anti.get('sounds_like_someone_else_if', [])}
""" if style_fingerprint else ""

    system_prompt = f"""
You are the IDENTITY and PERSONA ENFORCER for {creator_name}.

CRITICAL: Your only goal is to purge all system voice and AI identity leaks. The output must be indistinguishable from a real human message from {creator_name}.

1. REMOVE SYSTEM VOICE
- Remove meta language, disclaimers, and assistant phrasing.
- Remove librarian and search engine phrasing.
- Remove generic coach filler.

2. PRESERVE CREATOR UNIQUENESS
- Keep the creator worldview and emotional posture intact.
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
- Keep it human and DM natural.
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
                {"role": "user", "content": user_prompt},
            ],
            model=settings.REWRITE_MODEL,
            temperature=0.0,
        )
        filtered = strip_mid_sentence_hyphens(filtered.strip().strip('"'))
        return _enforce_distinctiveness(filtered, creator_name, style_fingerprint=style_fingerprint)
    except Exception as e:
        logger.error(f"Persona Surface Filter failed: {e}")
        return strip_mid_sentence_hyphens(text)
