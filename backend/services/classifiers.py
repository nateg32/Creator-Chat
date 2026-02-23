
import logging
import json
from typing import Dict, Any, List, Optional
import rag
from settings import settings

logger = logging.getLogger(__name__)

class ClassifiersService:
    """
    High-speed classification for intent, domain, emotion, etc.
    Model: GPT-4.1
    """

    def classify_all(
        self, 
        message: str, 
        history: List[Dict[str, str]], 
        creator_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Runs comprehensive classification on the user message.
        """
        history_str = "\n".join([f"{m['role']}: {m['content']}" for m in history[-5:]])
        
        system_prompt = f"""
        You are a Conversation Router. 
        Analyze the user's latest message in context of the history.
        
        CREATOR CONTEXT:
        Name: {creator_profile.get('name', 'The Creator')}
        Handle: {creator_profile.get('handle', '')}
        
        OUTPUT FORMAT: Strict JSON only.
        
        FIELDS:
        1. intent: greeting|question|request|followup|unknown
           (Rule: If message is just "hello", "hey", "hi", "yo", or similar, intent MUST be "greeting")
        2. goal_guess: A 1-sentence summary of what the user is trying to achieve.
        3. user_stage: exploring|deciding|executing|stuck|unknown (exploring=curiosity, deciding=choosing, executing=doing, stuck=blocked)
        4. missing_info: List of max 2 items needed to provide a high-quality answer. Empty if no info is missing.
        5. skill_level: beginner|intermediate|advanced|unknown
        6. clarity_level: clear|somewhat_clear|unclear
        7. confusion_level: low|medium|high
        8. primary_domain: e.g. fitness, trading, business, etc.
        9. request_type: pathway|explanation|recommendation|discussion|casual|other
        10. emotion: {{"primary": "neutral|curious|confused|frustrated|overwhelmed|excited|discouraged", "intensity": "low|medium|high"}}
        11. flags:
           - personal_question_flag: bool (asking about creator's personal life: wife, kids, age, net worth)
           - safety_highstakes_flag: bool (medical, legal, or extreme financial advice)
           - greeting_only_flag: bool (True ONLY if the message is JUST a greeting/small talk with NO request)
           - needs_clarification_flag: bool
        12. suggested_mode: GREETING|DISCOVERY|ONRAMP|EXPLAIN|RECOMMEND|COACH|DEEP
        
        STRICT RULE: If the user says "hello" or equivalent, do NOT look for hidden meaning. The intent is "greeting" and request_type is "casual".
        """

        user_prompt = f"History:\n{history_str}\n\nLast Message: {message}"

        try:
            resp = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model=settings.MODEL_CLASSIFICATION,
                temperature=0.0,
                json_mode=True
            )
            return json.loads(resp)
        except Exception as e:
            logger.error(f"Classification failed: {e}")
            # Fallback
            return {
                "intent": "unknown",
                "skill_level": "unknown",
                "clarity_level": "clear",
                "confusion_level": "low",
                "primary_domain": "general",
                "request_type": "discussion",
                "emotion": {"primary": "neutral", "intensity": "low"},
                "flags": {
                    "personal_question_flag": False,
                    "safety_highstakes_flag": False,
                    "greeting_only_flag": False,
                    "needs_clarification_flag": False
                },
                "suggested_mode": "EXPLAIN"
            }

classifiers = ClassifiersService()
