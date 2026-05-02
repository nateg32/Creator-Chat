
import logging
import json
import random
from typing import Dict, Any, Optional
import backend.rag as rag
from backend.settings import settings

logger = logging.getLogger(__name__)


class StrongholdGuardService:
    """
    Implements Creator Stronghold boundaries.
    Ensures the bot stays within primary/secondary domains.
    """

    def calculate_domain_match(
        self,
        question: str,
        stronghold_config: Dict[str, Any],
        detected_domain: str
    ) -> str:
        """
        Determines the domain action based on the detected domain and creator config.
        Returns: 'ANSWER' | 'CAUTIOUS' | 'BRIDGE' | 'DECLINE_HANDOFF'
        """
        primary = stronghold_config.get("primary_domains", [])
        secondary = stronghold_config.get("secondary_domains", [])
        bridge = stronghold_config.get("allowed_bridge_domains", [])
        out_of_scope = stronghold_config.get("out_of_scope_domains", [])

        detected_domain = detected_domain.lower()
        primary = [d.lower() for d in primary]
        secondary = [d.lower() for d in secondary]
        bridge = [d.lower() for d in bridge]
        out_of_scope = [d.lower() for d in out_of_scope]

        if detected_domain in primary:
            return "ANSWER"
        if detected_domain in secondary:
            return "CAUTIOUS"
        if detected_domain in bridge:
            return "BRIDGE"
        if detected_domain in out_of_scope:
            return "DECLINE_HANDOFF"

        # Default: when the detected domain isn't explicitly listed, ANSWER.
        # The dedicated general-knowledge and live-fact rules already block
        # clearly off-scope topics; defaulting to DECLINE here makes the bot
        # refuse adjacent questions (e.g. a business coach being asked about
        # "starting a business") and sound like a robotic assistant.
        return "ANSWER"

    def generate_boundary_message(
        self,
        creator_name: str,
        persona: str,
        stronghold_config: Dict[str, Any],
        user_message: str,
        recent_topic: Optional[str] = None,
        creator_focus: Optional[str] = None,
        allow_handoff: bool = True,
        creator_profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Generates a short in character boundary or bridge message when a request
        is genuinely outside the creator's world.

        The output must sound like the actual creator brushing the topic off in
        their own voice — not a polite assistant reading a script. Hardcoded
        phrases like "not my lane", "not my core focus", "right up my alley",
        and "you might want to check out creators" are explicitly forbidden.
        """
        focus_text = (creator_focus or "the stuff you actually care about").strip()

        # Pull persona-specific signals so the decline sounds like THIS creator.
        style_fp = (creator_profile or {}).get("style_fingerprint") or {}
        if isinstance(style_fp, str):
            try:
                style_fp = json.loads(style_fp)
            except Exception:
                style_fp = {}
        lexical = style_fp.get("lexical_rules") or {}
        anti = style_fp.get("anti_persona") or {}
        dna = style_fp.get("linguistic_dna") or {}
        signature_phrases = list(lexical.get("signature_phrases") or style_fp.get("signature_phrases") or [])[:6]
        high_signal_words = list(lexical.get("high_signal_words") or style_fp.get("lexicon") or [])[:8]
        forbidden_lines = list(anti.get("forbidden_generic_coach_lines") or [])[:6]
        swearing = (dna.get("swearing") or "").strip().lower()
        energy = (dna.get("energy") or "").strip()

        signature_hint = (
            f"Signature phrases you might draw on if it fits: {', '.join(signature_phrases)}."
            if signature_phrases else ""
        )
        vocab_hint = (
            f"Prefer your real vocabulary: {', '.join(high_signal_words)}."
            if high_signal_words else ""
        )
        forbidden_hint = (
            "PHRASES YOU NEVER USE: " + "; ".join(forbidden_lines)
            if forbidden_lines else ""
        )
        swearing_hint = (
            "Swearing/edge: you may use it the way you naturally do in your content."
            if swearing in {"frequent", "often", "yes", "strong", "heavy"} else (
                "Swearing/edge: light, only if it lands naturally."
                if swearing in {"occasional", "some", "mild"} else ""
            )
        )
        energy_hint = f"Energy level: {energy}." if energy else ""

        if recent_topic:
            pivot_instruction = (
                f"Steer back to what they were actually working on: {recent_topic!r}. "
                "One bridging beat, then a sharp follow up question about that."
            )
        elif allow_handoff:
            pivot_instruction = (
                "Invite them back to what you actually obsess over with one specific question."
            )
        else:
            pivot_instruction = (
                "Do not point them to another creator, app, or search. Pull the conversation "
                "back to what you actually care about with one specific question."
            )

        prompt = f"""
You are {creator_name}. Reply in your real voice. Do not narrate that you are an AI.
User said: {user_message!r}

This is not really what you talk about. Your world is: {focus_text}.

Write a short, natural reply (1-3 sentences) that:
- Sounds like YOU on a podcast or in a DM, not like a customer support agent.
- Gives your honest, in-character reaction in one beat (a take, a one-liner, a quick reframe). It is fine to lightly engage with the topic, but do not give a tutorial, steps, recipe, formula, code, or detailed how-to.
- Then pivots. {pivot_instruction}

ABSOLUTELY FORBIDDEN PHRASING (do not output, paraphrase, or imply any of these):
- "not my lane" / "not really my lane" / "out of my lane"
- "not my core focus" / "not really my main focus" / "not my main focus"
- "right up my alley" / "that is right up my alley"
- "happy to chat" / "feel free to ask" / "let me know if"
- "you might want to check out" + any creator name
- "as an AI" / "language model" / "I'm here to help"
- "What sparked your interest in"

{forbidden_hint}
{signature_hint}
{vocab_hint}
{swearing_hint}
{energy_hint}

Do not say you lack access, cannot browse, or cannot provide live information. Just answer in character.

Creator Persona:
{persona}
"""

        try:
            resp = rag.generate_chat_completion(
                messages=[{"role": "system", "content": prompt}],
                model=settings.MODEL_MAIN_REPLY,
                temperature=0.85,
            )
            return resp.strip()
        except Exception as e:
            logger.error(f"Failed to generate boundary message: {e}")
            # Last-resort fallbacks. These purposely avoid the banned cliches.
            if recent_topic:
                return (
                    f"Honestly, that is not what I spend my time on. "
                    f"Let's go back to {recent_topic} — what is the actual move you are stuck on?"
                )
            generic_pivots = [
                "Honestly, that is not what I spend my time on. What are you actually trying to build right now?",
                "Not really the stuff I dig into. What is the real problem you are working through?",
                "That is outside what I obsess over. What were you actually trying to get to?",
            ]
            return random.choice(generic_pivots)


stronghold_guard = StrongholdGuardService()
