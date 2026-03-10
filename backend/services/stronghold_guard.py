
import logging
import json
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

        focus_score = stronghold_config.get("focus_score", 0.8)
        if focus_score > 0.9:
            return "DECLINE_HANDOFF"

        return "BRIDGE"

    def generate_boundary_message(
        self,
        creator_name: str,
        persona: str,
        stronghold_config: Dict[str, Any],
        user_message: str,
        recent_topic: Optional[str] = None,
        creator_focus: Optional[str] = None,
        allow_handoff: bool = True,
    ) -> str:
        """
        Generates a short in character boundary or bridge message when a request is out of scope.
        """
        style = stronghold_config.get("style_for_decline", "short")
        focus_text = (creator_focus or "their core lane").strip()
        pivot_instruction = ""
        if recent_topic:
            pivot_instruction = (
                f"Pivot naturally back to this recent topic from the conversation: {recent_topic!r}. "
                "Use one short bridging line, then one grounded follow up question tied to that topic."
            )
        elif allow_handoff:
            pivot_instruction = "If helpful, invite the user to ask about your core lane or switch to a better fit creator."
        else:
            pivot_instruction = "Do not suggest apps, exchanges, search tips, or another creator. End by steering back to your own lane with one natural question."

        handoff_instruction = "You may suggest a better fit creator if that feels natural." if allow_handoff else "Do not suggest a different creator."

        prompt = f"""
You are {creator_name}.
User asked: {user_message!r}

This topic is outside your real lane. Your focus is: {focus_text}.
Your goal is to respond in character, briefly, and naturally.

Constraints:
- Style: {style}
- Length: 2 to 4 sentences.
- Sound like a real person, not a search tool or support agent.
- Acknowledge that this is not your lane without sounding robotic.
- {handoff_instruction}
- {pivot_instruction}
- Do not say you lack access, cannot browse, or cannot provide live information.
- Do not dump facts about the out of scope topic.

Creator Persona:
{persona}
"""

        try:
            resp = rag.generate_chat_completion(
                messages=[{"role": "system", "content": prompt}],
                model=settings.MODEL_MAIN_REPLY,
                temperature=0.7
            )
            return resp.strip()
        except Exception as e:
            logger.error(f"Failed to generate boundary message: {e}")
            if recent_topic:
                return f"That is not really my lane. Let's come back to {recent_topic}. What are you actually trying to figure out there?"
            return "That is not really my lane. Ask me something closer to what I actually talk about."


stronghold_guard = StrongholdGuardService()
