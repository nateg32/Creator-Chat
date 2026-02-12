import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class StyleDistiller:
    """
    Manages Style DNA: Rhythm, Structure, Lexical, Attitude.
    In a full implementation, this would compute DNA from transcripts.
    Here, it serves the structured DNA to the Voice Renderer.
    """
    
    def __init__(self):
        # Default generic DNA to fall back on
        self.default_dna = {
            "rhythm": {
                "sentence_length_dist": "varied", 
                "paragraph_length_dist": "short_to_medium",
                "punctuation_style": "standard",
                "question_frequency": "moderate"
            },
            "structure": {
                "framework_usage": "high",
                "list_vs_story": "balanced",
                "opening_style": "direct_hook",
                "closing_style": "actionable_step",
                "cta_pattern": "soft_nudge"
            },
            "lexical": {
                "signature_phrases": [],
                "high_signal_vocab": [],
                "banned_words": ["delve", "tapestry", "plethora", "unlock", "ensure", "moreover"],
                "filler_banlist": ["kind of", "sort of", "basically", "essentially", "literally"]
            },
            "attitude": {
                "bluntness": "balanced",
                "humour": "occasional",
                "empathy": "high",
                "certainty": "high"
            }
        }

    def get_style_dna(self, creator_id: int, creator_profile: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Retrieve Style DNA for a creator. 
        Merges profile-specific overrides with the default structure.
        """
        # In a real system, this would load creator_style_dna.json from storage
        # For now, we construct it from the creator_profile metadata if available
        
        dna = self.default_dna.copy()
        
        if not creator_profile:
            return dna

        # Map flat profile fields to DNA structure if they exist
        # (This adapts the existing 'profile' dict to the new DNA structure)
        
        # Lexical updates
        if "signature_phrases" in creator_profile:
            dna["lexical"]["signature_phrases"] = creator_profile["signature_phrases"]
            
        if "tone_of_voice" in creator_profile:
            tone = creator_profile["tone_of_voice"].lower()
            if "blunt" in tone: dna["attitude"]["bluntness"] = "high"
            if "funny" in tone: dna["attitude"]["humour"] = "high"
            
        # Structure overrides
        if "frameworks" in creator_profile:
            dna["structure"]["framework_usage"] = "very_high"

        return dna

    def format_for_prompt(self, dna: Dict[str, Any]) -> str:
        """
        Format the Style DNA into a concise system prompt section.
        """
        return f"""
[STYLE DNA CONSTRAINTS]
RHYTHM: {json.dumps(dna['rhythm'])}
STRUCTURE: {json.dumps(dna['structure'])}
KEY VOCABULARY: {json.dumps(dna['lexical']['signature_phrases'])}
BANNED WORDS: {json.dumps(dna['lexical']['banned_words'] + dna['lexical']['filler_banlist'])}
ATTITUDE: {json.dumps(dna['attitude'])}
""".strip()
