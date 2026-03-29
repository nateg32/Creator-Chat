import hashlib
import logging
import random
import re
from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

COLLECTIVE_GREETING_RE = re.compile(r"\b(everyone|everybody|guys|team|friends|fam|family|folks|yall|y'all|chat)\b", re.IGNORECASE)
QUESTION_RE = re.compile(r"([^?]{4,120}\?)")
GENERIC_TOPIC_RE = re.compile(r"^(business|mindset|life|growth|success|content|audience|brand|money|health|fitness|trading)$", re.IGNORECASE)
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
    "good to see you",
    "good to hear from you",
    "glad you're here",
    "im here",
}
GENERIC_QUESTIONS = {
    "what's the goal",
    "whats the goal",
    "what are you trying to figure out",
    "what's the biggest challenge today",
    "whats the biggest challenge today",
    "how can i help",
    "what is the goal",
    "what are you building right now",
    "what are we building",
    "what are you working on",
    "what do you need help with today",
    "what's on your mind right now",
    "what are you trying to sort out",
    "what are you working through today",
}
DIRECT_DM_OPENERS = ["Hi", "Hey", "Hello", "What's up"]
NAME_QUESTIONS = ["What's your name?", "What should I call you?", "Who am I talking to?"]
CATEGORY_QUESTIONS = {
    "business": [
        "Where is the bottleneck right now?",
        "What part of the offer feels soft right now?",
        "Where is the leverage point right now?",
    ],
    "ecommerce": [
        "Where is the store leaking right now?",
        "What part of the offer needs tightening?",
        "Where is conversion slipping right now?",
    ],
    "fitness": [
        "What part of your training feels off right now?",
        "Where does your routine keep breaking?",
        "What are you trying to clean up in your fitness right now?",
    ],
    "trading": [
        "Where are you getting chopped up right now?",
        "What part of the process feels shaky?",
        "What are you trying to sharpen in your trading right now?",
    ],
    "general": [
        "What's on your mind?",
        "What needs a clearer next move?",
        "Where are you getting stuck right now?",
    ],
}


def _normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


NORMALIZED_GENERIC_OPENERS = {_normalize_key(item) for item in GENERIC_OPENERS}
NORMALIZED_GENERIC_QUESTIONS = {_normalize_key(item) for item in GENERIC_QUESTIONS}


class GreetingService:
    """
    Handles conversational greeting logic.
    Ensures creator specific voice, avoids repetition, and applies constraints.
    """

    def __init__(self) -> None:
        self._rng = random.SystemRandom()
        self._recent_greetings: Dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=24))

    def _clean_text(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = COLLECTIVE_GREETING_RE.sub("", text)
        text = re.sub(r"\s+", " ", text).strip(" ,.!?-")
        return text

    def _clean_options(self, values: Iterable[Any]) -> List[str]:
        cleaned: List[str] = []
        seen = set()
        for value in values or []:
            text = self._clean_text(value)
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

    def _ordered(self, options: List[str], seed_key: Optional[str] = None) -> List[str]:
        cleaned = self._dedupe_preserve(options)
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

    def _dedupe_preserve(self, values: Iterable[Any]) -> List[str]:
        cleaned: List[str] = []
        seen = set()
        for value in values or []:
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        return cleaned

    def _looks_like_opening_hook(self, text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        if normalized.startswith(("what", "where", "how", "which", "who", "why", "when")):
            return False
        if len(normalized.split()) > 8:
            return False
        if re.search(r"\b(hi|hey|yo|hello|listen|look|alright|all right|what's|whats|let's|lets|good|right|okay|ok)\b", normalized):
            return True
        return len(normalized.split()) <= 3

    def _looks_like_question(self, text: str) -> bool:
        normalized = self._clean_text(text).lower()
        if not normalized:
            return False
        return normalized.endswith("?") or normalized.startswith(("what", "where", "how", "which", "who", "why", "when"))

    def _normalize_prompt_key(self, value: Any) -> str:
        return _normalize_key(value)

    def _coerce_dict(self, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _extract_sentence_candidates(self, values: Iterable[Any], *, questions_only: bool = False, limit: int = 8) -> List[str]:
        candidates: List[str] = []
        for value in values or []:
            text = str(value or "").strip()
            if not text:
                continue
            segments: List[str] = []
            if questions_only:
                segments.extend(match.group(1) for match in QUESTION_RE.finditer(text))
            else:
                segments.extend(re.split(r"[\n.!?]+", text))
            for segment in segments:
                cleaned = self._clean_text(segment)
                if not cleaned:
                    continue
                if questions_only:
                    if not self._looks_like_question(cleaned):
                        continue
                    cleaned = cleaned.rstrip(" ?!.") + "?"
                elif not self._looks_like_opening_hook(cleaned):
                    continue
                candidates.append(cleaned)
                if len(candidates) >= limit:
                    return self._clean_options(candidates)
        return self._clean_options(candidates)

    def _extract_topic_seeds(self, style_fingerprint: Dict[str, Any]) -> List[str]:
        style_fingerprint = self._coerce_dict(style_fingerprint)
        domain_map = self._coerce_dict(style_fingerprint.get("domain_map"))
        value_model = self._coerce_dict(style_fingerprint.get("value_model"))
        content_truth = self._coerce_dict(style_fingerprint.get("content_truth"))
        belief_graph = self._coerce_dict(style_fingerprint.get("belief_graph"))

        values = self._clean_options(
            list(domain_map.get("strong_topics") or [])
            + list(domain_map.get("adjacent_topics") or [])
            + list(style_fingerprint.get("recurring_themes") or [])
            + list(content_truth.get("products") or [])
            + list(content_truth.get("businesses") or [])
            + list(value_model.get("decision_heuristics") or [])
            + list(belief_graph.get("core_beliefs") or [])
        )

        topics: List[str] = []
        for value in values:
            if len(value.split()) > 7:
                continue
            if GENERIC_TOPIC_RE.match(value):
                continue
            topics.append(value)
            if len(topics) >= 8:
                break
        return topics

    def _style_signals(self, style_fingerprint: Dict[str, Any]) -> Dict[str, List[str]]:
        style_fingerprint = self._coerce_dict(style_fingerprint)
        lexical = self._coerce_dict(style_fingerprint.get("lexical_rules"))
        speech = self._coerce_dict(style_fingerprint.get("speech_mechanics"))
        mode_matrix = self._coerce_dict(style_fingerprint.get("mode_matrix"))
        greeting_mode = self._coerce_dict(mode_matrix.get("greeting"))
        anti = self._coerce_dict(style_fingerprint.get("anti_persona"))
        disambiguation = self._coerce_dict(style_fingerprint.get("disambiguation_markers"))
        golden_examples = self._coerce_dict(style_fingerprint.get("golden_examples"))
        voice_patterns = self._coerce_dict(style_fingerprint.get("voice_patterns"))
        rhetorical_moves = self._coerce_dict(voice_patterns.get("rhetorical_moves"))
        interaction_style = self._coerce_dict(voice_patterns.get("interaction_style"))

        greeting_examples = list(golden_examples.get("greeting") or [])
        opener_candidates = self._clean_options(
            self._extract_sentence_candidates(greeting_examples, questions_only=False, limit=10)
            + self._extract_sentence_candidates(speech.get("signature_openings") or [], questions_only=False, limit=8)
            + self._extract_sentence_candidates(rhetorical_moves.get("opens_with") or [], questions_only=False, limit=4)
            + [greeting_mode.get("opening_move")]
            + [interaction_style.get("how_they_address_audience")]
            + [interaction_style.get("audience_address")]
        )
        question_candidates = self._clean_options(
            self._extract_sentence_candidates(greeting_examples, questions_only=True, limit=10)
            + self._extract_sentence_candidates([greeting_mode.get("question_style")], questions_only=True, limit=4)
        )
        signature_phrases = self._clean_options(
            list(style_fingerprint.get("signature_phrases") or [])
            + list(lexical.get("signature_phrases") or [])
        )
        high_signal_words = self._clean_options(
            list(lexical.get("high_signal_words") or [])
            + list(style_fingerprint.get("lexicon") or [])
        )
        banned_frames = self._clean_options(
            list(lexical.get("banned_frames") or [])
            + list(anti.get("forbidden_generic_coach_lines") or [])
            + list(greeting_mode.get("forbidden") or [])
            + list(disambiguation.get("must_avoid") or [])
        )

        return {
            "openers": opener_candidates,
            "questions": question_candidates,
            "signature_phrases": signature_phrases,
            "high_signal_words": high_signal_words,
            "banned_frames": banned_frames,
            "topics": self._extract_topic_seeds(style_fingerprint),
        }

    def _creator_seed(
        self,
        creator_name: Optional[str],
        creator_category: Optional[str],
        voice_profile: Dict[str, Any],
        style_fingerprint: Dict[str, Any],
    ) -> str:
        style_signals = self._style_signals(style_fingerprint)
        signature_phrases = self._clean_options(
            (voice_profile.get("signature_phrases") or [])[:4] + style_signals.get("signature_phrases", [])[:4]
        )
        common_words = self._clean_options(
            (voice_profile.get("common_words") or [])[:4] + style_signals.get("high_signal_words", [])[:4]
        )
        energy_bucket = (voice_profile.get("energy") or {}).get("bucket", "MID")
        parts = [
            str(creator_name or "").strip().lower(),
            str(creator_category or "").strip().lower(),
            str(energy_bucket or "MID").strip().upper(),
            *[item.lower() for item in signature_phrases],
            *[item.lower() for item in common_words],
            *[self._normalize_prompt_key(item) for item in style_signals.get("topics", [])[:3]],
        ]
        return "|".join(part for part in parts if part)

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
                "Where are you actually stuck?",
                "What part needs tightening right now?",
            ])
        if supportive >= 0.7:
            questions.extend([
                "What's been weighing on you lately?",
                "What feels hardest right now?",
                "Where do you need a steadier next step?",
            ])
        if hype >= 0.65 or energy_bucket == "HIGH":
            questions.extend([
                "What's the move right now?",
                "Where are you pressing next?",
                "What are you going after right now?",
            ])
        return questions

    def _topic_question_bank(self, topics: List[str], tone_traits: Dict[str, Any], energy_bucket: str) -> List[str]:
        blunt = float(tone_traits.get("blunt", 0.0) or 0.0)
        supportive = float(tone_traits.get("supportive", 0.0) or 0.0)
        hype = float(tone_traits.get("hype", 0.0) or 0.0)

        questions: List[str] = []
        for topic in topics[:4]:
            cleaned = self._clean_text(topic)
            if not cleaned:
                continue
            if blunt >= 0.65:
                questions.extend([
                    f"Where is {cleaned} breaking right now?",
                    f"What part of {cleaned} are you overcomplicating?",
                ])
            elif supportive >= 0.7:
                questions.extend([
                    f"What feels heavy around {cleaned} right now?",
                    f"Where does {cleaned} feel hardest at the moment?",
                ])
            elif hype >= 0.65 or energy_bucket == "HIGH":
                questions.extend([
                    f"What's the play with {cleaned} right now?",
                    f"Where are you pushing {cleaned} next?",
                ])
            else:
                questions.extend([
                    f"What part of {cleaned} needs a cleaner decision right now?",
                    f"Where is {cleaned} getting muddy right now?",
                ])
        return self._clean_options(questions)

    def _build_openers(
        self,
        voice_profile: Dict[str, Any],
        style_fingerprint: Dict[str, Any],
        energy_bucket: str,
        tone_traits: Dict[str, Any],
    ) -> List[str]:
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

        style_signals = self._style_signals(style_fingerprint)
        openers = self._clean_options(
            style_signals.get("openers", [])
            + signature_openers
            + base_openers
            + filler_openers
            + style_signals.get("signature_phrases", [])
        )
        if not openers:
            openers = self._clean_options(self._fallback_openers(energy_bucket, tone_traits))
        return openers

    def _banned_frames(self, voice_profile: Dict[str, Any], style_fingerprint: Dict[str, Any]) -> List[str]:
        voice_frames = self._clean_options(((voice_profile.get("lexical_rules") or {}).get("banned_frames") or []))
        style_frames = self._style_signals(style_fingerprint).get("banned_frames", [])
        return self._clean_options(list(style_frames) + list(voice_frames))

    def _is_generic_or_banned(self, text: str, banned_frames: List[str]) -> bool:
        normalized = self._normalize_prompt_key(text).rstrip(" ?!.")
        if not normalized:
            return True
        if normalized in NORMALIZED_GENERIC_OPENERS or normalized in NORMALIZED_GENERIC_QUESTIONS:
            return True
        return any(frame and self._normalize_prompt_key(frame) in normalized for frame in banned_frames)

    def _build_known_name_questions(
        self,
        voice_profile: Dict[str, Any],
        style_fingerprint: Dict[str, Any],
        creator_category: Optional[str],
        tone_traits: Dict[str, Any],
        energy_bucket: str,
    ) -> List[str]:
        category_key = str(creator_category or "general").strip().lower()
        style_signals = self._style_signals(style_fingerprint)
        questions = self._clean_options(voice_profile.get("greeting_questions", []) or [])
        questions.extend(style_signals.get("questions", []))
        questions.extend(self._topic_question_bank(style_signals.get("topics", []), tone_traits, energy_bucket))

        if not questions:
            questions.extend(self._question_tone_bank(tone_traits, energy_bucket))

        if not questions:
            questions.extend(CATEGORY_QUESTIONS.get(category_key, CATEGORY_QUESTIONS["general"]))

        return self._clean_options(questions)

    def _build_unknown_name_questions(
        self,
        tone_traits: Dict[str, Any],
        energy_bucket: str,
        style_fingerprint: Dict[str, Any],
    ) -> List[str]:
        questions = list(NAME_QUESTIONS)
        style_signals = self._style_signals(style_fingerprint)
        questions.extend(
            [
                question
                for question in style_signals.get("questions", [])
                if any(marker in self._normalize_prompt_key(question) for marker in ("name", "call you", "talking to"))
            ]
        )
        if float(tone_traits.get("blunt", 0.0) or 0.0) >= 0.65:
            questions.insert(0, "What's your name?")
        elif float(tone_traits.get("supportive", 0.0) or 0.0) >= 0.7 or energy_bucket == "LOW":
            questions.insert(0, "What should I call you?")
        elif float(tone_traits.get("hype", 0.0) or 0.0) >= 0.65 or energy_bucket == "HIGH":
            questions.insert(0, "Who am I talking to?")
        return self._clean_options(questions) or NAME_QUESTIONS

    def _punctuate_statement(self, text: str, energy_bucket: str) -> str:
        statement = self._clean_text(text).rstrip(" ?!.")
        if not statement:
            return ""
        punct = "!" if energy_bucket == "HIGH" else "."
        return f"{statement}{punct}"

    def _opening_variants(
        self,
        openers: List[str],
        user_name: Optional[str],
        energy_bucket: str,
        creator_seed: str,
    ) -> List[str]:
        clean_name = (user_name or "").strip()
        variants: List[str] = []
        for opener in self._ordered(openers, f"{creator_seed}|opening-raw")[:8]:
            base = self._punctuate_statement(opener, energy_bucket)
            if not base:
                continue
            variants.append(base)
            if clean_name:
                naked = base[:-1]
                variants.append(f"{naked} {clean_name}{base[-1]}")
                variants.append(f"{naked}, {clean_name}{base[-1]}")
        return self._dedupe_preserve(variants)

    def _composed_topic_questions(
        self,
        topics: List[str],
        tone_traits: Dict[str, Any],
        energy_bucket: str,
    ) -> List[str]:
        blunt = float(tone_traits.get("blunt", 0.0) or 0.0)
        supportive = float(tone_traits.get("supportive", 0.0) or 0.0)
        hype = float(tone_traits.get("hype", 0.0) or 0.0)
        variants: List[str] = []

        for topic in topics[:4]:
            cleaned = self._clean_text(topic)
            if not cleaned:
                continue
            if blunt >= 0.65:
                variants.extend([
                    f"Where is {cleaned} actually breaking right now?",
                    f"What part of {cleaned} needs tightening first?",
                    f"What's the bottleneck in {cleaned} right now?",
                ])
            elif supportive >= 0.7:
                variants.extend([
                    f"What feels heaviest around {cleaned} right now?",
                    f"Where does {cleaned} feel hardest at the moment?",
                    f"What part of {cleaned} needs the gentlest next step?",
                ])
            elif hype >= 0.65 or energy_bucket == "HIGH":
                variants.extend([
                    f"What's the move with {cleaned} right now?",
                    f"Where are you pushing {cleaned} next?",
                    f"What are you trying to unlock in {cleaned} right now?",
                ])
            else:
                variants.extend([
                    f"What part of {cleaned} needs a cleaner decision right now?",
                    f"Where is {cleaned} getting muddy for you?",
                    f"What's the real constraint around {cleaned} right now?",
                ])
        return self._clean_options(variants)

    def _composed_general_questions(self, tone_traits: Dict[str, Any], energy_bucket: str) -> List[str]:
        blunt = float(tone_traits.get("blunt", 0.0) or 0.0)
        supportive = float(tone_traits.get("supportive", 0.0) or 0.0)
        hype = float(tone_traits.get("hype", 0.0) or 0.0)

        if blunt >= 0.65:
            return [
                "What's the real thing you want to solve?",
                "Where are you actually stuck?",
                "What needs tightening first?",
                "What are we really dealing with here?",
            ]
        if supportive >= 0.7:
            return [
                "What feels hardest right now?",
                "What needs a steadier next step?",
                "What's been sitting heavy on you lately?",
                "Where do you want to start, the messy version is fine?",
            ]
        if hype >= 0.65 or energy_bucket == "HIGH":
            return [
                "What's the move right now?",
                "What are you going after next?",
                "Where do you want momentum first?",
                "What are we attacking first?",
            ]
        return [
            "What's most live for you right now?",
            "What needs a clearer next move?",
            "Where do you want to start?",
            "What are you trying to sort out right now?",
        ]

    def _question_variants(
        self,
        user_name: Optional[str],
        voice_profile: Dict[str, Any],
        style_fingerprint: Dict[str, Any],
        creator_category: Optional[str],
        tone_traits: Dict[str, Any],
        energy_bucket: str,
    ) -> List[str]:
        style_signals = self._style_signals(style_fingerprint)
        if not (user_name or "").strip():
            return self._build_unknown_name_questions(tone_traits, energy_bucket, style_fingerprint)

        topic_questions = self._composed_topic_questions(style_signals.get("topics", []), tone_traits, energy_bucket)
        style_questions = self._clean_options(
            list(style_signals.get("questions", []))
            + list(voice_profile.get("greeting_questions", []) or [])
        )
        if topic_questions:
            prioritized = self._clean_options(topic_questions + style_questions)
            if prioritized:
                return prioritized

        base_pool = self._build_known_name_questions(
            voice_profile,
            style_fingerprint,
            creator_category,
            tone_traits,
            energy_bucket,
        )
        enriched = self._clean_options(
            list(base_pool)
            + topic_questions
            + self._composed_general_questions(tone_traits, energy_bucket)
        )
        return enriched or CATEGORY_QUESTIONS.get(str(creator_category or "general").strip().lower(), CATEGORY_QUESTIONS["general"])

    def _select_greeting_candidate(
        self,
        candidates: List[str],
        creator_seed: str,
        variation_seed: Optional[str] = None,
    ) -> str:
        if not candidates:
            return ""
        ordered = self._ordered(candidates, variation_seed)
        if variation_seed:
            return ordered[0]
        recent = self._recent_greetings[creator_seed]
        for candidate in ordered:
            if candidate not in recent:
                recent.append(candidate)
                return candidate
        choice = ordered[0]
        recent.append(choice)
        return choice

    def generate_greeting(
        self,
        user_name: Optional[str],
        voice_profile: Dict[str, Any],
        include_question: bool = True,
        creator_name: Optional[str] = None,
        creator_category: Optional[str] = None,
        style_fingerprint: Optional[Dict[str, Any]] = None,
        variation_seed: Optional[str] = None,
    ) -> str:
        """
        Generate a naturally varied, persona-restricted greeting.
        Format: [Opener] [Optional Question]
        """
        voice_profile = voice_profile or {}
        style_fingerprint = style_fingerprint or {}
        energy_bucket = (voice_profile.get("energy") or {}).get("bucket", "MID")
        tone_traits = voice_profile.get("tone_traits") or {}
        creator_seed = self._creator_seed(creator_name, creator_category, voice_profile, style_fingerprint) or "default"
        banned_frames = self._banned_frames(voice_profile, style_fingerprint)

        openers = self._build_openers(voice_profile, style_fingerprint, energy_bucket, tone_traits)
        non_generic_openers = [item for item in openers if not self._is_generic_or_banned(item, banned_frames)]
        opening_lines = self._opening_variants(non_generic_openers or openers or DIRECT_DM_OPENERS, user_name, energy_bucket, creator_seed)
        clean_name = (user_name or "").strip()
        if clean_name:
            named_openings = [item for item in opening_lines if clean_name.lower() in item.lower()]
            if named_openings:
                opening_lines = named_openings
        final_greeting = self._select_greeting_candidate(
            opening_lines or [self._punctuate_statement(self._pick(DIRECT_DM_OPENERS, f"{creator_seed}|direct"), energy_bucket)],
            f"{creator_seed}|opening-only",
            variation_seed=f"{variation_seed}|opening" if variation_seed else None,
        )

        if not include_question:
            return final_greeting

        question_pool = self._question_variants(
            user_name,
            voice_profile,
            style_fingerprint,
            creator_category,
            tone_traits,
            energy_bucket,
        )
        filtered_questions = [item for item in question_pool if not self._is_generic_or_banned(item, banned_frames)]
        question_pool = filtered_questions or question_pool or ["What's on your mind?"]
        ordered_questions = self._ordered(question_pool, f"{variation_seed}|question" if variation_seed else None)

        candidates: List[str] = []
        for opener in self._ordered(opening_lines, f"{variation_seed}|opener-line" if variation_seed else None)[:8]:
            for question in ordered_questions[:10]:
                clean_question = self._clean_text(question).rstrip(" ?!.") + "?"
                candidate = f"{opener} {clean_question}".strip()
                if self._is_generic_or_banned(candidate, banned_frames):
                    continue
                candidates.append(candidate)

        final = self._select_greeting_candidate(
            candidates or [f"{final_greeting} What's on your mind?"],
            creator_seed,
            variation_seed=variation_seed,
        )
        return final


greeting_service = GreetingService()
