
import json
import logging
from typing import Dict, Any, List, Optional
from backend.db import db
import backend.rag as rag
from backend.settings import settings

logger = logging.getLogger(__name__)

class CLFService:
    """
    Creator Linguistic Fingerprint (CLF) Service.
    Automatically extracts style markers from ingested content.
    """
    
    def __init__(self, creator_id: int):
        self.creator_id = creator_id
        self.creator_name = "the creator"
        self._load_creator_info()

    def _load_creator_info(self):
        row = db.execute_one("SELECT name FROM creators WHERE id = %s", (self.creator_id,))
        if row:
            self.creator_name = row["name"]

    def extract_and_save_profile(self):
        """Perform the full extraction pipeline and save to DB."""
        logger.info(f"Extracting voice profile for creator {self.creator_id}...")
        
        # 1. Pull representative sample of content
        content_sample = self._get_content_sample()
        if not content_sample:
            logger.warning(f"No content found for creator {self.creator_id}. Using default profile.")
            return self._save_default_profile()

        # 2. Use LLM to analyze style markers (qualitative)
        profile = self._analyze_style_with_llm(content_sample)
        
        # 3. Compute Energy Heuristics (quantitative)
        energy_stats = self._compute_energy_heuristics(content_sample)
        profile["energy"] = energy_stats
        
        # 4. Store in DB
        self._save_profile(profile)
        return profile

    def _compute_energy_heuristics(self, text: str) -> Dict[str, Any]:
        """
        Compute quantitative energy metrics from text.
        weights: 0.35*punct + 0.25*rhythm + 0.25*emphasis + 0.15*emoji
        """
        import re
        
        # 1. Punctuation Signal (Exclamations/Question marks/Dashes per 1000 chars)
        exclamations = text.count("!")
        questions = text.count("?")
        dashes = text.count("-") + text.count("—")
        total_chars = len(text) or 1
        punct_density = (exclamations * 2 + questions + dashes) / (total_chars / 1000)
        # Normalize: >15 per 1k = 1.0, <2 = 0.0
        punct_score = min(1.0, max(0.0, (punct_density - 2) / 13))

        # 2. Rhythm Signal (Avg sentence length)
        # Shorter sentences = higher energy.
        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 1]
        avg_words = 15
        if sentences:
            total_words = sum(len(s.split()) for s in sentences)
            avg_words = total_words / len(sentences)
        
        # Normalize: <8 words = 1.0 (high), >25 words = 0.0 (low)
        # Linear interpolation between 8 and 25
        rhythm_score = 1.0 - min(1.0, max(0.0, (avg_words - 8) / 17))
        
        # 3. Emphasis Signal (Caps + Intensifiers)
        words = text.split()
        total_words = len(words) or 1
        caps_count = sum(1 for w in words if w.isupper() and len(w) > 1 and w.isalpha())
        
        intensifiers = ["literally", "insane", "crazy", "bro", "look", "listen", "mate", "no cap", "actually", "huge", "massive", "deadass", "fr", "best", "worst", "stop", "start"]
        intensifier_count = sum(1 for w in words if w.lower() in intensifiers)
        
        # Normalize: >5% emphasis = 1.0
        emphasis_rate = (caps_count + intensifier_count) / total_words
        emphasis_score = min(1.0, emphasis_rate / 0.05)

        # 4. Emoji Signal
        # Simple regex for finding emoji range (rough approximation for common ranges)
        # Or look for non-ascii characters often used as emoji
        emoji_count = len(re.findall(r'[^\x00-\x7F]+', text)) 
        # Normalize: >3 per 1000 chars = 1.0
        emoji_density = emoji_count / (total_chars / 1000)
        emoji_score = min(1.0, emoji_density / 3.0)
        
        # Combined Score
        total_energy = (0.35 * punct_score) + (0.25 * rhythm_score) + (0.25 * emphasis_score) + (0.15 * emoji_score)
        
        bucket = "MID"
        if total_energy < 0.35: bucket = "LOW"
        elif total_energy > 0.70: bucket = "HIGH"
        
        logger.info(f"Computed Energy: {total_energy:.2f} ({bucket}). Signals: P={punct_score:.2f}, R={rhythm_score:.2f}, E={emphasis_score:.2f}, Em={emoji_score:.2f}")

        return {
            "default_score": round(total_energy, 2),
            "bucket": bucket,
            "signals": {
                "punctuation": round(punct_score, 2),
                "rhythm": round(rhythm_score, 2),
                "emphasis": round(emphasis_score, 2),
                "emoji": round(emoji_score, 2)
            }
        }

    def _get_content_sample(self, limit: int = 20) -> str:
        """Fetch the most relevant/recent chunks to analyze style."""
        # Get chunks owned by this creator
        rows = db.execute_query("""
            SELECT chunk_text as content 
            FROM chunks
            WHERE creator_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (self.creator_id, limit))
        
        if not rows:
            return ""
            
        return "\n\n".join([r["content"] for r in rows])

    def _analyze_style_with_llm(self, text_sample: str) -> Dict[str, Any]:
        """Analyze the text sample and return a structured voice profile."""
        system_prompt = f"""
You are a Linguistic Stylist. Your task is to extract the "Linguistic Fingerprint" for {self.creator_name} based on the provided text samples (transcripts/posts).

Output EXACTLY this JSON structure:
{{
  "greetings": ["list", "of", "top", "openers"],
  "greeting_short": ["1-word", "intro"],
  "greeting_neutral": ["standard", "intro"],
  "greeting_high_energy": ["hyped", "intro"],
  "greeting_questions": ["short", "catchy", "questions", "to", "open"],
  "signoffs": ["list", "of", "common", "closers"],
  "signature_phrases": ["unique", "multi-word", "catchphrases"],
  "common_words": ["distinctive", "unigrams"],
  "tone_traits": {{
    "blunt": 0.0-1.0,
    "humor": 0.0-1.0,
    "supportive": 0.0-1.0,
    "hype": 0.0-1.0
  }},
  "style_constraints": {{
    "avg_sentence_words": int,
    "emoji_rate": "none" | "low" | "medium" | "high",
    "caps_rate": "none" | "rare" | "occasional" | "frequent",
    "uses_dashes": boolean,
    "uses_ellipses": boolean
  }},
  "interaction_traits": {{
    "question_first_rate": 0.0-1.0,
    "action_step_rate": 0.0-1.0
  }},
  "energy": {{
    "default_score": 0.0-1.0,
    "bucket": "LOW" | "MID" | "HIGH",
    "signals": {{
      "punctuation": 0.0-1.0,
      "rhythm": 0.0-1.0,
      "emphasis": 0.0-1.0,
      "emoji": 0.0-1.0
    }}
  }},
  "speech_rhythm": {{
    "fillers": ["list", "of", "top", "5", "fillers"],
    "filler_rate": 0.0-0.5,
    "sentence_variation": "short_bursts" | "balanced" | "flowing",
    "pause_markers": ["—", "..."]
  }}
}}

RULES:
1. GREETINGS: Look for how they start videos or posts (e.g., "Yo", "What's up guys", "Listen").
   - greeting_short: 1 word only (e.g. "Yo", "Hey").
   - greeting_neutral: Standard (e.g. "What's going on", "Hi everyone").
   - greeting_high_energy: Hyped (e.g. "Let's Go!", "LISTEN UP").
   - greeting_questions: Identify 3-5 short questions they use to check in (e.g. "What's the goal?", "How's it going?", "You ready?").
2. SIGNATURE PHRASES: Identify repeated patterns of 2-5 words that feel unique to them.
3. TONE: Be objective. 1.0 is maximum intensity for that trait.
4. ENERGY:
   - Punctuation: High frequency of !, ? and dashes.
   - Rhythm: High score if sentences are short and punchy.
   - Emphasis: High score if they use CAPS or many intensifiers (insane, literally, listen).
   - Emoji: Regular usage increases score.
   - BUCKETING: <0.35 = LOW, 0.35-0.70 = MID, >0.70 = HIGH.
5. EMOJI: If the text has no emojis, rate "none".
6. CONSTRAINTS: Be precise about their punctuation habits (dashes, ellipses).
"""
        user_prompt = f"TEXT SAMPLES:\n{text_sample[:10000]}" # Limit to 10k chars for sanity

        try:
            resp = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model=settings.ROUTER_MODEL,
                temperature=0.0,
                json_mode=True
            )
            return json.loads(resp)
        except Exception as e:
            logger.error(f"CLF Analysis failed: {e}")
            return self._get_default_profile()

    def _get_default_profile(self) -> Dict[str, Any]:
        return {
            "greetings": ["Hey", "Hello"],
            "greeting_short": ["Hey"],
            "greeting_neutral": ["Hello there"],
            "greeting_high_energy": ["Let's Go!"],
            "greeting_questions": ["What is the goal?", "How can I help?"],
            "signoffs": ["Talk soon", "Best"],
            "signature_phrases": [],
            "common_words": [],
            "tone_traits": {"blunt": 0.5, "humor": 0.2, "supportive": 0.8, "hype": 0.5},
            "style_constraints": {
                "avg_sentence_words": 15,
                "emoji_rate": "low",
                "caps_rate": "rare",
                "uses_dashes": True,
                "uses_ellipses": False
            },
            "interaction_traits": {"question_first_rate": 0.3, "action_step_rate": 0.5},
            "energy": {
                "default_score": 0.5,
                "bucket": "MID",
                "signals": {"punctuation": 0.5, "rhythm": 0.5, "emphasis": 0.5, "emoji": 0.5}
            },
            "speech_rhythm": {
                "fillers": ["Look", "Alright", "So"],
                "filler_rate": 0.1,
                "sentence_variation": "balanced",
                "pause_markers": ["—"]
            }
        }

    def _save_profile(self, profile: Dict[str, Any]):
        db.execute_update(
            "UPDATE creators SET voice_profile = %s WHERE id = %s",
            (json.dumps(profile), self.creator_id)
        )

    def _save_default_profile(self):
        profile = self._get_default_profile()
        self._save_profile(profile)
        return profile
