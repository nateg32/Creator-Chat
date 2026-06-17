
import logging
import json
import re
from typing import Dict, Any, List, Optional
import backend.rag as rag
from backend.settings import settings

logger = logging.getLogger(__name__)

class AssumptionBlocker:
    """
    Scans generated responses for unearned specifics (assumptions).
    If found and MVC is low, it replaces the assumption with a clarifying question.
    """

    def block_assumptions(
        self, 
        text: str, 
        mvc_score: int, 
        creator_name: str,
        memory: Dict[str, Any]
    ) -> str:
        if mvc_score >= 3: # If we know enough, assumptions are likely inferred correctly
            return text

        # 1. Detection of unearned specifics via LLM
        system_prompt = f"""
        You are an Assumption Blocker for {creator_name}.
        Review the generated message for any specifics that the user HAS NOT explicitly mentioned yet.
        
        KNOWN CONTEXT (MVC):
        {json.dumps(memory, indent=2)}
        
        UNEARNED SPECIFICS TO BLOCK:
        - Guessing business type (e.g. SaaS, Agency, Affiliate) if user just said "business".
        - Guessing industry or goal (e.g. fitness, trading, investing) if user just said "hello" or "how do I start".
        - Guessing market (e.g. Real Estate, Crypto) if user just said "investing" or "trading".
        - Guessing fitness strategy (e.g. bulk, cut) if user just said "fitness".
        - Any roadmap details that assume one specific path.

        IF ASSUMPTIONS FOUND:
        - Rewrite the sentence to remove the specificity.
        - Replace it with a clarifying question instead.
        - Keep the tone of {creator_name} (e.g. Hormozi).
        - IMPORTANT: NEVER mention being an assistant, an AI, or a bot. Speak as if you are the creator yourself.

        OUTPUT: Return only the final safe text.
        """

        try:
            cleaned = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                model=settings.MODEL_SYNTHESIS, # Use synthesis for this check
                temperature=0.0
            )
            return cleaned.strip()
        except Exception as e:
            logger.error(f"Assumption blocking failed: {e}")
            return text

assumption_blocker = AssumptionBlocker()
