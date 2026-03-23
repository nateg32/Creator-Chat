
import logging
import json
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

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

    def classify_question(self, question: str, intent: str, history: Optional[List[Dict[str, str]]] = None) -> Tuple[str, str, int]:
        """
        Classifies message into behavioral types, topics, and sufficiency.
        Returns (type, topic, sufficiency_score)
        """
        q = question.lower().strip()
        sufficiency = self.score_context_sufficiency(question, history)
        
        # 1. Topic Identification
        topic = "general"
        if any(word in q for word in ["wife", "husband", "married", "dating", "girlfriend", "boyfriend", "relationship", "partner"]):
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
        elif intent == "personal_bio_question":
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
