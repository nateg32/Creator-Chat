
import re
import random
import logging
from typing import List, Dict, Any, Optional

from backend.services.formatting import clean_response

logger = logging.getLogger(__name__)

class RhythmShaper:
    """
    Production-grade rhythm and tone shaper.
    Enforces DM chunking, anti-repetition, and persona boundaries.
    """

    def apply_rhythm(
        self, 
        text: str, 
        profile: Optional[Dict[str, Any]] = None, 
        state: Optional[Dict[str, Any]] = None
    ) -> str:
        if not text:
            return text
        
        profile = profile or {}
        
        # 1. Bot Phrase Removal (Anti-AI slop)
        text = self._remove_bot_phrases(text)
        text = clean_response(text)
        
        # 2. Skill-based Sentence Shortening
        user_state = (state or {}).get("last_router_meta", {}).get("user_state", {})
        if user_state.get("skill_level") == "beginner" or user_state.get("confusion_level") == "high":
            text = self._shorten_sentences(text)

        # 3. Connector Cleanup
        text = self._cleanup_connectors(text, profile.get("connector_avoidance", []))
        
        # 4. Sentence Splitting
        sentences = self._split_into_sentences(text)
        
        # 5. Punctuation & Constraint Enforcement
        final_sentences = []
        dash_used = False
        ellipsis_used = False
        
        for s in sentences:
            s = s.strip()
            # Strict Rule: No dashes in final output
            if "—" in s:
                s = s.replace("—", ",") 
            if "--" in s:
                s = s.replace("--", ",")
            if " - " in s:
                s = s.replace(" - ", ", ")
                
            if not ellipsis_used and "..." in s:
                ellipsis_used = True
            elif "..." in s:
                s = s.replace("...", ".") # Downgrade to period

            final_sentences.append(s)

        # 6. DM Chunking (Max Paragraphs)
        # Production Rule: Never more than 3 paragraphs for DMs
        return clean_response(self._apply_dm_chunking(final_sentences, max_paragraphs=3))

    def _remove_bot_phrases(self, text: str) -> str:
        forbidden = [
            "as an AI", "based on the information", "according to the database",
            "I'm sorry, but I cannot", "I don't have personal opinions",
            "I hope this helps", "let me know if you have more questions"
        ]
        for phrase in forbidden:
            text = re.sub(phrase, "", text, flags=re.IGNORECASE)
        # Cleanup double spaces/newlines
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _shorten_sentences(self, text: str) -> str:
        """Splits long sentences into punchy ones for beginners."""
        sentences = self._split_into_sentences(text)
        shorter = []
        for s in sentences:
            words = s.split()
            if len(words) > 15:
                mid = len(words) // 2
                shorter.append(" ".join(words[:mid]) + ".")
                shorter.append(" ".join(words[mid:]))
            else:
                shorter.append(s)
        return " ".join(shorter)

    def _split_into_sentences(self, text: str) -> List[str]:
        return re.findall(r'[^.!?]+[.!?]?', text)

    def _cleanup_connectors(self, text: str, avoidance: List[str]) -> str:
        replacements = {
            "therefore": "so", "moreover": "also", "furthermore": "plus",
            "consequently": "so", "however": "but", "additionally": "also"
        }
        for word in avoidance + list(replacements.keys()):
            pattern = re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)
            rep = replacements.get(word.lower(), "and")
            text = pattern.sub(rep, text)
        return text

    def _apply_dm_chunking(self, sentences: List[str], max_paragraphs: int = 3) -> str:
        if not sentences: return ""
        
        # Group sentences into paragraphs (roughly 2 per para)
        paragraphs = []
        for i in range(0, len(sentences), 2):
            paragraphs.append(" ".join(sentences[i:i+2]))
            
        # Enforce max paragraph limit
        if len(paragraphs) > max_paragraphs:
            paragraphs = paragraphs[:max_paragraphs]
            
        return "\n\n".join(paragraphs)

rhythm_shaper = RhythmShaper()
