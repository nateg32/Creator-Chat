
import logging
import json
import rag
from typing import Dict, Any, List, Optional
from settings import settings

logger = logging.getLogger(__name__)

class UserPriorityService:
    """
    Implements User Priority & Real Conversation Engine.
    Detects user state and selects response mode to ensure 
    comprehension and natural pacing.
    """

    MODES = [
        "GREETING_MODE", "DISCOVERY_MODE", "CURIOSITY_GATE", 
        "ONRAMP_MODE", "EXPLAIN_MODE", "RECOMMEND_MODE", 
        "COACH_MODE", "DISCUSSION_MODE", "DEEP_MODE"
    ]

    def calculate_mvc_score(self, user_state: Dict[str, Any], memory: Dict[str, Any]) -> int:
        """
        Computes the Minimal Viable Context (MVC) score based on memory state.
        """
        score = 0
        req_type = user_state.get("request_type", "discussion")
        
        # Mapping memory to MVC fields
        if memory.get("user_goal"): score += 1
        if memory.get("category_type"): score += 1
        if memory.get("starting_assets"): score += 1
        if memory.get("time_horizon"): score += 1
        if memory.get("constraints"): score += 1
        
        # Skill level is always high signal
        if memory.get("skill_level") and memory.get("skill_level") != "unknown":
            score += 1

        # Preference constraints for recommendations
        if memory.get("preference_constraint"): score += 1

        return score

    def detect_user_state(
        self, 
        question: str, 
        history: Optional[List[Dict[str, str]]] = None,
        current_memory: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Stage 1: Detect user skill, confusion, and clarity from history.
        Uses comprehensive classifiers.
        """
        from services.classifiers import classifiers
        from db import db
        
        # Resolve creator profile for context
        # In a real scenario, this would be passed in or fetched by creator_id
        # For parity with existing call sites, we fetch a default or handle errors
        try:
            creator_row = db.execute_one("SELECT name, handle FROM creators") or {}
            return classifiers.classify_all(question, history or [], creator_row)
        except Exception as e:
            logger.error(f"User state detection failed: {e}")
            return {
                "intent": question[:50],
                "skill_level": "unknown",
                "clarity_level": "clear",
                "confusion_level": "low",
                "emotional_tone": "neutral",
                "request_type": "casual"
            }

    def select_response_mode(self, user_state: Dict[str, Any], q_type: str, mvc_score: int) -> str:
        """
        Stage 2: Route to the appropriate conversational mode.
        """
        req_type = user_state.get("request_type", "casual")

        if q_type == "greeting":
            return "GREETING_MODE"
        
        # --- Curiosity Gate Logic ---
        # For pathway/roadmap requests, threshold is 2
        if req_type == "pathway" and mvc_score < 2:
            return "CURIOSITY_GATE"
            
        # For recommendation requests, threshold is 1
        if req_type == "recommendation" and mvc_score < 1:
            return "CURIOSITY_GATE"

        if user_state["clarity_level"] == "unclear":
            return "DISCOVERY_MODE"
            
        if user_state["skill_level"] == "beginner" or user_state["confusion_level"] != "low":
            return "ONRAMP_MODE"
            
        if user_state["request_type"] == "explanation":
            return "EXPLAIN_MODE"
            
        if user_state["request_type"] == "recommendation":
            return "RECOMMEND_MODE"
            
        if user_state["request_type"] == "pathway":
            return "COACH_MODE"
            
        if user_state["skill_level"] == "advanced":
            return "DEEP_MODE"
            
        return "DISCUSSION_MODE"

    def get_mode_constraints(self, mode: str, user_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Stage 3: Extract mandatory constraints and complexity controls for the selected mode.
        Implements OFPO Step 1 (Next Action) and Step 2 (Verbosity Budget).
        """
        # Default Budget (OFPO Step 2)
        stage = user_state.get("user_stage", "exploring")
        if stage == "exploring":
            budget = {"sentences": 8, "bullets": 0}
        elif stage == "deciding":
            budget = {"sentences": 12, "bullets": 3}
        elif stage == "executing":
            budget = {"sentences": 5, "bullets": 0} # only what's needed
        elif stage == "stuck":
            budget = {"sentences": 8, "bullets": 2}
        else:
            budget = {"sentences": 5, "bullets": 0}

        constraints = {
            "max_sentences": budget["sentences"],
            "max_bullets": budget["bullets"],
            "complexity": "moderate",
            "jargon_allowed": True,
            "one_step_only": False,
            "mode_guidance": "",
            "next_action": "Explain" # Default action
        }

        # OFPO Step 1: Next Action Selection
        missing_info = user_state.get("missing_info", [])
        if missing_info:
            constraints["next_action"] = "Clarify"
            constraints["mode_guidance"] = f"Ask exactly ONE question about: {', '.join(missing_info)}. Do NOT teach yet."
            constraints["max_sentences"] = 2
        elif mode == "GREETING_MODE":
            constraints.update({
                "next_action": "Clarify",
                "max_sentences": 2,
                "complexity": "simple",
                "mode_guidance": "Conversational greeting. Ask one natural question. No info dump."
            })
        elif mode == "DISCOVERY_MODE":
            constraints.update({
                "next_action": "Clarify",
                "max_sentences": 2,
                "complexity": "simple",
                "mode_guidance": "Gently clarify the user's intent. Ask 1-2 simple questions. No teaching."
            })
        elif mode == "CURIOSITY_GATE":
            constraints.update({
                "next_action": "Clarify",
                "max_sentences": 2,
                "complexity": "simple",
                "mode_guidance": "Ask exactly ONE clarifying question. Do not assume specifics. No full plan yet. Stay in character."
            })
        elif mode == "ONRAMP_MODE":
            constraints.update({
                "next_action": "Coach",
                "max_sentences": 3,
                "complexity": "beginner",
                "jargon_allowed": False,
                "one_step_only": True,
                "mode_guidance": "No jargon. Simple language. Provide a clear pathway. ONE small actionable step. Do NOT ask advanced questions."
            })
        elif mode == "EXPLAIN_MODE":
            constraints.update({
                "next_action": "Explain",
                "max_sentences": 4,
                "complexity": "simple",
                "mode_guidance": "Use a simple analogy. Plain language. Short explanation."
            })
        elif mode == "RECOMMEND_MODE":
            constraints.update({
                "next_action": "Compare",
                "max_sentences": 3,
                "mode_guidance": "Give one strong recommendation OR ask one clarifying question."
            })
        elif mode == "COACH_MODE":
            constraints.update({
                "next_action": "Coach",
                "max_sentences": 5,
                "mode_guidance": "Supportive tone. Practical steps. Realistic advice."
            })
        elif mode == "DEEP_MODE":
            constraints.update({
                "next_action": "Execute",
                "max_sentences": 6,
                "complexity": "advanced",
                "mode_guidance": "Technical language allowed. Keep it structured and clear."
            })
        else: # DISCUSSION_MODE
            constraints.update({
                "next_action": "Discuss",
                "mode_guidance": "Natural human conversation. Gradual pacing. No lecturing."
            })

        # Failure Prevention: Force lower complexity if confusion is high
        if user_state.get("confusion_level") == "high":
            constraints["complexity"] = "simple"
            constraints["max_sentences"] = min(constraints["max_sentences"], 3)
            constraints["jargon_allowed"] = False

        return constraints

    def get_curious_question(self, creator_profile: Dict[str, Any], user_state: Dict[str, Any]) -> str:
        """
        Fetches a high-signal question from the creator's curiosity bank.
        """
        bank = creator_profile.get("curiosity_profile_json", {})
        if isinstance(bank, str): bank = json.loads(bank)
        
        questions = bank.get("early_stage_questions", [])
        if not questions:
            # Domain-based fallbacks
            domain = user_state.get("primary_domain", "general").lower()
            if "trade" in domain or "market" in domain:
                return "What market and timeframe are you thinking?"
            if "fit" in domain or "health" in domain:
                return "Are you trying to bulk, cut, or get stronger?"
            if "business" in domain or "money" in domain:
                return "What are you selling — product, service, or content?"
            return "What's the main goal you're working toward right now?"

        import random
        return random.choice(questions)

user_priority_service = UserPriorityService()
