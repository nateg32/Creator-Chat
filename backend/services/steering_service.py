
import logging
import json
import re
from typing import Dict, Any, List, Optional
import backend.rag as rag
from backend.settings import settings

logger = logging.getLogger(__name__)

class SteeringService:
    """
    Implements Conversation Steering and Response Planning.
    Model: GPT-5.2
    """

    def create_response_plan(
        self, 
        user_state: Dict[str, Any], 
        memory: Dict[str, Any],
        creator_id: int
    ) -> Dict[str, Any]:
        """
        Creates a structured response_plan to guide the main reply.
        """
        skill = user_state.get("skill_level", "unknown")
        confusion = user_state.get("confusion_level", "low")
        mode = user_state.get("suggested_mode", "EXPLAIN")
        req_type = user_state.get("request_type", "discussion")
        
        # 1. Base constraints
        plan = {
            "mode": mode,
            "domain_action": "ANSWER", 
            "max_sentences": 4,
            "max_questions": 1,
            "allow_jargon": skill != "beginner",
            "must_include": [],
            "must_avoid": ["as an AI", "based on available content", "I'm sorry"],
            "video_policy": "none"
        }

        # 2. Curiosity Gate / Greeting / Discovery Adaptation
        if mode in ["CURIOSITY_GATE", "GREETING", "DISCOVERY"]:
            plan["max_sentences"] = 2
            plan["domain_action"] = "LISTEN" if mode != "GREETING" else "GREET"
            plan["video_policy"] = "none"
            plan["must_include"] = ["one_clarifying_question"] if mode != "GREETING" else ["warm_greeting"]
            plan["must_avoid"].append("long_roadmap")
            plan["must_avoid"].append("business_advice")
            return plan

        # 3. Adaptation logic
        if skill == "beginner" or confusion == "high":
            plan["max_sentences"] = 3
            plan["allow_jargon"] = False
            plan["must_include"].append("one_simple_action")
            
        if user_state.get("emotion", {}).get("primary") == "overwhelmed":
            plan["max_sentences"] = 2
            plan["must_include"] = ["supportive_statement", "single_next_step"]

        if req_type in ["recommendation", "pathway"]:
            plan["video_policy"] = "one_if_helpful"
            plan["must_include"].append("roadmap_context")

        return plan

    def get_steering_guidance(self, plan: Dict[str, Any]) -> str:
        """
        Converts response_plan into a string for the main reply prompt.
        """
        guidance = "RESPONSE PLAN CONSTRAINTS:\n"
        guidance += f"- Mode: {plan['mode']}\n"
        guidance += f"- Length: Max {plan['max_sentences']} sentences.\n"
        guidance += f"- Questions: Max {plan['max_questions']} questions.\n"
        guidance += f"- Jargon Allowed: {plan['allow_jargon']}\n"
        
        if plan.get("must_include"):
            guidance += f"- Must Include: {', '.join(plan['must_include'])}\n"
            
        return guidance

    def determine_steering_move(self, user_state: Dict[str, Any], memory: Dict[str, Any], question: str) -> Dict[str, Any]:
        """
        Calculates the internal steering move, new stage, and depth.
        """
        # Simple heuristic for now, or use LLM
        current_stage = memory.get("progress_stage", "starting")
        skill = user_state.get("skill_level", "unknown")
        depth = memory.get("topic_depth_level", 0)
        
        new_stage = current_stage
        if current_stage == "starting":
            new_stage = "explaining"
        
        # Simple topic extraction
        detected_topic = "general"
        if "risk" in question.lower(): detected_topic = "risk_management"
        if "psychology" in question.lower(): detected_topic = "psychology"
        
        return {
            "steering_move": "CONTINUE_TOPIC" if detected_topic == memory.get("current_topic") else "SWITCH_TOPIC",
            "new_stage": new_stage,
            "detected_topic": detected_topic,
            "topic_depth": depth + 1 if detected_topic == memory.get("current_topic") else 1,
            "steering_guidance": f"Keep it {skill} level and focus on {detected_topic}."
        }

    def validate_steering(self, response: str, move: str, intent: str) -> Dict[str, Any]:
        """
        Heuristic validation of the response against steering constraints.
        """
        sentences = re.split(r'[.!?]+', response)
        sentences = [s for s in sentences if s.strip()]
        
        questions = response.count('?')
        
        report = {
            "drift_detected": False,
            "overwhelmed": False,
            "reason": None
        }
        
        if len(sentences) > 5:
            report["overwhelmed"] = True
            report["reason"] = f"Response too long ({len(sentences)} sentences)"
            
        if questions > 1:
            report["drift_detected"] = True
            report["reason"] = "Too many questions asked."
            
        return report

steering_service = SteeringService()
