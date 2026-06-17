import hashlib
import logging
import random
import re
from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional

from backend.services.style_signal_sanitizer import (
    clean_style_phrase_list,
    looks_like_raw_content_hook,
    sanitize_style_fingerprint_for_runtime,
    sanitize_voice_profile_for_runtime,
)


logger = logging.getLogger(__name__)


def _normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _is_greeting_token(token: str) -> bool:
    token = re.sub(r"[^a-z0-9]+", "", str(token or "").lower())
    if not token:
        return False
    if token in GREETING_WORDS:
        return True
    return bool(
        re.fullmatch(
            r"(?:"
            r"h+i+|"
            r"he+y+|"
            r"hello+|"
            r"y+o+|"
            r"sup+|"
            r"wass+up+|"
            r"wsg+"
            r")",
            token,
        )
    )


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
    "yoo",
    "yooo",
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

SOCIAL_FILLER_WORDS = {
    "bro",
    "bruh",
    "bruv",
    "broski",
    "broskie",
    "man",
    "mate",
    "ma",
    "dude",
    "g",
    "myg",
    "fam",
    "chief",
    "homie",
}

SOCIAL_TASK_HINT_WORDS = {
    "advice",
    "analyse",
    "analyze",
    "ask",
    "build",
    "business",
    "calorie",
    "chart",
    "coach",
    "compare",
    "cut",
    "diet",
    "does",
    "explain",
    "fix",
    "gym",
    "help",
    "how",
    "image",
    "improve",
    "link",
    "plan",
    "post",
    "recommend",
    "reccomend",
    "reel",
    "review",
    "send",
    "show",
    "source",
    "start",
    "strategy",
    "teach",
    "tell",
    "trade",
    "trading",
    "video",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "workout",
}

DIRECT_ADDRESS_BLOCKLIST = SOCIAL_TASK_HINT_WORDS | {
    "about",
    "again",
    "all",
    "am",
    "are",
    "been",
    "can",
    "could",
    "did",
    "do",
    "for",
    "from",
    "get",
    "go",
    "going",
    "good",
    "got",
    "have",
    "i",
    "im",
    "in",
    "is",
    "it",
    "like",
    "me",
    "my",
    "need",
    "should",
    "that",
    "the",
    "there",
    "this",
    "to",
    "u",
    "up",
    "with",
    "you",
    "your",
}

CHECKIN_PHRASE_RE = re.compile(
    r"\b("
    r"how\s+(?:are|r)\s+(?:you|u)|"
    r"how\s+(?:you|u|ya)\s+(?:going|goin|doing)|"
    r"how(?:'s|s| is)?\s+(?:life|things|your\s+day)|"
    r"(?:what\s+)?(?:have\s+)?(?:you|u|ya)\s+been\s+up\s*to|"
    r"(?:what\s+)?(?:are\s+)?(?:you|u|ya)\s+up\s+to|"
    r"hbu|wyd"
    r")\b",
    re.IGNORECASE,
)


def _squash_repeated_letters(token: str) -> str:
    return re.sub(r"(.)\1{2,}", r"\1\1", str(token or "").lower())


def _is_social_filler_token(token: str) -> bool:
    cleaned = re.sub(r"[^a-z0-9]+", "", _squash_repeated_letters(token))
    if not cleaned:
        return False
    if cleaned in SOCIAL_FILLER_WORDS:
        return True
    # Covers casual vocatives such as "broskki" without enumerating every spelling.
    if cleaned.startswith(("bro", "bruv", "bruh")) and 3 <= len(cleaned) <= 10:
        return True
    if cleaned.startswith("my") and cleaned[2:] in {"g", "gee", "bro", "man"}:
        return True
    return False


def _normalize_social_tokens(message: str) -> List[str]:
    cleaned = _normalize_key(message)
    if not cleaned:
        return []
    cleaned = re.sub(r"\bwhat\s+s\s+up\b", "whats up", cleaned)
    cleaned = re.sub(r"\bwhat\s+up\b", "whats up", cleaned)
    cleaned = re.sub(r"\bwhatsup\b", "whats up", cleaned)
    cleaned = re.sub(r"\bwassup\b", "wassup", cleaned)
    cleaned = re.sub(r"\bwaz+up\b", "wassup", cleaned)
    return [_squash_repeated_letters(token) for token in cleaned.split() if token]


def _is_direct_address_token(token: str) -> bool:
    cleaned = re.sub(r"[^a-z0-9]+", "", _squash_repeated_letters(token))
    if not cleaned or not cleaned.isalpha():
        return False
    if len(cleaned) < 2 or len(cleaned) > 20:
        return False
    if _is_greeting_token(cleaned) or _is_social_filler_token(cleaned):
        return False
    if cleaned in DIRECT_ADDRESS_BLOCKLIST:
        return False
    return True


def _only_social_address_tokens(tokens: List[str], *, allow_whats_up: bool = True) -> bool:
    address_count = 0
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if _is_greeting_token(token) or _is_social_filler_token(token):
            idx += 1
            continue
        if allow_whats_up and token == "whats" and idx + 1 < len(tokens) and tokens[idx + 1] == "up":
            idx += 2
            continue
        if _is_direct_address_token(token):
            address_count += 1
            if address_count > 2:
                return False
            idx += 1
            continue
        return False
    return True

GREETING_WORDS = {
    "hello",
    "hi",
    "hey",
    "howdy",
    "sup",
    "yo",
    "yoo",
    "yooo",
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
    r"\b("
    r"direct and engaging|friendly and|warm and|clear and|honest and|"
    r"my style is|i like to be|i tend to be|as someone who|in the spirit of|"
    r"definitive statements?|declarative statements?|signature (?:move|moves|phrase|phrases|opening|openings)|"
    r"opening move|rhetorical move|voice pattern|persona pattern|style descriptor|"
    r"commitment means"
    r")\b",
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
QUOTEY_CREATOR_OPENER_RE = re.compile(
    r"\b("
    r"bro\s+needs\s+to\s+see\s+this|"
    r"you\s+need\s+to\s+see\s+this|"
    r"watch\s+this|"
    r"listen\s+to\s+this|"
    r"if\s+you\s+know\s+you\s+know|"
    r"this\s+(?:is\s+)?why|"
    r"pretty\s+much\s+every|"
    r"most\s+\w+\s+think|"
    r"get\s*f\*?cked|"
    r"the\s+\w+\s+industry\s+is\s+f\*?cked|"
    r"stop\s+scrolling|"
    r"pov\b|"
    r"hot\s+take\b"
    r")\b",
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

CATEGORY_OPENERS = {
    "business": ["Alright", "Good, you're here", "Let's get into it", "Good to see you"],
    "fitness": ["Yo", "Good, you're here", "Alright", "Let's get moving"],
    "trading": ["Alright", "Good to see you", "Let's look at it", "I'm here"],
    "creator": ["Hey", "Good to see you", "Alright", "Let's make this useful"],
    "finance": ["Alright", "Good to see you", "Let's keep this clean", "I'm here"],
    "general": ["Hey", "Alright", "Good to see you", "I'm here"],
}

CATEGORY_QUESTIONS = {
    "business": ["What's the move?", "What are you building right now?", "What are we working through?"],
    "fitness": ["What are you training for?", "What are you working on today?", "What's the goal right now?"],
    "trading": ["What are you looking at today?", "What's the setup?", "What are we trying to work out?"],
    "creator": ["What are you trying to grow?", "What's the idea?", "What are you working on?"],
    "finance": ["What's the money move?", "What are you trying to work out?", "What's the goal?"],
    "general": ["What's on your mind?", "What are we working through?", "Where do you want to start?"],
}

CASUAL_USER_OPENERS = ["Yo", "Hey", "Alright", "I'm here"]
LOW_ENERGY_OPENERS = ["Hey", "I'm here", "Good to hear from you", "Take a breath"]


def is_greeting(message: str) -> bool:
    cleaned = _normalize_key(message)
    if not cleaned:
        return False
    cleaned = re.sub(r"\bwhat\s+s\s+up\b", "whats up", cleaned)
    cleaned = re.sub(r"\bwhatsup\b", "whats up", cleaned)
    if cleaned in GREETING_TRIGGERS:
        return True

    tokens = _normalize_social_tokens(message)
    if not tokens:
        return False

    normalized_text = " ".join(tokens)
    if re.search(r"\bwhats\s+up\s+with\b", normalized_text):
        return False
    if CHECKIN_PHRASE_RE.search(normalized_text):
        # "how are you" / "what have you been up to" is small talk, not a
        # pure greeting. The interaction router handles that lighter route.
        return False
    has_greeting = any(_is_greeting_token(token) for token in tokens)
    if not has_greeting and normalized_text.startswith("whats up"):
        return _only_social_address_tokens(tokens, allow_whats_up=True)
    if len(tokens) <= 7 and _is_greeting_token(tokens[0]) and _only_social_address_tokens(tokens):
        return True
    return has_greeting and _only_social_address_tokens(tokens)


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
        self._seeded_greetings: Dict[str, Dict[str, str]] = defaultdict(dict)

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

    def _category_key(self, creator_category: Optional[str], creator_name: Optional[str] = None) -> str:
        combined = f"{creator_category or ''} {creator_name or ''}".lower()
        if re.search(r"\b(gym|fitness|muscle|workout|bodybuilding|training|sport|soccer|health)\b", combined):
            return "fitness"
        if re.search(r"\b(trading|forex|stock|market|crypto|invest)\b", combined):
            return "trading"
        if re.search(r"\b(content|creator|youtube|tiktok|instagram|audience)\b", combined):
            return "creator"
        if re.search(r"\b(finance|money|wealth|real estate|property)\b", combined):
            return "finance"
        if re.search(r"\b(business|sales|offer|agency|startup|entrepreneur|acquisition|marketing|scale)\b", combined):
            return "business"
        return "general"

    def _message_vibe(self, user_message: Optional[str]) -> Dict[str, Any]:
        text = str(user_message or "").lower().strip()
        words = set(re.findall(r"[a-z0-9']+", text))
        if not text:
            return {"tone": "neutral", "is_checkin": False}
        is_checkin = bool(
            re.search(r"\bhow (?:are|r|you|u|ya)\b", text)
            or re.search(r"\bhow'?s (?:it|life|things)\b", text)
            or re.search(r"\bhow (?:u|you) go(?:ing|in)\b", text)
        )
        if words & {"yo", "yoo", "bro", "mate", "bruh", "g"} or "my g" in text or "ma g" in text:
            tone = "casual"
        elif any(term in text for term in ("stressed", "overwhelmed", "tired", "anxious", "lost", "stuck")):
            tone = "soft"
        elif "!" in text or any(term in text for term in ("pumped", "excited", "lets go", "let's go")):
            tone = "high"
        else:
            tone = "neutral"
        return {"tone": tone, "is_checkin": is_checkin}

    # Prepositions that create semantic confusion when a user name is appended
    # e.g. "Let's talk about" + "Nathan" → reads as Nathan being the topic
    _DANGLING_PREPS = frozenset({
        "about", "with", "to", "for", "from", "at", "of", "by", "on",
    })

    # YouTube / broadcast filler that should never appear in 1-on-1 chat
    _BROADCAST_FILLER = (
        "my channel", "the channel", "this channel",
        "welcome back to", "back to my",
        "subscribe", "like and subscribe", "hit the bell",
        "notification bell", "smash that", "click the link",
        "thanks for watching", "thanks for tuning",
        "in today's video", "in this video", "today's episode",
    )

    def _looks_like_safe_opener(self, text: str) -> bool:
        normalized = self._clean_text(text)
        if not normalized:
            return False
        if re.search(r"[\(\)\[\]\{\}]", normalized):
            return False
        if QUESTION_RE.search(normalized):
            return False
        if STYLE_DESCRIPTION_RE.search(normalized):
            return False
        if SPECIFIC_QUESTION_RE.search(normalized):
            return False
        if GENERIC_AI_RE.search(normalized):
            return False
        if QUOTEY_CREATOR_OPENER_RE.search(normalized) or looks_like_raw_content_hook(normalized):
            return False
        lower = normalized.lower()
        if lower.startswith(("what", "where", "how", "which", "why", "when", "if", "because", "since", "unless")):
            return False
        if len(normalized.split()) > 7:
            return False
        # Reject broadcast / YouTube filler — not appropriate for 1-on-1 chat
        if any(filler in lower for filler in self._BROADCAST_FILLER):
            return False
        # Reject openers whose last word is a dangling preposition —
        # appending a user name would make the name the object of the
        # preposition instead of the addressee.
        last_word = normalized.split()[-1].lower().rstrip(".,!?")
        if last_word in self._DANGLING_PREPS:
            return False
        return True

    def _looks_like_quoted_content_line(self, text: str) -> bool:
        normalized = self._clean_text(text)
        if not normalized:
            return False
        lower = normalized.lower()
        if QUOTEY_CREATOR_OPENER_RE.search(normalized) or looks_like_raw_content_hook(normalized):
            return True
        if re.search(r"\b(link in bio|caption|subscribe|follow for|part \d+|day \d+)\b", lower):
            return True
        if len(normalized.split()) > 8 and not normalized.endswith("?"):
            return True
        return False

    def _extract_greeting_examples(self, style_fingerprint: Dict[str, Any]) -> List[str]:
        style_fingerprint = sanitize_style_fingerprint_for_runtime(self._coerce_dict(style_fingerprint))
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
        style_fingerprint = sanitize_style_fingerprint_for_runtime(self._coerce_dict(style_fingerprint))
        voice_patterns = self._coerce_dict(creator_profile.get("voice_patterns"))
        behavioral = self._coerce_dict(creator_profile.get("behavioral_fingerprint"))
        interaction = self._coerce_dict(voice_patterns.get("interaction_style"))
        rhythm = self._coerce_dict(voice_patterns.get("rhythm"))
        sentence_structure = self._coerce_dict(voice_patterns.get("sentence_structure"))
        speech_mechanics = self._coerce_dict(style_fingerprint.get("speech_mechanics"))
        voice_profile = sanitize_voice_profile_for_runtime(voice_profile)
        creator_phrases = self._clean_options(
            list(voice_profile.get("signature_phrases") or [])
            + list(behavioral.get("catchphrases") or [])
        )

        openers = self._clean_options(
            list(voice_profile.get("greeting_high_energy") or [])
            + list(voice_profile.get("greeting_neutral") or [])
            + list(voice_profile.get("greeting_short") or [])
            + list(voice_profile.get("greetings") or [])
            + clean_style_phrase_list(speech_mechanics.get("signature_openings") or [], limit=4)
            + self._extract_greeting_examples(style_fingerprint)
        )
        openers = [item for item in openers if self._looks_like_safe_opener(item)]

        # Extract golden greeting examples as full greetings (not just openers)
        golden_greetings = self._extract_golden_greeting_patterns(style_fingerprint)

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

        # Extract signature landings for question style
        sig_landings = self._clean_options(
            clean_style_phrase_list(speech_mechanics.get("signature_landings") or [], limit=4)
        )

        # Extract humor profile for greeting tone
        humor = self._coerce_dict(speech_mechanics.get("humor_profile"))

        return {
            "openers": openers,
            "golden_greetings": golden_greetings,
            "creator_phrases": [
                item
                for item in creator_phrases
                if self._looks_like_safe_opener(item) and not self._looks_like_quoted_content_line(item)
            ],
            "address": address,
            "energy": energy,
            "pacing": pacing,
            "sentence_length": sentence_length,
            "tone_traits": tone_traits,
            "signature_landings": sig_landings,
            "humor_profile": humor,
            # Deep identity signals for richer greetings
            "identity_signature": self._coerce_dict(style_fingerprint.get("identity_signature")),
            "mode_greeting_rules": self._coerce_dict(
                (style_fingerprint.get("mode_matrix") or {}).get("greeting")
            ),
            "worldview_beliefs": list(
                (self._coerce_dict(style_fingerprint.get("worldview")).get("core_beliefs") or [])
            )[:3],
            "signature_moves": list(
                (style_fingerprint.get("signature_moves") or style_fingerprint.get("rhetorical_moves") or [])
            )[:3],
            "anti_persona_lines": list(
                (self._coerce_dict(style_fingerprint.get("anti_persona")).get("forbidden_generic_coach_lines") or [])
            )[:3],
            "power_position": self._coerce_dict(style_fingerprint.get("identity_signature")).get("power_position", ""),
        }

    def _extract_golden_greeting_patterns(self, style_fingerprint: Dict[str, Any]) -> List[str]:
        """
        Extract full greeting patterns from golden_examples.
        These are the creator's own words for how they greet people.
        """
        style_fingerprint = sanitize_style_fingerprint_for_runtime(self._coerce_dict(style_fingerprint))
        golden = self._coerce_dict(style_fingerprint.get("golden_examples"))
        examples = list(golden.get("greeting") or [])
        patterns: List[str] = []
        for ex in examples:
            text = str(ex or "").strip()
            if not text or len(text) < 5:
                continue
            # Skip broadcast / YouTube filler that isn't appropriate for 1-on-1 chat
            lower = text.lower()
            if any(filler in lower for filler in self._BROADCAST_FILLER):
                continue
            # Take up to first 2 sentences as the greeting pattern
            sentences = re.split(r'(?<=[.!?])\s+', text)
            pattern = " ".join(sentences[:2]).strip()
            if pattern and len(pattern.split()) <= 20 and self._looks_human(pattern):
                patterns.append(pattern)
        return self._clean_options(patterns)

    def _fallback_openers(
        self,
        signals: Dict[str, Any],
        returning: bool,
        creator_category: Optional[str] = None,
        creator_name: Optional[str] = None,
        user_message: Optional[str] = None,
    ) -> List[str]:
        energy = signals.get("energy", "medium")
        tone = signals.get("tone_traits", {})
        blunt = float(tone.get("blunt", 0.0) or 0.0)
        supportive = float(tone.get("supportive", 0.0) or 0.0)
        hype = float(tone.get("hype", 0.0) or 0.0)
        category = self._category_key(creator_category, creator_name)
        message_vibe = self._message_vibe(user_message)

        # Prioritize true greeting examples from the creator's own voice. Raw
        # catchphrases/content hooks are intentionally not used as openers:
        # they make a normal "hello" feel like a clipped transcript quote.
        golden = signals.get("golden_greetings") or []

        # Build a voice-colored pool, starting with real creator signals
        pool: List[str] = []
        for g in golden[:3]:
            opener = re.split(r'[.!?]', g)[0].strip()
            if opener and self._looks_like_safe_opener(opener):
                pool.append(opener)

        if returning:
            if pool:
                # Add voice-colored returning variants
                pool.extend(["Good to see you again", "Glad you're back", "Back again", "Good, you're back"])
                return self._clean_options(pool)[:7]
            if supportive >= 0.7:
                return ["Good to hear from you again", "Glad you're back", "Good to see you again"]
            if hype >= 0.65 or energy == "high":
                return ["Back at it", "Good, you're back", "Let's get back into it"]
            return ["Good to see you again", "Glad you're back", "Alright, good to have you back"]

        if pool:
            category_pool = CATEGORY_OPENERS.get(category, CATEGORY_OPENERS["general"])
            if message_vibe.get("tone") == "casual":
                pool = CASUAL_USER_OPENERS + pool
            elif message_vibe.get("tone") == "soft":
                pool = LOW_ENERGY_OPENERS + pool
            pool.extend(category_pool)
            return self._clean_options(pool)[:8]

        category_pool = list(CATEGORY_OPENERS.get(category, CATEGORY_OPENERS["general"]))
        if message_vibe.get("tone") == "casual":
            category_pool = CASUAL_USER_OPENERS + category_pool
        elif message_vibe.get("tone") == "soft":
            category_pool = LOW_ENERGY_OPENERS + category_pool
        if hype >= 0.65 or energy == "high":
            return self._clean_options(category_pool + ["Let's go", "Alright", "Good, you're here", "I'm with you"])
        if supportive >= 0.7 or energy == "calm" or energy == "low":
            return self._clean_options(category_pool + ["Hey", "Good to hear from you", "Glad you reached out", "I'm here"])
        if blunt >= 0.65:
            return self._clean_options(category_pool + ["Alright", "Good, let's talk", "Let's get into it", "Say it straight"])
        return self._clean_options(category_pool + ["Hey", "Good to see you", "Alright", "I'm here"])

    def _broad_questions(
        self,
        creator_category: Optional[str],
        signals: Dict[str, Any],
        returning: bool,
        know_name: bool,
        creator_name: Optional[str] = None,
        user_message: Optional[str] = None,
    ) -> List[str]:
        energy = signals.get("energy", "medium")
        tone = signals.get("tone_traits", {})
        blunt = float(tone.get("blunt", 0.0) or 0.0)
        supportive = float(tone.get("supportive", 0.0) or 0.0)
        hype = float(tone.get("hype", 0.0) or 0.0)
        category = self._category_key(creator_category, creator_name)
        message_vibe = self._message_vibe(user_message)

        if not know_name:
            # Use power_position and energy to flavor the name ask.
            # NOTE: avoid form-feel phrasings like "What should I call you?",
            # "What's your name?", "What do you want me to call you?" — those
            # make the bot sound like an intake assistant. Prefer openers a
            # real person would type in a DM.
            power = str(signals.get("power_position") or "").lower()
            energy = signals.get("energy", "medium")
            tone = signals.get("tone_traits", {})
            blunt = float(tone.get("blunt", 0.0) or 0.0)
            hype = float(tone.get("hype", 0.0) or 0.0)
            supportive = float(tone.get("supportive", 0.0) or 0.0)
            if blunt >= 0.65 or "authority" in power:
                return ["Who am I talking to?", "Who do I have here?", "Who's this?"]
            if hype >= 0.65 or energy == "high":
                return ["Who am I talking to?", "Who do I have on the other end?", "Who's this?"]
            if "peer" in power or "equal" in power:
                return ["Who am I talking to?", "Who do I have here?", "Who's on the other end?"]
            if supportive >= 0.7 or "coach" in power or "mentor" in power:
                return ["Who do I have here?", "Who am I talking to?", "Who's on the other side?"]
            return ["Who am I talking to?", "Who do I have here?", "Who's this?"]

        # Check if mode_greeting_rules has specific question guidance
        greeting_rules = signals.get("mode_greeting_rules") or {}
        greeting_question_hint = (
            greeting_rules.get("opening_question")
            or greeting_rules.get("first_question")
            or greeting_rules.get("opener_question")
        )
        # If the creator's style fingerprint defines a preferred opening question pattern,
        # surface it as first option — but only if it's genuinely a question, not a
        # style description like "Bring me the real question..."
        rule_questions: List[str] = []
        if greeting_question_hint and isinstance(greeting_question_hint, str) and len(greeting_question_hint) < 80:
            cleaned = self._clean_text(greeting_question_hint).rstrip(" ?!.") + "?"
            if cleaned and len(cleaned.split()) <= 12 and not re.search(
                r"\b(bring me|give me the|skip the|the messy|the real question|unpack|unfinished)\b",
                cleaned, re.IGNORECASE,
            ):
                rule_questions.append(cleaned)

        if returning:
            base = []
            if supportive >= 0.7:
                base = ["What's been on your mind?", "Where should we pick this up?", "What's going on?"]
            elif hype >= 0.65 or energy == "high":
                base = ["What's the move?", "What's going on?", "Where should we pick this up?"]
            elif blunt >= 0.65:
                base = ["What's up?", "Where do you want to start?", "What's on your mind?"]
            else:
                base = ["What's up?", "What's on your mind?", "Where should we pick this up?"]
            return base

        base = []
        category_questions = CATEGORY_QUESTIONS.get(category, CATEGORY_QUESTIONS["general"])
        if message_vibe.get("is_checkin"):
            checkin_questions = ["How are you doing?", "What's been happening?", "What are we working through today?"]
            return self._clean_options(checkin_questions + category_questions)
        if supportive >= 0.7 or energy == "calm" or energy == "low":
            base = ["What's on your mind?", "What's going on?", "Where do you want to start?"]
        elif hype >= 0.65 or energy == "high":
            base = ["What's the move?", "What's going on?", "Where do you want to start?"]
        elif blunt >= 0.65:
            base = ["What's up?", "What's on your mind?", "Where do you want to start?"]
        else:
            base = ["What's up?", "What's on your mind?", "Where do you want to start?"]
        return self._clean_options(category_questions + base)

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
        if re.search(r"[\(\)\[\]\{\}]", text):
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
        if QUOTEY_CREATOR_OPENER_RE.search(text):
            return False
        # Reject broadcast / YouTube filler
        if any(filler in lowered for filler in self._BROADCAST_FILLER):
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
        user_message: Optional[str] = None,
    ) -> str:
        voice_profile = voice_profile or {}
        style_fingerprint = style_fingerprint or {}
        creator_profile = creator_profile or {}
        key = self._creator_key(creator_name, creator_category, user_name)
        returning = bool(conversation_history and len(conversation_history) > 2)
        seed_cache_key = (
            f"{variation_seed}|returning={int(returning)}|msg={_normalize_key(user_message)[:48]}"
            if variation_seed
            else ""
        )
        if seed_cache_key and seed_cache_key in self._seeded_greetings[key]:
            return self._seeded_greetings[key][seed_cache_key]
        signals = self._extract_creator_signals(creator_profile, voice_profile, style_fingerprint)

        openers = self._clean_options(
            signals.get("openers")
            or self._fallback_openers(
                signals,
                returning,
                creator_category=creator_category,
                creator_name=creator_name,
                user_message=user_message,
            )
        )
        if not openers:
            openers = self._fallback_openers(
                signals,
                returning,
                creator_category=creator_category,
                creator_name=creator_name,
                user_message=user_message,
            )
        questions = self._broad_questions(
            creator_category,
            signals,
            returning,
            bool((user_name or "").strip()),
            creator_name=creator_name,
            user_message=user_message,
        )
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
            "direct_informal": [item for item in openers if len(item.split()) <= 4] + self._fallback_openers(
                signals,
                returning,
                creator_category=creator_category,
                creator_name=creator_name,
                user_message=user_message,
            ),
            "brief_anchor": anchors + openers,
            "question_only": [""],
            "returning_reacknowledge": anchors + self._fallback_openers(
                signals,
                returning,
                creator_category=creator_category,
                creator_name=creator_name,
                user_message=user_message,
            ),
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
        # Cross-call de-duplication. Even when a variation_seed is supplied (so
        # strategy + opener picks are deterministic per seed), two different
        # seeds can still land on the same opener+question combo and produce a
        # near-identical greeting that only differs by punctuation. Walk the
        # alternative openers and questions until we find one that doesn't
        # word-overlap >=0.85 with anything already emitted for this creator.
        recent = self._recent_greetings[key]

        def _word_set(text: str) -> set:
            return set(re.findall(r"[a-z']+", str(text or "").lower()))

        def _too_similar(candidate_text: str) -> bool:
            cand_words = _word_set(candidate_text)
            if not cand_words:
                return False
            for prev in recent:
                # Same-seed replays return from the seed cache above. If we
                # reach this point, an identical line came from a different
                # seed, so rotate it.
                if prev == candidate_text:
                    return True
                prev_words = _word_set(prev)
                universe = cand_words | prev_words
                if not universe:
                    continue
                overlap = len(cand_words & prev_words) / len(universe)
                if overlap >= 0.85:
                    return True
            return False

        if _too_similar(final):
            opener_pool_full = self._ordered(opener_pool, opener_seed) or [opener]
            question_pool_full = self._ordered(questions, question_seed) or [question]
            for q_alt in question_pool_full:
                for o_alt in opener_pool_full:
                    if o_alt == opener and q_alt == question:
                        continue
                    alt_candidate = self._compose_candidate(
                        strategy,
                        o_alt,
                        q_alt,
                        user_name or "",
                        address,
                        returning,
                    )
                    alt_final = self._safety_check(alt_candidate, user_name, signals, returning)
                    if alt_final and not _too_similar(alt_final):
                        final = alt_final
                        break
                else:
                    continue
                break
        recent.append(final)
        if seed_cache_key:
            self._seeded_greetings[key][seed_cache_key] = final
        return final


greeting_service = GreetingService()
