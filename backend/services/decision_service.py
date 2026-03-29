
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
}

FAMILY_TERMS = {
    "kids", "children", "son", "daughter", "parents", "family",
    "mom", "dad", "siblings",
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
        "answer_style": {
            "direct_answer_first": 0.8,
            "teach_mode": 0.5,
            "reframe_to_domain": 0.5,
            "pushback_rate": 0.1,
            "humor_deflect_rate": 0.1
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

    def is_creator_personal_fact_question(self, question: str) -> bool:
        lowered = self._normalized_followup(question)
        words = set(self._words(question))
        if self.is_user_relationship_business_question(question):
            return False

        sensitive_terms = RELATIONSHIP_TERMS | FAMILY_TERMS | {
            "age", "birthday", "born", "birth", "address", "house", "city",
            "state", "resident", "located", "live", "religion", "religious",
            "god", "belief", "beliefs", "worldview", "net", "worth", "salary",
            "income", "rich", "earn",
        }
        if not (words & sensitive_terms):
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

    def resolve_followup_question(self, question: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        q = (question or "").strip()
        if not q or not history:
            return question

        normalized = re.sub(r"\s+", " ", q.lower()).strip(" .!?")
        if self._looks_like_clarification_followup(q):
            rewritten = self._rewrite_clarification_followup(history)
            if rewritten:
                return rewritten
        if self._looks_like_book_followup(q):
            title = self._extract_recent_book_title(history)
            if title:
                return self._rewrite_book_followup(q, title)
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
        if self.is_user_relationship_business_question(question):
            topic = "general"
        elif any(word in q for word in ["wife", "husband", "married", "dating", "girlfriend", "boyfriend", "relationship", "partner"]):
            topic = "relationship"
        elif any(word in q for word in ["kids", "children", "son", "daughter", "parents", "family", "mom", "dad", "siblings"]):
            topic = "family"
        elif any(word in q for word in ["old are you", "age", "birthday", "born", "birth"]):
            topic = "age"
        elif any(word in q for word in ["live", "address", "house", "where are you", "city", "state", "resident", "located"]):
            topic = "location"
        elif any(word in q for word in ["money", "net worth", "make", "income", "salary", "rich", "worth", "revenue", "cents", "dollars", "earn"]):
            topic = "income_networth"
        elif any(word in q for word in ["politics", "religion", "religious", "god", "vote", "party", "republican", "democrat", "christian", "muslim", "jewish", "atheist", "agnostic", "nihilist", "pagan", "worldview", "belief", "beliefs"]):
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
        if any(word in q for word in ["address", "phone", "email", "ssn", "secret", "password", "bank", "account", "credit card"]):
            q_type = "private_sensitive"
        elif any(word in q for word in ["think about", "opinion", "feel about", "do you like", "best part of", "worst part of", "hot take"]):
            q_type = "opinion"
        elif any(word in q for word in ["are you real", "ai", "bot", "language model", "software", "coded", "simulation"]):
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
        Incorporates random probability based on policy rates.
        """
        import random
        
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
            if random.random() < policy.get("answer_style", {}).get("humor_deflect_rate", 0.1):
                return "DEFLECT_WITH_HUMOR"
            return "BOUNDARY_PUSHBACK" # Refuse meta-commentary

        # C. Personal Bio Questions
        if question_type == "personal_bio" or intent == "personal_bio_question":
            default_boundary = "public_ok" if topic == "general" else "private"
            boundary = policy.get("privacy_boundary", {}).get(topic, default_boundary)
            
            if boundary == "private":
                return "DECLINE_PRIVATE"
            
            if boundary == "deflect":
                return "DEFLECT_WITH_HUMOR"
                
            if confidence == "HIGH":
                # Check if we should answer directly vs reframe
                if random.random() < policy.get("answer_style", {}).get("direct_answer_first", 0.8):
                    return "ANSWER_DIRECTLY"
                
                if random.random() < policy.get("answer_style", {}).get("reframe_to_domain", 0.0):
                     return "REFRAME_TO_DOMAIN"
                
                return "ANSWER_DIRECTLY"
            
            if confidence == "MEDIUM":
                return "ANSWER_WITH_QUALIFIER"
            
            if topic == "general" and sufficiency >= 1:
                return "ANSWER_WITH_QUALIFIER"

            return "DECLINE_PRIVATE"

        # D. Domain Advice / Knowledge (how to, strategy, etc.)
        p_reframe = policy.get("answer_style", {}).get("reframe_to_domain", 0.5)
        p_teach = policy.get("answer_style", {}).get("teach_mode", 0.5)
        
        # Determine if we should pivot to a lesson
        if random.random() < max(p_reframe, p_teach):
            return "REFRAME_TO_DOMAIN"
            
        if random.random() < policy.get("answer_style", {}).get("pushback_rate", 0.1):
            return "BOUNDARY_PUSHBACK" # Push back on "bad" or "lazy" advice requests

        return "ANSWER_DIRECTLY"

decision_service = DecisionService()
