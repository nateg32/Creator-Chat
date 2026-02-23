
import logging
import re
from typing import Dict, Any, List, Optional
import rag
from settings import settings

logger = logging.getLogger(__name__)

class GrammarNormalizer:
    """
    Grammar Normalization Layer.
    Ensures creator responses have correct grammar and are fragment-free
    while preserving the unique persona and tone.
    """

    def grammar_cleanup(self, text: str) -> str:
        if not text:
            return text
            
        # First, a quick regex pass for basic artifacts (Transcript Artifact Removal)
        text = self._basic_cleanup(text)
        
        system_prompt = """
        You are the Grammar Normalization Layer for a Creator Bot.
        Your goal is to fix grammar, punctuation, and structural issues while STRICTLY PRESERVING the creator's tone and wording.

        CLEANUP RULES:
        1. NO DASHES: All dashes ("—", "--", "-") must be removed. Replace with a comma or period depending on context. Never keep dash punctuation.
        2. FIX FRAGMENTS: Detect sentences that end abruptly or are incomplete thoughts (especially those with fewer than 4 meaningful words). Merge them with the next sentence or complete them using the context.
        3. TRANSCRIPT ARTIFACTS: Remove mid-sentence line breaks, duplicated punctuation (e.g., ",," or ".."), and stray filler words at the start of sentences (like "um," "so," "like") if they break flow.
        4. GRAMMAR: Ensure sentences start with capital letters and punctuation spacing is correct.
        
        STYLE PRESERVATION:
        - Do NOT change vocabulary or tone.
        - Do NOT add formal wording or make it sound robotic.
        - The creator must still sound like themselves.
        - Keep the overall length and structure similar.

        Output ONLY the normalized text.
        """
        
        try:
            # Use the Main Reply model for high-fidelity tone preservation
            normalized = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                model=settings.MODEL_MAIN_REPLY,
                temperature=0.0
            )
            return normalized.strip()
        except Exception as e:
            logger.error(f"Grammar normalization LLM call failed: {e}")
            # Fallback to a slightly better regex-only pass if LLM fails
            return self._final_fallback_normalization(text)

    def _basic_cleanup(self, text: str) -> str:
        """Removes obvious transcript artifacts before LLM pass."""
        # Remove mid-sentence line breaks
        text = re.sub(r'([a-z,])\n([a-z])', r'\1 \2', text)
        # Fix duplicated punctuation
        text = re.sub(r'[,\.]{2,}', lambda m: m.group(0)[0], text)
        # Ensure single space after punctuation
        text = re.sub(r'([\.!\?])([A-Za-z])', r'\1 \2', text)
        return text

    def _final_fallback_normalization(self, text: str) -> str:
        """A more aggressive regex pass to use if the LLM fails."""
        # Replace all types of dashes with commas/periods (simple heuristic)
        text = text.replace("—", ",").replace("--", ",").replace(" - ", ", ")
        # Basic capitalization
        sentences = re.split(r'([\.!\?]\s*)', text)
        for i in range(len(sentences)):
            if sentences[i] and sentences[i][0].isalpha():
                sentences[i] = sentences[i][0].upper() + sentences[i][1:]
        return "".join(sentences)

grammar_normalizer = GrammarNormalizer()
