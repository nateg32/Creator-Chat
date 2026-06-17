
import logging
import json
import re
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

AFFIRMATIVE_FOLLOWUPS = {
    "yes", "yeah", "yep", "yup", "correct", "exactly", "that one", "this one",
    "yes please", "yep yes",
}

CLARIFICATION_FOLLOWUPS = {
    "wdym",
    "wdymean",
    "what do you mean",
    "what do u mean",
    "what u mean",
    "what do you mean by that",
    "what do u mean by that",
}

BOOK_CONTEXT_TERMS = (
    "book", "launch", "launched", "publish", "published", "publication",
    "release", "released", "release date", "come out", "write", "wrote", "writing",
)

RELATIONSHIP_TERMS = {
    "wife", "husband", "married", "dating", "girlfriend", "boyfriend",
    "relationship", "partner", "spouse", "fiance", "fiancee",
    "wifey", "missus", "misus", "mrs", "missis",
}

FAMILY_TERMS = {
    "kids", "children", "son", "daughter", "parents", "family",
    "mom", "dad", "siblings",
}

POLITICS_RELIGION_TERMS = {
    "politics", "political", "religion", "religious", "god", "faith",
    "church", "pray", "prayer", "vote", "voted", "party", "republican",
    "democrat", "christian", "muslim", "jewish", "atheist", "agnostic",
    "nihilist", "pagan", "worldview", "belief", "beliefs",
}

PRIVATE_BODY_SEX_TERMS = {
    "dick", "penis", "cock", "boobs", "nudes", "naked", "sex", "sexual",
    "body count", "virgin", "virginity",
}

USER_SELF_TERMS = {"i", "im", "i'm", "me", "my", "mine", "we", "our", "us"}
CREATOR_REF_TERMS = {"you", "your", "yours", "u", "ur"}
BUSINESS_CONTEXT_TERMS = {
    "business", "company", "startup", "sales", "client", "clients", "offer",
    "offers", "revenue", "team", "teams", "marketing", "content", "work",
    "job", "career", "entrepreneur", "founder", "build", "building", "built",
}

ADVICE_REQUEST_PATTERNS = (
    re.compile(r"\bwhat (?:would|should) (?:you|u) rec(?:o|c)o?m+e?n?d\b", re.IGNORECASE),
    re.compile(r"\bwhat do you rec(?:o|c)o?m+e?n?d\b", re.IGNORECASE),
    re.compile(r"\bwhat should i do\b", re.IGNORECASE),
    re.compile(r"\bhow do i\b", re.IGNORECASE),
    re.compile(r"\bhow can i\b", re.IGNORECASE),
    re.compile(r"\bany advice\b", re.IGNORECASE),
)
# Source/attribution meta-question patterns
# Catches: "where did you get this information", "which videos", "from what video",
# "what source", "where is that from"
_SOURCE_META_RE = re.compile(
    r"\b(?:"
    r"where (?:did|do) (?:you|u|ya) (?:get|find|pull|take|source)"
    r"|which (?:video|videos|source|sources|content|episode|clip)"
    r"|from (?:what|which) (?:video|source|content|episode)"
    r"|what (?:video|source|content) (?:is|was|did) (?:that|this|it)"
    r"|where (?:is|was) (?:that|this|it) from"
    r"|what(?:'s| is) (?:the|your) source"
    r"|i me(?:an|n) which video"
    r"|sorry.*which video"
    r")\b",
    re.IGNORECASE,
)
CLARIFICATION_TITLE_PATTERNS = (
    re.compile(r'(?i)\bwhich one[,:-]?\s*[\"“]?([^?\"\n]+?)[\"”]?\??\s*$'),
    re.compile(r'(?i)\bdo you mean[,:-]?\s*[\"“]?([^?\"\n]+?)[\"”]?\??\s*$'),
    re.compile(r'(?i)\bare you asking about[,:-]?\s*[\"“]?([^?\"\n]+?)[\"”]?\??\s*$'),
)

RECENT_BOOK_TITLE_PATTERNS = (
    re.compile(r'(?i)\bbook (?:called|titled)\s*[\"â€œ]?([^\"\n\.\!\?]+?)[\"â€]?(?:[\.\!\?]|$)'),
    re.compile(r'(?i)\bcalled\s*[\"â€œ]?([^\"\n\.\!\?]+?)[\"â€]?(?:[\.\!\?]|$)'),
    re.compile(r'(?i)\btitled\s*[\"â€œ]?([^\"\n\.\!\?]+?)[\"â€]?(?:[\.\!\?]|$)'),
)

RECENT_BOOK_TITLE_PATTERNS_EXTRA = (
    re.compile(r'(?i)(?:^|[\.!\?]\s+)([$A-Za-z0-9][^"\n\.\!\?]{2,100}?)\s+is\s+the\s+\w+\s+book\b'),
    re.compile(r'(?i)(?:^|[\.!\?]\s+)([$A-Za-z0-9][^"\n\.\!\?]{2,100}?)\s+is\s+my\s+\w+\s+book\b'),
)

FOCUSLESS_WHEN_START_RE = re.compile(
    r"^\s*(?:so\s+)?when\s+did\s+(?:you|u|he|she|they)\s+(?:start|begin|began|get\s+into|got\s+into)\s*\??\s*$",
    re.IGNORECASE,
)
FOCUSLESS_WHY_START_RE = re.compile(
    r"^\s*(?:so\s+)?why\s+did\s+(?:you|u|he|she|they)\s+(?:start|begin|began|get\s+into|got\s+into)\s*\??\s*$",
    re.IGNORECASE,
)
CONTEXTUAL_TURNAROUND_FOLLOWUP_RE = re.compile(
    r"\b(?:what|why|how)\s+(?:(?:made|led|pushed|forced|motivated|inspired|did)\s+)?(?:you|u|he|she|they|him|her|them)?\s*(?:to\s+)?turn\s+(?:it|that|this|things)\s+around\b",
    re.IGNORECASE,
)
ASSISTANT_TURNAROUND_CONTEXT_RE = re.compile(
    r"\b(?:turn(?:ed|ing)?\s+(?:my|your|his|her|their|a)?\s*(?:life|path|career|future)?\s*around|journey|background|story|dark\s+place|rock\s+bottom|legal\s+system|convict|stolen\s+cars|trauma|rebuilt|changed\s+(?:my|his|her|their)\s+life)\b",
    re.IGNORECASE,
)
SUBJECT_SWAP_RE = re.compile(r"^\s*(?:so\s+)?(?:what|how)\s+about\s+(.+?)\s*\??\s*$", re.IGNORECASE)
FOLLOWUP_FOCUS_FILLER_TERMS = {"man", "bro", "brother", "dude", "yo", "lol", "pls", "please"}
ASSISTANT_ANCHOR_FOLLOWUP_RE = re.compile(
    r"\b(?:latest|newest|recent|last|that|this|it|one|thing|project|build|wreck|car|video|clip|episode|podcast|post|link|links|source|resource|"
    r"you\s+(?:were\s+)?(?:talking|mentioned|said)|talking\s+about)\b",
    re.IGNORECASE,
)
ASSISTANT_ANCHOR_EXTRACTORS = (
    re.compile(r"\b((?:the\s+)?(?:latest|newest|recent|last)\s+[^.!?\n]{2,120})", re.IGNORECASE),
    re.compile(r"\b((?:we|i)\s+(?:just|recently)\s+[^.!?\n]{3,120})", re.IGNORECASE),
    re.compile(
        r"\b(?:you\s+should\s+see|check\s+out|watch|look\s+at|see)\s+(?:the\s+state\s+of\s+)?([^.!?\n]{3,140})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:including\s+)?(?:an?\s+|the\s+)?(?:video|podcast|episode|post|reel|clip|resource)\s+(?:called|titled|named)\s+[\"“]?([^.!?\n\"”]{3,160})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:including\s+)?(?:an?\s+|the\s+)?(?:episode|podcast|video|post|reel|clip|resource)\s+of\s+([^.!?\n]{3,160})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bon\s+(?:an?\s+|the\s+)?(?:episode\s+of\s+)?([^.!?\n]{3,160}?(?:podcast|show|channel))\b",
        re.IGNORECASE,
    ),
)
ASSISTANT_ANCHOR_CUT_RE = re.compile(
    r"\b(?:if\s+you\s+(?:want|wanna|would\s+like)|though|because|but|so|below|for\s+yourself|"
    r"which\s+is|that\s+is|where\s+i|where\s+we|is\s+the\s+one|is\s+one)\b",
    re.IGNORECASE,
)
ASSISTANT_ANCHOR_SIGNAL_RE = re.compile(
    r"\b(?:latest|newest|recent|last|just|pulled|bought|built|rebuilt|posted|uploaded|shared|attached|recommended|mentioned|video|podcast|episode|resource|source|link|watch|listen)\b",
    re.IGNORECASE,
)
ASSISTANT_RESOURCE_FOLLOWUP_RE = re.compile(
    r"\b(?:link|links|url|source|sources|send|show|watch|listen|read|video|episode|podcast|resource|"
    r"break\s*down|breakdown|summary|summari[sz]e|recap|takeaways?|where\s+can\s+i)\b",
    re.IGNORECASE,
)
ASSISTANT_RESOURCE_SENTENCE_RE = re.compile(
    r"\b(?:video|podcast|episode|resource|post|reel|clip|source|link|watch|listen)\b",
    re.IGNORECASE,
)
ASSISTANT_ANCHOR_STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "if", "you", "u", "your", "ur", "i",
    "we", "my", "our", "it", "that", "this", "one", "thing", "what", "was", "is",
    "are", "were", "did", "do", "does", "about", "tell", "me", "more", "latest",
    "newest", "recent", "last", "just", "into", "with", "for", "from", "there",
}
RESOURCE_BREAKDOWN_FOLLOWUP_RE = re.compile(
    r"\b(?:deep|full|detailed|proper|complete)?\s*(?:break\s*down|breakdown|summary|summari[sz]e|recap|"
    r"walk\s+(?:me\s+)?through|go\s+through|takeaways?|main\s+points|key\s+points|lessons?)\b"
    r"|\b(?:don'?t|dont|do\s+not|can't|cant)\s+(?:wanna|want\s+to|have\s+time\s+to)?\s*"
    r"(?:watch|listen|read)\b",
    re.IGNORECASE,
)


class DecisionService:
    """
    Implements Creator-Style Decision Making.
    Decides 'WHAT' conversational move to make before 'HOW' to say it.
    """

    DEFAULT_POLICY = {
        "privacy_boundary": {
            "general": "public_ok",
            "relationship": "private",
            "family": "private",
            "age": "public_ok",
            "location": "private",
            "income_networth": "deflect",
            "politics_religion": "avoid"
        },
        "conversation_moves": {
            "ask_followup_rate": 0.5,
            "end_cleanly_rate": 0.3,
            "one_liner_rate": 0.2
        },
        "evidence_requirement": {
            "bio_facts": "high",
            "opinions": "low",
            "advice": "medium"
        }
    }

    def get_policy(self, creator_row: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch policy from row or return default."""
        policy = creator_row.get("decision_policy")
        if not policy:
            return self.DEFAULT_POLICY
        if isinstance(policy, str):
            try:
                return json.loads(policy)
            except:
                return self.DEFAULT_POLICY
        return policy

    def score_context_sufficiency(self, question: str, history: Optional[List[Dict[str, str]]] = None) -> int:
        """
        Computes context_score (0-3):
        +1 question_present
        +1 problem_statement_present
        +1 specificity_present
        """
        q = question.lower().strip()
        score = 0
        
        # 1. Question present
        if "?" in q or any(q.startswith(w) for w in ["how", "what", "can", "why", "who", "where", "when"]):
            score += 1
            
        # 2. Problem Statement (heuristic: long enough and contains action/subject)
        words = q.split()
        if len(words) > 8: # Arbitrary threshold for "statement"
            score += 1
            
        # 3. Specificity (heuristic: contains domain-specific nouns/keywords)
        domain_keywords = ["business", "train", "workout", "money", "client", "sales", "diet", "macro", "trade", "market", "content", "strategy"]
        if any(w in q for w in domain_keywords) or len(words) > 15:
            score += 1
            
        return score

    def _normalized_followup(self, question: str) -> str:
        return re.sub(r"[^a-z0-9\s']", "", str(question or "").lower()).strip()

    def _words(self, question: str) -> List[str]:
        return re.findall(r"[a-z0-9']+", str(question or "").lower())

    def is_user_relationship_business_question(self, question: str) -> bool:
        lowered = self._normalized_followup(question)
        words = set(self._words(question))
        has_relationship = bool(words & RELATIONSHIP_TERMS)
        has_business = bool(words & BUSINESS_CONTEXT_TERMS)
        if not (has_relationship and has_business):
            return False

        user_centered = bool(words & USER_SELF_TERMS) or "if i have" in lowered or "my business" in lowered or "our business" in lowered
        if not user_centered:
            return False

        return any(pattern.search(lowered) for pattern in ADVICE_REQUEST_PATTERNS) or "?" in str(question or "")

    # Terms that signal the question is about business growth / strategy,
    # not purely personal life.  When these dominate, don't divert to the
    # personal-bio path even if a relationship/family word co-occurs.
    _STRONG_BUSINESS_ACTION_TERMS = {
        "grow", "grew", "growing", "growth", "scale", "scaled", "scaling",
        "acquisition", "acquire", "acquired", "launch", "launched",
        "build", "built", "building", "start", "started", "starting",
        "revenue", "profit", "sales", "monetize", "hire", "hiring",
        "company", "business", "brand", "agency", "fund", "funding",
    }

    def _is_public_wealth_journey_question(self, question: str) -> bool:
        lowered = self._normalized_followup(question)
        patterns = (
            r"\bhow\s+did\s+(?:you|u|he|she|they)\s+(?:get|become)\s+(?:rich|wealthy|successful)\b",
            r"\bhow\s+did\s+(?:you|u|he|she|they)\s+(?:make|build)\s+(?:your|ur|his|her|their)?\s*(?:money|wealth|fortune)\b",
            r"\bwhat\s+made\s+(?:you|u|him|her|them)\s+(?:rich|wealthy|successful)\b",
        )
        return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns)

    def is_creator_personal_fact_question(self, question: str) -> bool:
        lowered = self._normalized_followup(question)
        words = set(self._words(question))
        if self.is_user_relationship_business_question(question):
            return False
        if self._is_public_wealth_journey_question(question):
            return False

        sensitive_terms = RELATIONSHIP_TERMS | FAMILY_TERMS | POLITICS_RELIGION_TERMS | {
            "age", "birthday", "born", "birth", "address", "house", "city",
            "state", "resident", "located", "live", "net", "worth", "salary",
            "income", "rich", "earn",
        }
        if not (words & sensitive_terms):
            return False

        # If the question carries strong business-action language, the sensitive
        # term is likely incidental context ("...while being in a relationship")
        # rather than the core question.  Let normal RAG handle it.
        business_hits = words & self._STRONG_BUSINESS_ACTION_TERMS
        sensitive_hits = words & sensitive_terms
        if business_hits and len(business_hits) >= len(sensitive_hits):
            return False

        if words & CREATOR_REF_TERMS:
            return True

        creator_question_starts = (
            "are you", "do you", "did you", "how old are you", "where are you",
            "who are you", "tell me about yourself", "what do you believe",
            "what are your beliefs", "your family", "your background",
        )
        return any(lowered.startswith(prefix) or prefix in lowered for prefix in creator_question_starts)

    def _looks_like_clarification_followup(self, question: str) -> bool:
        normalized = self._normalized_followup(question)
        compact = normalized.replace(" ", "")
        return normalized in CLARIFICATION_FOLLOWUPS or compact in CLARIFICATION_FOLLOWUPS

    def _rewrite_clarification_followup(self, history: Optional[List[Dict[str, str]]] = None) -> str:
        if not history:
            return ""
        last_assistant_index = None
        for idx in range(len(history) - 1, -1, -1):
            if (history[idx].get("role") or "").lower() == "assistant":
                last_assistant_index = idx
                break
        if last_assistant_index is None:
            return ""

        previous_user = ""
        for idx in range(last_assistant_index - 1, -1, -1):
            if (history[idx].get("role") or "").lower() == "user":
                previous_user = (history[idx].get("content") or history[idx].get("text") or "").strip()
                break

        if previous_user:
            cleaned = re.sub(r"\s+", " ", previous_user).strip(" .!?")
            return f"Can you clarify what you meant in your last answer about: {cleaned}?"
        return "Can you clarify what you meant in your last answer?"

    def _normalize_followup_focus(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip(" ?!.").lower()
        if not normalized:
            return ""

        patterns = (
            re.compile(r"(?:start|started|begin|began|get into|got into)\s+(.+)$", re.IGNORECASE),
            re.compile(r"(?:what|how)\s+about\s+(.+)$", re.IGNORECASE),
        )
        focus = ""
        for pattern in patterns:
            match = pattern.search(normalized)
            if match:
                focus = re.sub(r"\s+", " ", match.group(1)).strip(" \"'")
                break

        if not focus:
            for candidate in (
                "day trading",
                "trading",
                "investing",
                "youtube",
                "dropshipping",
                "business",
                "podcast",
                "content creation",
            ):
                if candidate in normalized:
                    focus = candidate
                    break

        if not focus:
            return ""

        focus_words = [
            word
            for word in re.findall(r"[a-z0-9']+", focus)
            if word not in FOLLOWUP_FOCUS_FILLER_TERMS
        ]
        if not focus_words:
            return ""

        compact = " ".join(focus_words)
        alias_map = (
            (r"\b(?:day\s*)?trad(?:e|ing|er|ers)\b|\bswing\s*trad(?:e|ing)\b", "trading"),
            (r"\binvest(?:ing|ment|ments)?\b", "investing"),
            (r"\b(?:youtube|content\s+creation|creating\s+content|making\s+content|videos?)\b", "content creation"),
            (r"\b(?:podcast|podcasting)\b", "podcasting"),
            (r"\b(?:dropship(?:ping)?|e-?commerce|online\s+store)\b", "ecommerce"),
            (r"\b(?:business|entrepreneur(?:ship)?)\b", "business"),
        )
        for pattern, canonical in alias_map:
            if re.search(pattern, compact):
                return canonical
        return compact

    def _latest_user_message(self, history: Optional[List[Dict[str, str]]]) -> str:
        for msg in reversed(list(history or [])):
            if (msg.get("role") or "").lower() != "user":
                continue
            text = (msg.get("content") or msg.get("text") or "").strip()
            if text:
                return text
        return ""

    def _latest_assistant_message(self, history: Optional[List[Dict[str, str]]]) -> str:
        for msg in reversed(list(history or [])):
            if (msg.get("role") or "").lower() != "assistant":
                continue
            text = (msg.get("content") or msg.get("text") or "").strip()
            if text:
                return text
        return ""

    def _anchor_terms(self, text: str) -> set:
        return {
            word
            for word in re.findall(r"[a-z0-9']+", str(text or "").lower())
            if len(word) > 2 and word not in ASSISTANT_ANCHOR_STOP_WORDS
        }

    def _clean_assistant_anchor(self, value: str) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip(" \"'.,:;!?")
        if not text:
            return ""
        text = ASSISTANT_ANCHOR_CUT_RE.split(text, maxsplit=1)[0]
        text = re.sub(r"\s+", " ", text).strip(" \"'.,:;!?")
        if len(text.split()) < 2:
            return ""
        return text[:140].rsplit(" ", 1)[0].strip() if len(text) > 140 else text

    def _extract_assistant_anchor(self, assistant_text: str, question: str) -> str:
        text = re.sub(r"\s+", " ", str(assistant_text or "")).strip()
        if not text:
            return ""

        question_terms = self._anchor_terms(question)
        pronoun_followup = bool(re.search(r"\b(?:it|that|this|one|thing)\b", str(question or "").lower()))
        resource_followup = bool(ASSISTANT_RESOURCE_FOLLOWUP_RE.search(str(question or "")))
        candidates: List[Tuple[int, str]] = []
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if not ASSISTANT_ANCHOR_SIGNAL_RE.search(sentence):
                continue
            for pattern in ASSISTANT_ANCHOR_EXTRACTORS:
                for match in pattern.finditer(sentence):
                    raw = match.group(1) if match.groups() else match.group(0)
                    candidate = self._clean_assistant_anchor(raw)
                    if not candidate:
                        continue
                    candidate_terms = self._anchor_terms(candidate)
                    overlap = len(question_terms & candidate_terms)
                    if not overlap and not pronoun_followup:
                        if not (resource_followup and ASSISTANT_RESOURCE_SENTENCE_RE.search(sentence)):
                            continue
                    score = overlap * 4
                    if re.search(r"\b(?:latest|newest|recent|last)\b", candidate, re.IGNORECASE):
                        score += 3
                    if re.search(r"\bjust\b", candidate, re.IGNORECASE):
                        score += 1
                    if resource_followup and ASSISTANT_RESOURCE_SENTENCE_RE.search(sentence):
                        score += 2
                    candidates.append((score, candidate))

        if not candidates:
            return ""

        candidates.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
        return candidates[0][1]

    def _looks_like_assistant_anchor_followup(self, question: str) -> bool:
        lowered = str(question or "").lower()
        words = self._words(question)
        if not words:
            return False
        if len(words) > 14 and not re.search(r"\byou\s+(?:were\s+)?(?:talking|mentioned|said)\b", lowered):
            return False
        return bool(ASSISTANT_ANCHOR_FOLLOWUP_RE.search(lowered))

    def _looks_like_resource_breakdown_followup(self, question: str) -> bool:
        lowered = re.sub(r"\s+", " ", str(question or "").lower()).strip()
        if not lowered:
            return False
        return bool(RESOURCE_BREAKDOWN_FOLLOWUP_RE.search(lowered))

    def _rewrite_resource_breakdown_followup(
        self,
        question: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        if not history or not self._looks_like_resource_breakdown_followup(question):
            return ""
        title = self._extract_recent_card_title(history)
        if not title:
            last_assistant = self._latest_assistant_message(history)
            title = self._extract_assistant_anchor(last_assistant, question)
        clean_title = re.sub(r"\s+", " ", (title or "")).strip(" \"'.,:;!?")
        if not clean_title:
            return ""

        lower = (question or "").lower()
        if re.search(r"\b(?:watch|video|youtube|clip|short|reel)\b", lower):
            medium = "video"
        elif re.search(r"\b(?:listen|podcast|episode)\b", lower):
            medium = "episode"
        elif re.search(r"\b(?:read|post|article|newsletter)\b", lower):
            medium = "resource"
        else:
            medium = "resource"
        return f"Give me a detailed breakdown of your {medium} \"{clean_title}\"."

    def _assistant_anchor_to_user_phrase(self, anchor: str) -> str:
        phrase = re.sub(r"\s+", " ", str(anchor or "")).strip(" \"'.,:;!?")
        replacements = (
            (r"\bwe\s+just\b", "you just"),
            (r"\bi\s+just\b", "you just"),
            (r"\bwe\b", "you"),
            (r"\bi\b", "you"),
            (r"\bmy\b", "your"),
            (r"\bour\b", "your"),
            (r"\bus\b", "you"),
        )
        for pattern, replacement in replacements:
            phrase = re.sub(pattern, replacement, phrase, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", phrase).strip()

    def _anchor_with_article(self, phrase: str) -> str:
        clean = re.sub(r"\s+", " ", str(phrase or "")).strip(" \"'.,:;!?")
        if not clean:
            return ""
        if clean.startswith("The "):
            clean = "the " + clean[4:]
        if re.match(r"^(?:the|a|an|your|you)\b", clean, re.IGNORECASE):
            return clean
        if re.match(r"^(?:latest|newest|recent|last)\b", clean, re.IGNORECASE):
            return f"the {clean}"
        return clean

    def _rewrite_assistant_anchor_followup(
        self,
        question: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        if not history or not self._looks_like_assistant_anchor_followup(question):
            return ""
        last_assistant = self._latest_assistant_message(history)
        anchor = self._extract_assistant_anchor(last_assistant, question)
        if not anchor:
            return ""

        raw_phrase = re.sub(r"\s+", " ", self._assistant_anchor_to_user_phrase(anchor)).strip(" \"'.,:;!?")
        phrase = self._anchor_with_article(raw_phrase)
        lowered = self._normalized_followup(question)
        if not phrase and not raw_phrase:
            return ""
        if re.search(r"\b(?:link|links|url|source|sources|send|show|watch|video|episode|podcast|resource)\b", lowered):
            return f"Find the link for the resource \"{raw_phrase or phrase}\" mentioned in the previous answer."
        if not phrase:
            return ""
        if lowered.startswith(("what was", "whats", "what is", "what's")):
            return f"What was {phrase}?"
        return f"Tell me more about {phrase}."

    def _extract_recent_followup_focus(self, history: Optional[List[Dict[str, str]]]) -> str:
        for msg in reversed(list(history or [])[-8:]):
            if (msg.get("role") or "").lower() != "user":
                continue
            text = (msg.get("content") or msg.get("text") or "").strip()
            focus = self._normalize_followup_focus(text)
            if focus:
                return focus
        return ""

    def _rewrite_focusless_creator_followup(self, question: str, history: Optional[List[Dict[str, str]]]) -> str:
        if not history:
            return ""
        focus = self._extract_recent_followup_focus(history)
        if not focus:
            return ""
        if FOCUSLESS_WHEN_START_RE.match(question or ""):
            return f"When did you start {focus}?"
        if FOCUSLESS_WHY_START_RE.match(question or ""):
            return f"Why did you start {focus}?"
        return ""

    def _rewrite_subject_swap_followup(self, question: str, history: Optional[List[Dict[str, str]]]) -> str:
        if not history:
            return ""
        match = SUBJECT_SWAP_RE.match(question or "")
        if not match:
            return ""
        focus = self._normalize_followup_focus(match.group(1))
        if not focus:
            return ""

        previous_user = self._latest_user_message(history)
        if not previous_user:
            return ""
        if FOCUSLESS_WHEN_START_RE.match(previous_user):
            return f"When did you start {focus}?"
        if FOCUSLESS_WHY_START_RE.match(previous_user):
            return f"Why did you start {focus}?"
        return ""

    def _rewrite_turnaround_followup(self, question: str, history: Optional[List[Dict[str, str]]]) -> str:
        if not history or not CONTEXTUAL_TURNAROUND_FOLLOWUP_RE.search(question or ""):
            return ""
        last_assistant = self._latest_assistant_message(history)
        if not ASSISTANT_TURNAROUND_CONTEXT_RE.search(last_assistant):
            return ""
        lowered = self._normalized_followup(question)
        if lowered.startswith("how"):
            return "How did you turn your life around?"
        if lowered.startswith("why"):
            return "Why did you turn your life around?"
        return "What made you turn your life around?"

    def resolve_followup_question(self, question: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        q = (question or "").strip()
        if not q or not history:
            return question

        normalized = re.sub(r"\s+", " ", q.lower()).strip(" .!?")
        if self._looks_like_clarification_followup(q):
            rewritten = self._rewrite_clarification_followup(history)
            if rewritten:
                return rewritten
        # ── source / attribution meta-question ──
        if self._looks_like_source_meta_question(q):
            rewritten = self._rewrite_source_meta_question(q, history)
            if rewritten:
                return rewritten
        rewritten = self._rewrite_resource_breakdown_followup(q, history)
        if rewritten:
            return rewritten
        # ── content/video reference follow-up ──
        if self._looks_like_content_reference(q):
            title = self._extract_recent_card_title(history)
            if title:
                return self._rewrite_content_followup(q, title)
        if self._looks_like_book_followup(q):
            title = self._extract_recent_book_title(history)
            if title:
                return self._rewrite_book_followup(q, title)
        rewritten = self._rewrite_turnaround_followup(q, history)
        if rewritten:
            return rewritten
        rewritten = self._rewrite_assistant_anchor_followup(q, history)
        if rewritten:
            return rewritten
        rewritten = self._rewrite_focusless_creator_followup(q, history)
        if rewritten:
            return rewritten
        rewritten = self._rewrite_subject_swap_followup(q, history)
        if rewritten:
            return rewritten
        if normalized not in AFFIRMATIVE_FOLLOWUPS:
            return question

        last_assistant_index = None
        for idx in range(len(history) - 1, -1, -1):
            if (history[idx].get("role") or "").lower() == "assistant":
                last_assistant_index = idx
                break
        if last_assistant_index is None:
            return question

        last_assistant = (history[last_assistant_index].get("content") or history[last_assistant_index].get("text") or "").strip()
        previous_user = ""
        for idx in range(last_assistant_index - 1, -1, -1):
            if (history[idx].get("role") or "").lower() == "user":
                previous_user = (history[idx].get("content") or history[idx].get("text") or "").strip()
                break
        if not previous_user:
            return question

        title = self._extract_clarified_title(last_assistant)
        if not title:
            return question

        previous_lower = previous_user.lower()
        if not any(term in previous_lower for term in BOOK_CONTEXT_TERMS):
            return question

        return self._rewrite_book_followup(previous_user, title)

    def _extract_clarified_title(self, assistant_text: str) -> str:
        text = (assistant_text or "").strip()
        if not text:
            return ""
        for pattern in CLARIFICATION_TITLE_PATTERNS:
            match = pattern.search(text)
            if match:
                candidate = re.sub(r"\s+", " ", match.group(1)).strip(" \"“”'.,:;!?")
                if 1 <= len(candidate) <= 160:
                    return candidate
        return ""

    def _rewrite_book_followup(self, previous_user: str, title: str) -> str:
        lower = (previous_user or "").lower()
        clean_title = re.sub(r"\s+", " ", (title or "")).strip()
        if any(token in lower for token in ["launch", "launched", "release", "released", "come out", "release date"]):
            return f"When was {clean_title} launched?"
        if any(token in lower for token in ["publish", "published", "publication"]):
            return f"When was {clean_title} published?"
        if any(token in lower for token in ["write", "wrote", "writing", "written"]):
            return f"When did you write {clean_title}?"
        return f"When was {clean_title} published?"

    # ── content / video reference follow-up helpers ──

    _CONTENT_REF_PATTERN = re.compile(
        r"\b(?:that|this|the)\s+(?:video|episode|clip|reel|podcast|content|one)\b",
        re.IGNORECASE,
    )

    def _looks_like_content_reference(self, question: str) -> bool:
        """Detect questions like 'what did u talk about in that video?'"""
        lowered = re.sub(r"\s+", " ", str(question or "").lower()).strip()
        if not lowered:
            return False
        return bool(self._CONTENT_REF_PATTERN.search(lowered))

    def _extract_recent_card_title(self, history: Optional[List[Dict[str, str]]]) -> str:
        """Pull the most recent resource title from assistant metadata."""
        for msg in reversed(list(history or [])[-6:]):
            if (msg.get("role") or "").lower() != "assistant":
                continue
            for bucket in ("cards", "citations"):
                resources = msg.get(bucket) or []
                if isinstance(resources, list):
                    for resource in resources:
                        if not isinstance(resource, dict):
                            continue
                        title = (resource.get("title") or "").strip()
                        if title:
                            return title
        return ""

    def _rewrite_content_followup(self, question: str, title: str) -> str:
        """Rewrite a content-reference follow-up with the specific title."""
        lower = (question or "").lower()
        clean_title = re.sub(r"\s+", " ", (title or "")).strip()
        if any(w in lower for w in ("talk about", "cover", "discuss", "say in", "said in", "about")):
            return f"What did you talk about in your video \"{clean_title}\"?"
        if any(w in lower for w in ("how long", "length", "duration")):
            return f"How long is your video \"{clean_title}\"?"
        if any(w in lower for w in ("when", "date", "upload", "post")):
            return f"When did you upload \"{clean_title}\"?"
        return f"Tell me more about your video \"{clean_title}\""

    # ── source / attribution meta-question helpers ──

    def _looks_like_source_meta_question(self, question: str) -> bool:
        """Detect 'where did you get this info / which videos' type questions."""
        lowered = re.sub(r"\s+", " ", str(question or "").lower()).strip()
        if not lowered:
            return False
        return bool(_SOURCE_META_RE.search(lowered))

    def _extract_cited_sources_from_history(self, history: Optional[List[Dict[str, str]]]) -> List[Dict[str, str]]:
        """Extract all video/source titles and URLs mentioned in recent assistant messages."""
        sources: List[Dict[str, str]] = []
        seen_titles: set = set()
        for msg in reversed(list(history or [])[-10:]):
            if (msg.get("role") or "").lower() != "assistant":
                continue
            # Check card metadata
            cards = msg.get("cards") or []
            if isinstance(cards, list):
                for card in cards:
                    if isinstance(card, dict):
                        title = (card.get("title") or "").strip()
                        url = (card.get("url") or "").strip()
                        if title and title.lower() not in seen_titles:
                            seen_titles.add(title.lower())
                            sources.append({"title": title, "url": url})
            # Check citations metadata
            citations = msg.get("citations") or []
            if isinstance(citations, list):
                for cit in citations:
                    if isinstance(cit, dict):
                        title = (cit.get("title") or "").strip()
                        url = (cit.get("url") or "").strip()
                        if title and title.lower() not in seen_titles:
                            seen_titles.add(title.lower())
                            sources.append({"title": title, "url": url})
        return sources

    def _identify_topic_from_history(self, history: Optional[List[Dict[str, str]]]) -> str:
        """Identify the recent discussion topic from user messages."""
        for msg in reversed(list(history or [])[-6:]):
            if (msg.get("role") or "").lower() != "user":
                continue
            text = (msg.get("content") or msg.get("text") or "").strip()
            # Skip short meta-questions (the source question itself)
            if text and len(text.split()) > 3 and not _SOURCE_META_RE.search(text.lower()):
                return re.sub(r"\s+", " ", text).strip()[:200]
        return ""

    def _rewrite_source_meta_question(self, question: str, history: Optional[List[Dict[str, str]]]) -> str:
        """Rewrite a source meta-question to fetch the cited content with context."""
        sources = self._extract_cited_sources_from_history(history)
        topic = self._identify_topic_from_history(history)

        if sources:
            titles = ", ".join(f'"{s["title"]}"' for s in sources[:5])
            return (
                f"The user is asking which sources/videos the previous information came from. "
                f"The following sources were cited in this conversation: {titles}. "
                f"Tell the user which of these sources the information came from, "
                f"and briefly explain what each source covers related to the topic."
            )
        if topic:
            return (
                f"The user wants to know which of your videos or content covers "
                f"this topic: \"{topic}\". Search your content for videos about this topic "
                f"and share the relevant ones with links."
            )
        return ""

    def _looks_like_book_followup(self, question: str) -> bool:
        lowered = re.sub(r"\s+", " ", str(question or "").lower()).strip(" .!?")
        if not lowered:
            return False
        words = re.findall(r"[a-z0-9']+", lowered)
        if len(words) > 10:
            return False
        referential = {"it", "that", "this", "one", "book"}
        timing = {"when", "publish", "published", "publication", "release", "released", "launch", "launched", "write", "wrote", "written"}
        return bool(set(words) & referential) and bool(set(words) & timing)

    def _extract_recent_book_title(self, history: Optional[List[Dict[str, str]]]) -> str:
        for message in reversed(list(history or [])[-6:]):
            text = (message.get("content") or message.get("text") or "").strip()
            if not text:
                continue
            clarified = self._extract_clarified_title(text)
            if clarified:
                return clarified
            for pattern in RECENT_BOOK_TITLE_PATTERNS + RECENT_BOOK_TITLE_PATTERNS_EXTRA:
                match = pattern.search(text)
                if not match:
                    continue
                candidate = re.sub(r"\s+", " ", match.group(1)).strip(" \"â€œâ€'.,:;!?")
                if 1 <= len(candidate) <= 160:
                    return candidate
        return ""

    def classify_question(self, question: str, intent: str, history: Optional[List[Dict[str, str]]] = None) -> Tuple[str, str, int]:
        """
        Classifies message into behavioral types, topics, and sufficiency.
        Returns (type, topic, sufficiency_score)
        """
        q = question.lower().strip()
        sufficiency = self.score_context_sufficiency(question, history)
        
        # 1. Topic Identification
        topic = "general"
        words_set = set(self._words(question))
        _has_strong_biz = bool(words_set & self._STRONG_BUSINESS_ACTION_TERMS)
        if self.is_user_relationship_business_question(question):
            topic = "general"
        elif words_set & RELATIONSHIP_TERMS:
            # If the user also mentions strong business terms, the personal word
            # is likely incidental context ("...while being in a relationship"),
            # so keep topic as general to avoid personal-bio diversion.
            topic = "general" if _has_strong_biz else "relationship"
        elif any(word in q for word in ["kids", "children", "son", "daughter", "parents", "family", "mom", "dad", "siblings"]):
            topic = "family"
        elif "old are you" in q or bool(words_set & {"age", "birthday", "born", "birth"}):
            topic = "age"
        elif any(word in q for word in ["live", "address", "house", "where are you", "city", "state", "resident", "located"]):
            topic = "location"
        elif self._is_public_wealth_journey_question(question):
            topic = "general"
        elif any(word in q for word in ["money", "net worth", "make", "income", "salary", "rich", "worth", "revenue", "cents", "dollars", "earn"]):
            topic = "income_networth"
        elif any(word in q for word in POLITICS_RELIGION_TERMS):
            topic = "politics_religion"
        elif any(word in q for word in ["book", "published", "publication", "write your book", "wrote your book", "your company", "your business", "your career", "your background", "your story"]):
            topic = "general"
            
        # 2. Type Identification
        q_type = "domain_advice"
        words = q.split()
        
        # User Rule: Greeting Detection
        # < 5 words, no question mark, no specific topic
        if len(words) < 5 and "?" not in q and topic == "general":
            q_type = "greeting"
        elif intent in ["greeting", "greeting_only"]:
            q_type = "greeting"
        elif intent == "personal_bio_question" or self.is_creator_personal_fact_question(question):
            q_type = "personal_bio"
        
        # Override for high-sensitivity items
        if any(word in q for word in ["address", "phone", "email", "ssn", "secret", "password", "bank", "account", "credit card"]) or any(term in q for term in PRIVATE_BODY_SEX_TERMS):
            q_type = "private_sensitive"
        elif any(word in q for word in ["think about", "opinion", "feel about", "do you like", "best part of", "worst part of", "hot take"]):
            q_type = "opinion"
        elif re.search(r"\bare\s+you\s+real\b|\bai\b|\bbot\b|\blanguage\s+model\b|\bsoftware\b|\bcoded\b|\bsimulation\b", q, re.IGNORECASE):
            q_type = "meta"
            
        return q_type, topic, sufficiency

    def choose_move(
        self, 
        policy: Dict[str, Any], 
        question_type: str, 
        topic: str, 
        confidence: str = "LOW",
        intent: str = "how_to",
        sufficiency: int = 2
    ) -> str:
        """
        Decision Router: Selects the strategic conversational move.
        Keep this deterministic. Gemini decides the turn intent and the render
        layer can add creator-flavored pushback; this router must not randomly
        turn normal advice into a boundary or unrelated reframe.
        """
        # 0. Context Sufficiency / Greeting Check
        if question_type == "greeting":
            return "ASK_CLARIFY"
            
        if question_type == "domain_advice" and sufficiency < 2:
            return "ASK_CLARIFY"

        # A. Private/Sensitive always pushes back
        if question_type == "private_sensitive":
            return "BOUNDARY_PUSHBACK"
            
        # B. Meta questions (are you real?)
        if question_type == "meta":
            return "BOUNDARY_PUSHBACK" # Refuse meta-commentary

        # C. Personal Bio Questions
        if question_type == "personal_bio" or intent == "personal_bio_question":
            default_boundary = "public_ok" if topic == "general" else "private"
            boundary = policy.get("privacy_boundary", {}).get(topic, default_boundary)
            
            if boundary in {"private", "avoid"}:
                return "DECLINE_PRIVATE"
            
            if boundary == "deflect":
                return "DEFLECT_WITH_HUMOR"
                
            if confidence == "HIGH":
                return "ANSWER_DIRECTLY"
            
            if confidence == "MEDIUM":
                return "ANSWER_WITH_QUALIFIER"
            
            if topic == "general" and sufficiency >= 1:
                return "ANSWER_WITH_QUALIFIER"

            return "DECLINE_PRIVATE"

        # D. Domain Advice / Knowledge (how to, strategy, etc.)
        return "ANSWER_DIRECTLY"

decision_service = DecisionService()
