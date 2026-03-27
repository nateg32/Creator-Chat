import hashlib
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

COLLECTIVE_GREETING_RE = re.compile(r"\b(everyone|everybody|guys|team|friends|fam|family|folks|yall|y'all|chat)\b", re.IGNORECASE)
DIRECT_DM_OPENERS = ["Hi", "Hey", "Hello", "What's up"]
GENERIC_OPENERS = {
    "hi",
    "hey",
    "hello",
    "hey there",
    "hello there",
    "what's up",
    "whats up",
    "let's go",
    "lets go",
}
GENERIC_QUESTIONS = {
    "what's the goal",
    "whats the goal",
    "what are you trying to figure out",
    "what's the biggest challenge today",
    "whats the biggest challenge today",
    "how can i help",
    "what is the goal",
}
NAME_QUESTIONS = ["What's your name?", "What should I call you?", "Who am I talking to?"]
CATEGORY_QUESTIONS = {
    "business": [
        "What are you building right now?",
        "Where are you stuck in the business?",
        "What's the bottleneck right now?",
    ],
    "ecommerce": [
        "What are you selling right now?",
        "Where is the store getting stuck?",
        "What part of the offer needs work?",
    ],
    "fitness": [
        "What are you training for right now?",
        "What part of your fitness feels off?",
        "What's the goal with your body right now?",
    ],
    "trading": [
        "What are you trying to sharpen in your trading?",
        "Where are you getting chopped up right now?",
        "What part of your process feels shaky?",
    ],
    "general": [
        "What's on your mind right now?",
        "What are you trying to sort out?",
        "What are you working through today?",
    ],
}


class GreetingService:
    """
    Handles conversational greeting logic.
    Ensures creator specific voice, avoids repetition, and applies constraints.
    """

    def _clean_options(self, values: Iterable[Any]) -> List[str]:
        cleaned: List[str] = []
        seen = set()
        for value in values or []:
            text = str(value or "").strip()
            if not text:
                continue
            text = COLLECTIVE_GREETING_RE.sub("", text)
            text = re.sub(r"\s+", " ", text).strip(" ,.!?")
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        return cleaned

    def _pick(self, options: List[str], seed_key: str) -> str:
        if not options:
            return ""
        digest = hashlib.sha256(seed_key.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % len(options)
        return options[index]

    def _creator_seed(self, creator_name: Optional[str], creator_category: Optional[str], voice_profile: Dict[str, Any]) -> str:
        signature_phrases = self._clean_options((voice_profile.get("signature_phrases") or [])[:4])
        common_words = self._clean_options((voice_profile.get("common_words") or [])[:4])
        energy_bucket = (voice_profile.get("energy") or {}).get("bucket", "MID")
        parts = [
            str(creator_name or "").strip().lower(),
            str(creator_category or "").strip().lower(),
            str(energy_bucket or "MID").strip().upper(),
            *[item.lower() for item in signature_phrases],
            *[item.lower() for item in common_words],
        ]
        return "|".join(part for part in parts if part)

    def _looks_like_opening_hook(self, text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        if len(normalized.split()) > 4:
            return False
        if re.search(r"\b(hi|hey|yo|hello|listen|look|alright|all right|what's|whats|let's|lets|good)\b", normalized):
            return True
        return len(normalized.split()) <= 2

    def _fallback_openers(self, energy_bucket: str, tone_traits: Dict[str, Any]) -> List[str]:
        blunt = float(tone_traits.get("blunt", 0.0) or 0.0)
        supportive = float(tone_traits.get("supportive", 0.0) or 0.0)
        hype = float(tone_traits.get("hype", 0.0) or 0.0)

        if hype >= 0.65 or energy_bucket == "HIGH":
            return ["Let's get after it", "Alright, let's work", "Good, you're here"]
        if supportive >= 0.7:
            return ["Hey, glad you're here", "Hey, good to hear from you", "I'm here"]
        if blunt >= 0.65:
            return ["Alright", "Let's get to it", "Good, let's talk"]
        if energy_bucket == "LOW":
            return ["Hey", "Good to hear from you", "Hey, glad you reached out"]
        return ["Hey", "Alright", "Good to hear from you"]

    def _question_tone_bank(self, tone_traits: Dict[str, Any], energy_bucket: str) -> List[str]:
        blunt = float(tone_traits.get("blunt", 0.0) or 0.0)
        supportive = float(tone_traits.get("supportive", 0.0) or 0.0)
        hype = float(tone_traits.get("hype", 0.0) or 0.0)

        questions: List[str] = []
        if blunt >= 0.65:
            questions.extend([
                "What's the real problem?",
                "What are we solving?",
                "Where are you actually stuck?",
            ])
        if supportive >= 0.7:
            questions.extend([
                "What's been weighing on you lately?",
                "What do you need help with today?",
                "What's been hard lately?",
            ])
        if hype >= 0.65 or energy_bucket == "HIGH":
            questions.extend([
                "What are you going after right now?",
                "What are we building?",
                "What's the move right now?",
            ])
        return questions

    def _build_openers(self, voice_profile: Dict[str, Any], energy_bucket: str, tone_traits: Dict[str, Any]) -> List[str]:
        if energy_bucket == "HIGH":
            base_openers = list(voice_profile.get("greeting_high_energy", []) or [])
        elif energy_bucket == "LOW":
            base_openers = list(voice_profile.get("greeting_short", []) or [])
        else:
            base_openers = list(voice_profile.get("greeting_neutral", []) or [])

        base_openers.extend(voice_profile.get("greetings", []) or [])

        signature_openers = [
            phrase
            for phrase in (voice_profile.get("signature_phrases", []) or [])
            if self._looks_like_opening_hook(str(phrase or ""))
        ]
        filler_openers = [
            filler
            for filler in ((voice_profile.get("speech_rhythm") or {}).get("fillers", []) or [])
            if self._looks_like_opening_hook(str(filler or ""))
        ]

        openers = self._clean_options([*signature_openers, *base_openers, *filler_openers])
        if not openers:
            openers = self._clean_options(self._fallback_openers(energy_bucket, tone_traits))
        return openers

    def _build_known_name_questions(
        self,
        voice_profile: Dict[str, Any],
        creator_category: Optional[str],
        tone_traits: Dict[str, Any],
        energy_bucket: str,
    ) -> List[str]:
        category_key = str(creator_category or "general").strip().lower()
        questions = self._clean_options(voice_profile.get("greeting_questions", []) or [])
        questions.extend(self._question_tone_bank(tone_traits, energy_bucket))
        questions.extend(CATEGORY_QUESTIONS.get(category_key, CATEGORY_QUESTIONS["general"]))
        unique_questions = self._clean_options(questions)
        if not unique_questions:
            return CATEGORY_QUESTIONS["general"]
        return unique_questions

    def _build_unknown_name_questions(self, tone_traits: Dict[str, Any], energy_bucket: str) -> List[str]:
        questions = list(NAME_QUESTIONS)
        if float(tone_traits.get("blunt", 0.0) or 0.0) >= 0.65:
            questions.insert(0, "What's your name?")
        elif float(tone_traits.get("supportive", 0.0) or 0.0) >= 0.7 or energy_bucket == "LOW":
            questions.insert(0, "What should I call you?")
        elif float(tone_traits.get("hype", 0.0) or 0.0) >= 0.65 or energy_bucket == "HIGH":
            questions.insert(0, "Who am I talking to?")
        return self._clean_options(questions) or NAME_QUESTIONS

    def generate_greeting(
        self,
        user_name: Optional[str],
        voice_profile: Dict[str, Any],
        include_question: bool = True,
        creator_name: Optional[str] = None,
        creator_category: Optional[str] = None,
    ) -> str:
        """
        Generate a deterministic but varied greeting based on creator profile.
        Format: [Opener] [Optional Name]. [Optional Question]
        """
        voice_profile = voice_profile or {}
        energy_bucket = (voice_profile.get("energy") or {}).get("bucket", "MID")
        tone_traits = voice_profile.get("tone_traits") or {}
        creator_seed = self._creator_seed(creator_name, creator_category, voice_profile) or "default"

        openers = self._build_openers(voice_profile, energy_bucket, tone_traits)
        non_generic_openers = [item for item in openers if item.strip().lower() not in GENERIC_OPENERS]
        opener = self._pick(non_generic_openers or openers, f"{creator_seed}|opener").strip()
        opener = COLLECTIVE_GREETING_RE.sub("", opener)
        opener = re.sub(r"\s+", " ", opener).strip(" ,.!?")
        opener = opener or self._pick(DIRECT_DM_OPENERS, f"{creator_seed}|direct")

        if opener.endswith("?"):
            opener = opener[:-1]
        if not opener.endswith((".", "!")):
            opener += "!" if energy_bucket == "HIGH" else "."

        final_greeting = opener
        clean_name = (user_name or "").strip()
        if clean_name and len(opener.split()) < 4:
            base = opener[:-1]
            punct = opener[-1]
            final_greeting = f"{base} {clean_name}{punct}"

        if not include_question:
            return final_greeting

        if not clean_name:
            question_pool = self._build_unknown_name_questions(tone_traits, energy_bucket)
        else:
            question_pool = self._build_known_name_questions(voice_profile, creator_category, tone_traits, energy_bucket)
            non_generic_questions = []
            for item in question_pool:
                normalized = item.strip().lower().rstrip(" ?!.")
                if normalized not in GENERIC_QUESTIONS:
                    non_generic_questions.append(item)
            question_pool = non_generic_questions or question_pool

        question = self._pick(question_pool, f"{creator_seed}|question")
        question = str(question or "").strip(" .!?")
        if not question:
            question = "What's on your mind"
        question = f"{question}?"
        return f"{final_greeting} {question}"


greeting_service = GreetingService()
