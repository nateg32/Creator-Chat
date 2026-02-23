
import logging
import json
from typing import Dict, Any, List, Optional
import rag
from settings import settings

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

        # Normalize
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

        # Fallback if domain is unknown but not explicitly out of scope
        # Heuristic: if we have primary domains, anything NOT in them or secondary
        # might be a bridge or decline depending on focus score.
        focus_score = stronghold_config.get("focus_score", 0.8)
        if focus_score > 0.9:
            return "DECLINE_HANDOFF"
        
        return "BRIDGE"

    def generate_boundary_message(
        self, 
        creator_name: str, 
        persona: str, 
        stronghold_config: Dict[str, Any],
        user_message: str
    ) -> str:
        """
        Generates a short in-character boundary message when a request is out-of-scope.
        """
        style = stronghold_config.get("style_for_decline", "short")
        
        prompt = f"""
        You are {creator_name}. 
        User asked: "{user_message}"
        
        This topic is OUTSIDE your expertise/domain. 
        Your goal is to politely but firmly decline answering OR redirect them back to your main focus.
        
        CONSTRAINTS:
        - Style: {style}
        - Length: 1-3 sentences max.
        - Questions: 1 question max.
        - NO info dumping.
        - Suggest they switch to a different creator or ask something about your core domain.
        
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
            return f"I'm focused on other things right now. Let's stick to what I know best!"

stronghold_guard = StrongholdGuardService()
