
import re
from typing import List, Dict, Any
import statistics

class RhythmExtractor:
    """
    Extracts speech rhythm patterns from creator text samples.
    Used during ingestion/onboarding.
    """
    
    def extract_from_text(self, text: str) -> Dict[str, Any]:
        if not text or len(text) < 100:
            return self._default_profile()

        # Split into sentences (basic)
        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if len(s.strip().split()) > 1]
        
        if not sentences:
            return self._default_profile()

        # A. Sentence Length & Variance
        word_counts = [len(s.split()) for s in sentences]
        avg_len = sum(word_counts) / len(word_counts)
        variance = statistics.stdev(word_counts) if len(word_counts) > 1 else 0

        # B. Punctuation Rates (per 1000 chars)
        char_count = len(text)
        dashes = len(re.findall(r'[—\-]', text))
        ellipses = len(re.findall(r'\.\.\.', text))
        exclamations = text.count('!')
        questions = text.count('?')

        norm_factor = 1000 / max(1, char_count)
        
        # C. Opener Fillers
        openers = {}
        for s in sentences:
            words = s.split()
            if words:
                first_word = words[0].lower().strip(',.')
                if len(first_word) > 2: # Ignore things like "I", "A"
                    openers[first_word] = openers.get(first_word, 0) + 1
        
        # Sort and take top 10
        sorted_openers = sorted(openers.items(), key=lambda x: x[1], reverse=True)
        top_fillers = [f[0] for f in sorted_openers[:10] if f[1] > 1]

        # D. DM Chunking Style
        # Estimate based on newlines
        paragraphs = [p for p in text.split('\n') if p.strip()]
        avg_para_len = len(paragraphs) / max(1, len(text) / 500) # Simple heuristic
        
        chunk_style = "one_block"
        if avg_para_len > 2.5:
            chunk_style = "multi_block"
        elif avg_para_len > 1.2:
            chunk_style = "two_block"

        return {
            "avg_sentence_words": round(avg_len, 2),
            "sentence_variance": round(variance, 2),
            "one_liner_rate": 0.1, # Default placeholder
            "dash_rate": round(dashes * norm_factor, 3),
            "ellipsis_rate": round(ellipses * norm_factor, 3),
            "question_rate": round(questions * norm_factor, 3),
            "exclamation_rate": round(exclamations * norm_factor, 3),
            "opener_fillers": top_fillers,
            "connector_avoidance": ["therefore", "moreover", "consequently", "furthermore", "in conclusion"],
            "dm_chunk_style": chunk_style
        }

    def _default_profile(self) -> Dict[str, Any]:
        return {
            "avg_sentence_words": 15,
            "sentence_variance": 5,
            "one_liner_rate": 0.1,
            "dash_rate": 0.05,
            "ellipsis_rate": 0.05,
            "question_rate": 0.1,
            "exclamation_rate": 0.05,
            "opener_fillers": ["so", "look", "listen"],
            "connector_avoidance": ["therefore", "moreover"],
            "dm_chunk_style": "two_block"
        }

rhythm_extractor = RhythmExtractor()
