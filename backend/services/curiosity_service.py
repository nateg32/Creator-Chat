
import logging
import json
import random
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

DEFAULT_CURIOSITY_PROFILES = {
    "fitness": {
        "domain": "fitness",
        "curiosity_axes": ["goal", "training_age", "diet", "frequency", "constraints"],
        "max_questions_first_turn": 1,
        "question_style": "short",
        "slot_question_bank": {
            "goal": [
                "Are you trying to bulk, cut, or just get stronger?",
                "What’s the actual goal—body composition, pure strength, or performance?",
                "What's the main focus—losing fat or building muscle?"
            ],
            "training_age": [
                "How long have you been training seriously?",
                "Are you just getting started or have you been at this for a while?",
                "What's your lifting experience level?"
            ],
            "diet": [
                "How's the nutrition looking—are you tracking macros or just winging it?",
                "What does your daily diet look like right now?",
                "Are you focused on the kitchen or just the gym?"
            ],
            "frequency": [
                "How many days a week are you actually getting into the gym?",
                "What does your current training schedule look like?"
            ],
            "constraints": [
                "Any injuries or limitations I should know about?",
                "Anything holding you back from training properly right now?"
            ]
        },
        "asking_style": {
            "tone": "direct",
            "pushiness": 0.5
        }
    },
    "trading": {
        "domain": "trading",
        "curiosity_axes": ["market", "timeframe", "risk", "setup", "strategy"],
        "max_questions_first_turn": 1,
        "question_style": "short",
        "slot_question_bank": {
            "market": [
                "What market are you focused on these days—Forex, Crypto, or Stocks?",
                "What are we looking at specifically?",
                "What's your primary instrument of choice right now?"
            ],
            "timeframe": [
                "Timeframe—are we talking a scalp, a day trade, or a swing?",
                "Are you looking at the 1-minute chart or the daily?"
            ],
            "risk": [
                "What's the risk per trade—fixed percentage or fixed dollar amount?",
                "Where's the stop loss going on this setup?"
            ],
            "setup": [
                "What's the setup—are you playing a breakout or a reversal?",
                "What pattern are we looking for in this trade?"
            ],
            "strategy": [
                "What's the strategy—mean reversion or trend following?",
                "How are you defining your edge on this one?"
            ]
        },
        "asking_style": {
            "tone": "direct",
            "pushiness": 0.6
        }
    },
    "business": {
        "domain": "business",
        "curiosity_axes": ["goal", "bottleneck", "timeline", "leverage", "offer"],
        "max_questions_first_turn": 1,
        "question_style": "short",
        "slot_question_bank": {
            "goal": [
                "What’s the actual goal here—more customers, more profit, or more time?",
                "What are we trying to build or fix today?"
            ],
            "bottleneck": [
                "What's the biggest bottleneck—traffic, conversion, or delivery?",
                "Where is the system breaking right now?"
            ],
            "timeline": [
                "When do you actually want to have this completed by?",
                "What's the roadmap—are we looking to scale this week or this month?"
            ],
            "leverage": [
                "What's the leverage point—is it content, code, or capital?",
                "How are we looking to multiply the output here?"
            ],
            "offer": [
                "What exactly are you selling and for how much?",
                "What's the core offer you're bringing to market?"
            ]
        },
        "asking_style": {
            "tone": "warm_direct",
            "pushiness": 0.4
        }
    }
}

class CuriosityService:
    def get_profile(self, creator_row: Dict[str, Any]) -> Dict[str, Any]:
        profile = creator_row.get("curiosity_profile")
        if not profile or (isinstance(profile, dict) and not profile):
            # Infer domain from name or handle as a fallback
            name = (creator_row.get("name") or "").lower()
            if any(w in name for w in ["fit", "coach", "train"]):
                return DEFAULT_CURIOSITY_PROFILES.get("fitness")
            if any(w in name for w in ["trade", "crypto", "market"]):
                return DEFAULT_CURIOSITY_PROFILES.get("trading")
            return DEFAULT_CURIOSITY_PROFILES.get("business")
            
        if isinstance(profile, str):
            try:
                return json.loads(profile)
            except:
                return DEFAULT_CURIOSITY_PROFILES.get("business")
        return profile

    def select_next_question(
        self, 
        profile: Dict[str, Any], 
        known_slots: Dict[str, Any],
        last_asked_slot: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Selects the best axis and a question variant.
        """
        axes = profile.get("curiosity_axes") or profile.get("curiosity_slots_ranked", [])
        question_bank = profile.get("slot_question_bank", {})
        
        # 1. Identify missing axes that weren't just asked
        candidates = [s for s in axes if s not in known_slots and s != last_asked_slot]
        
        if not candidates:
            # If everything is filled, pick from all axes to go deeper
            candidates = axes
            
        if not candidates:
            return None, None
            
        # Select randomly from top 3 candidates to add variance
        top_candidates = candidates[:3]
        selected_slot = random.choice(top_candidates)
        variants = question_bank.get(selected_slot, ["What can I help with?"])
        selected_question = random.choice(variants)
        
        return selected_slot, selected_question

curiosity_service = CuriosityService()
