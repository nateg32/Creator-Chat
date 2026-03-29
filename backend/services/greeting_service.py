import hashlib
import logging
import random
import re
from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional


logger = logging.getLogger(__name__)


def _normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


GREETING_TRIGGERS = {
    "hello",
    "hi",
    "hey",
    "hey there",
    "hi there",
    "hello there",
    "howdy",
    "whats up",
    "what s up",
    "sup",
    "yo",
    "hiya",
    "good morning",
    "good afternoon",
    "good evening",
    "morning",
    "afternoon",
    "evening",
    "greetings",
    "helo",
    "hii",
    "heyyy",
    "heyy",
}

GREETING_WORDS = {
    "hello",
    "hi",
    "hey",
    "howdy",
    "sup",
    "yo",
    "hiya",
    "morning",
    "afternoon",
    "evening",
    "greetings",
    "helo",
    "hii",
    "heyyy",
    "heyy",
}

COLLECTIVE_RE = re.compile(r"\b(everyone|everybody|guys|team|friends|fam|family|folks|chat|yall|y'all)\b", re.IGNORECASE)
QUESTION_RE = re.compile(r"\?$")
STYLE_DESCRIPTION_RE = re.compile(
    r"\b(direct and engaging|friendly and|warm and|clear and|honest and|my style is|i like to be|i tend to be|as someone who|in the spirit of)\b",
    re.IGNORECASE,
)
SPECIFIC_QUESTION_RE = re.compile(
    r"(what part of .+ needs|which area of .+ are you|where are you stuck with|what is your current .+ situation|how many .+ do you have|what stage is your .+)",
    re.IGNORECASE,
)
GENERIC_AI_RE = re.compile(
    r"\b(how can i assist you|how can i help you today|what can i do for you|i'm here to help|i'm here to assist|feel free to ask|don't hesitate to|certainly|absolutely|of course|great question)\b",
    re.IGNORECASE,
)

GREETING_STRATEGIES = [
    "warm_open",
    "acknowledgement_then_open",
    "direct_informal",
    "brief_anchor",
    "question_only",
    "returning_reacknowledge",
]


def is_greeting(message: str) -> bool:
    cleaned = _normalize_key(message)
    if not cleaned:
        return False
    if cleaned in GREETING_TRIGGERS:
        return True

    tokens = cleaned.split()
    if not tokens:
        return False
    if len(tokens) <= 3 and tokens[0] in GREETING_WORDS:
        return True
    return all(token in GREETING_WORDS for token in tokens if token)


class GreetingService:
    """
    Dedicated social-opening engine.
    Greetings stay out of retrieval and web search and instead compose a
    short, human opener using creator-specific energy, cadence, and phrasing.
    """

    def __init__(self) -> None:
        self._rng = random.SystemRandom()
        self._recent_greetings: Dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=16))
        self._recent_strategies: Dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=4))

    def _coerce_dict(self, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _clean_text(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = COLLECTIVE_RE.sub("", text)
        text = re.sub(r"\s+", " ", text).strip(" ,.!?:;")
        return text

    def _clean_options(self, values: Iterable[Any]) -> List[str]:
        cleaned: List[str] = []
        seen = set()
        for value in values or []:
            text = self._clean_text(value)
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned.append(text)
        return cleaned

    def _ordered(self, values: Iterable[str], seed_key: Optional[str] = None) -> List[str]:
        cleaned = self._clean_options(values)
        if not cleaned:
            return []
        if seed_key:
            return sorted(
                cleaned,
                key=lambda item: hashlib.sha256(f"{seed_key}|{item}".encode("utf-8")).hexdigest(),
            )
        ordered = list(cleaned)
        self._rng.shuffle(ordered)
        return ordered

    def _creator_key(
        self,
        creator_name: Optional[str],
        creator_category: Optional[str],
        user_name: Optional[str],
    ) -> str:
        return "|".join(
            part
            for part in [
                _normalize_key(creator_name),
                _normalize_key(creator_category),
                _normalize_key(user_name),
            ]
            if part
        ) or "default"

    def _looks_like_safe_opener(self, text: str) -> bool:
        normalized = self._clean_text(text)
        if not normalized:
            return False
        if QUESTION_RE.search(normalized):
            return False
        if STYLE_DESCRIPTION_RE.search(normalized):
            return False
        if SPECIFIC_QUESTION_RE.search(normalized):
            return False
        if GENERIC_AI_RE.search(normalized):
            return False
        if normalized.lower().startswith(("what", "where", "how", "which", "why", "when", "if", "because", "since", "unless")):
            return False
        if len(normalized.split()) > 7:
            return False
        return True

    def _extract_greeting_examples(self, style_fingerprint: Dict[str, Any]) -> List[str]:
        style_fingerprint = self._coerce_dict(style_fingerprint)
        golden_examples = self._coerce_dict(style_fingerprint.get("golden_examples"))
        examples = list(golden_examples.get("greeting") or [])
        phrases: List[str] = []
        for example in examples:
            text = str(example or "").strip()
            if not text:
                continue
            first_sentence = re.split(r"[.!?]+", text)[0].strip()
            if self._looks_like_safe_opener(first_sentence):
                phrases.append(first_sentence)
        return self._clean_options(phrases)

    def _extract_creator_signals(
        self,
        creator_profile: Optional[Dict[str, Any]],
        voice_profile: Dict[str, Any],
        style_fingerprint: Dict[str, Any],
    ) -> Dict[str, Any]:
        creator_profile = creator_profile or {}
        voice_patterns = self._coerce_dict(creator_profile.get("voice_patterns"))
        behavioral = self._coerce_dict(creator_profile.get("behavioral_fingerprint"))
        interaction = self._coerce_dict(voice_patterns.get("interaction_style"))
        rhythm = self._coerce_dict(voice_patterns.get("rhythm"))
        sentence_structure = self._coerce_dict(voice_patterns.get("sentence_structure"))
        speech_mechanics = self._coerce_dict(style_fingerprint.get("speech_mechanics"))
        creator_phrases = self._clean_options(
            list(voice_profile.get("signature_phrases") or [])
            + list(behavioral.get("catchphrases") or [])
        )

        openers = self._clean_options(
            list(voice_profile.get("greeting_high_energy") or [])
            + list(voice_profile.get("greeting_neutral") or [])
            + list(voice_profile.get("greeting_short") or [])
            + list(voice_profile.get("greetings") or [])
            + list(speech_mechanics.get("signature_openings") or [])
            + self._extract_greeting_examples(style_fingerprint)
        )
        openers = [item for item in openers if self._looks_like_safe_opener(item)]

        address = self._clean_text(
            interaction.get("how_they_address_audience")
            or interaction.get("audience_address")
        )
        if address and (len(address.split()) > 3 or address.lower() in {"team", "guys", "everyone", "folks", "friends"}):
            address = ""

        energy = (
            interaction.get("energy_level")
            or (voice_profile.get("energy") or {}).get("bucket")
            or "medium"
        )
        energy = str(energy or "medium").strip().lower()
        pacing = str(rhythm.get("pacing") or "").strip().lower()
        sentence_length = str(sentence_structure.get("avg_sentence_length") or "").strip().lower()
        tone_traits = self._coerce_dict(voice_profile.get("tone_traits"))

        return {
            "openers": openers,
            "creator_phrases": [item for item in creator_phrases if self._looks_like_safe_opener(item)],
            "address": address,
            "energy": energy,
            "pacing": pacing,
            "sentence_length": sentence_length,
            "tone_traits": tone_traits,
        }

    def _fallback_openers(self, signals: Dict[str, Any], returning: bool) -> List[str]:
        energy = signals.get("energy", "medium")
        tone = signals.get("tone_traits", {})
        blunt = float(tone.get("blunt", 0.0) or 0.0)
        supportive = float(tone.get("supportive", 0.0) or 0.0)
        hype = float(tone.get("hype", 0.0) or 0.0)

        if returning:
            if supportive >= 0.7:
                return ["Good to hear from you again", "Glad you're back", "Good to see you again"]
            if hype >= 0.65 or energy == "high":
                return ["Back at it", "Good, you're back", "Let's get back into it"]
            return ["Good to see you again", "Glad you're back", "Alright, good to have you back"]

        if hype >= 0.65 or energy == "high":
            return ["Let's go", "Alright", "Good, you're here"]
        if supportive >= 0.7 or energy == "calm" or energy == "low":
            return ["Hey", "Good to hear from you", "Glad you reached out"]
        if blunt >= 0.65:
            return ["Alright", "Good, let's talk", "Let's get into it"]
        return ["Hey", "Good to see you", "Alright"]

    def _broad_questions(
        self,
        creator_category: Optional[str],
        signals: Dict[str, Any],
        returning: bool,
        know_name: bool,
    ) -> List[str]:
        energy = signals.get("energy", "medium")
        tone = signals.get("tone_traits", {})
        blunt = float(tone.get("blunt", 0.0) or 0.0)
        supportive = float(tone.get("supportive", 0.0) or 0.0)
        hype = float(tone.get("hype", 0.0) or 0.0)

        if not know_name:
            return ["What should I call you?", "What's your name?", "Who am I talking to?"]

        if returning:
            if supportive >= 0.7:
                return ["What's been on your mind lately?", "What are you working through right now?", "Where do you want to pick this up?"]
            if hype >= 0.65 or energy == "high":
                return ["What's the move right now?", "What are you pushing on right now?", "What are you working on?"]
            if blunt >= 0.65:
                return ["What are we solving?", "Where do you want to start?", "What's the real thing on your plate?"]
            return ["What's moving right now?", "What are you working on?", "Where do you want to start?"]

        if supportive >= 0.7 or energy == "calm" or energy == "low":
            return ["What's on your mind?", "What are you working through?", "Where do you want to start?"]
        if hype >= 0.65 or energy == "high":
            return ["What are you working on?", "What's the move?", "What are you building?"]
        if blunt >= 0.65:
            return ["What are you working on?", "What's the real thing you want to solve?", "Where do you want to start?"]
        if str(creator_category or "").strip().lower() in {"business", "ecommerce", "creator", "marketing"}:
            return ["What are you working on?", "What are you building right now?", "Where do you want to start?"]
        return ["What's on your mind?", "What are you working on?", "Where do you want to start?"]

    def _anchor_lines(self, signals: Dict[str, Any], returning: bool) -> List[str]:
        energy = signals.get("energy", "medium")
        tone = signals.get("tone_traits", {})
        supportive = float(tone.get("supportive", 0.0) or 0.0)
        hype = float(tone.get("hype", 0.0) or 0.0)
        blunt = float(tone.get("blunt", 0.0) or 0.0)

        if returning:
            anchors = ["Good to have you back", "Good to pick this up again", "Glad we get another round at this"]
        elif supportive >= 0.7:
            anchors = ["Good to have you here", "Glad you stopped in", "Good to connect"]
        elif hype >= 0.65 or energy == "high":
            anchors = ["Let's make this useful", "Let's get into it", "Good, let's make this count"]
        elif blunt >= 0.65:
            anchors = ["Let's keep this simple", "Let's get straight to it", "Let's make this clear"]
        else:
            anchors = ["Good to have you here", "Let's get into it", "Glad you dropped in"]

        return self._clean_options(anchors)

    def _pick_strategy(self, key: str, variation_seed: Optional[str] = None) -> str:
        recent = self._recent_strategies[key]
        available = [item for item in GREETING_STRATEGIES if item != (recent[-1] if recent else None)]
        if variation_seed:
            digest = hashlib.sha256(f"{variation_seed}|{key}".encode("utf-8")).digest()
            return available[int.from_bytes(digest[:4], "big") % len(available)]
        choice = self._rng.choice(available)
        recent.append(choice)
        return choice

    def _compose_candidate(
        self,
        strategy: str,
        opener: str,
        question: str,
        user_name: str,
        address: str,
        returning: bool,
    ) -> str:
        name = user_name.strip()
        opener = self._clean_text(opener)
        question = self._clean_text(question).rstrip(" ?!.") + "?"

        if strategy == "question_only":
            if name:
                return f"{question[:-1]}, {name}?"
            return question

        if strategy == "returning_reacknowledge" and returning:
            prefix = opener or "Good to see you again"
            if name:
                return f"{prefix}, {name}. {question}"
            return f"{prefix}. {question}"

        if strategy == "direct_informal":
            prefix = opener or "Alright"
            if name:
                return f"{prefix}, {name}. {question}"
            return f"{prefix}. {question}"

        if strategy == "brief_anchor":
            prefix = opener or "Good to have you here"
            if name:
                return f"{prefix}, {name}. {question}"
            return f"{prefix}. {question}"

        if strategy == "acknowledgement_then_open":
            prefix = opener or ("Good to see you again" if returning else "Good to see you")
            if name:
                return f"{prefix}, {name}. {question}"
            return f"{prefix}. {question}"

        if strategy == "warm_open":
            prefix = opener or "Hey"
            if name:
                return f"{prefix} {name}. {question}"
            return f"{prefix}. {question}"

        prefix = opener or address or "Hey"
        if name:
            return f"{prefix} {name}. {question}"
        return f"{prefix}. {question}"

    def _looks_human(self, text: str) -> bool:
        lowered = text.lower()
        if not text or len(text.split()) < 3:
            return False
        if text.startswith("I "):
            return False
        if text.count("?") > 1:
            return False
        if ":" in text and re.search(r":\s*(\d+\.|-)", text):
            return False
        if STYLE_DESCRIPTION_RE.search(lowered):
            return False
        if SPECIFIC_QUESTION_RE.search(lowered):
            return False
        if GENERIC_AI_RE.search(lowered):
            return False
        return True

    def _safety_check(
        self,
        text: str,
        user_name: Optional[str],
        signals: Dict[str, Any],
        returning: bool,
    ) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        cleaned = cleaned.replace("..", ".").strip(" ")
        if self._looks_human(cleaned) and len(cleaned.split()) <= 60:
            if user_name and user_name.strip() and user_name.lower() not in cleaned.lower():
                fallback = self._safe_fallback(user_name, signals, returning)
                return fallback
            return cleaned
        return self._safe_fallback(user_name, signals, returning)

    def _safe_fallback(self, user_name: Optional[str], signals: Dict[str, Any], returning: bool) -> str:
        name = (user_name or "").strip()
        questions = self._broad_questions("general", signals, returning, bool(name))
        question = questions[0]
        tone = signals.get("tone_traits", {})
        supportive = float(tone.get("supportive", 0.0) or 0.0)
        hype = float(tone.get("hype", 0.0) or 0.0)
        if returning:
            opener = "Good to see you again"
        elif hype >= 0.65 or signals.get("energy") == "high":
            opener = "Hey"
        elif supportive >= 0.7:
            opener = "Good to see you"
        else:
            opener = "Hey"
        if name:
            return f"{opener} {name}. {question}"
        return f"{opener}. {question}"

    def generate_greeting(
        self,
        user_name: Optional[str],
        voice_profile: Dict[str, Any],
        include_question: bool = True,
        creator_name: Optional[str] = None,
        creator_category: Optional[str] = None,
        style_fingerprint: Optional[Dict[str, Any]] = None,
        variation_seed: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        creator_profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        voice_profile = voice_profile or {}
        style_fingerprint = style_fingerprint or {}
        creator_profile = creator_profile or {}
        key = self._creator_key(creator_name, creator_category, user_name)
        returning = bool(conversation_history and len(conversation_history) > 2)
        signals = self._extract_creator_signals(creator_profile, voice_profile, style_fingerprint)

        openers = self._clean_options(signals.get("openers") or self._fallback_openers(signals, returning))
        if not openers:
            openers = self._fallback_openers(signals, returning)
        questions = self._broad_questions(creator_category, signals, returning, bool((user_name or "").strip()))
        anchors = self._anchor_lines(signals, returning)

        strategy = self._pick_strategy(key, variation_seed=variation_seed)
        if not include_question:
            opener = self._ordered(openers + anchors, variation_seed)[0]
            return self._safety_check(f"{opener}.", user_name, signals, returning)

        question_seed = f"{variation_seed}|question" if variation_seed else None
        opener_seed = f"{variation_seed}|opener" if variation_seed else None
        question = self._ordered(questions, question_seed)[0]

        strategy_openers = {
            "warm_open": openers + anchors,
            "acknowledgement_then_open": anchors + openers,
            "direct_informal": [item for item in openers if len(item.split()) <= 4] + self._fallback_openers(signals, returning),
            "brief_anchor": anchors + openers,
            "question_only": [""],
            "returning_reacknowledge": anchors + self._fallback_openers(signals, returning),
        }
        opener_pool = strategy_openers.get(strategy, openers) or openers
        if strategy == "question_only":
            opener = ""
        else:
            ordered_openers = self._ordered(opener_pool, opener_seed)
            opener = ordered_openers[0] if ordered_openers else "Hey"
        address = signals.get("address") or ""
        candidate = self._compose_candidate(
            strategy,
            opener,
            question,
            user_name or "",
            address,
            returning,
        )

        final = self._safety_check(candidate, user_name, signals, returning)
        if not variation_seed:
            recent = self._recent_greetings[key]
            if final in recent:
                alternate_questions = self._ordered(questions[1:] or questions, f"{key}|alt")
                if alternate_questions:
                    candidate = self._compose_candidate(
                        strategy,
                        opener,
                        alternate_questions[0],
                        user_name or "",
                        address,
                        returning,
                    )
                    final = self._safety_check(candidate, user_name, signals, returning)
            recent.append(final)
        return final


greeting_service = GreetingService()
