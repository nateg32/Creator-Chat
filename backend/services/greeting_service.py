
import random
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class GreetingService:
    """
    Handles conversational greeting logic.
    Ensures creator-specific voice, avoids repetition, and applies constraints.
    """
    
    def generate_greeting(
        self, 
        user_name: Optional[str], 
        voice_profile: Dict[str, Any], 
        include_question: bool = True
    ) -> str:
        """
        Generate a deterministic but varied greeting based on creator profile.
        Format: [Opener] [Optional Name]. [Optional Question]
        """
        energy_bucket = (voice_profile.get("energy") or {}).get("bucket", "MID")
        
        # 1. Select Opener
        openers = []
        if energy_bucket == "HIGH":
            openers = voice_profile.get("greeting_high_energy", []) or ["Let's go!"]
        elif energy_bucket == "LOW":
            openers = voice_profile.get("greeting_short", []) or ["Hey."]
        else: # MID
            openers = voice_profile.get("greeting_neutral", []) or ["Hey there."]
            
        if not openers:
            openers = voice_profile.get("greetings", ["Hey"])
            
        opener = random.choice(openers)
        
        # 2. Assembly
        opener = opener.strip()
        if not opener.endswith((".", "!", "?")):
            opener += "!" if energy_bucket == "HIGH" else "."
                
        final_greeting = opener
        if user_name and len(opener.split()) < 3 and random.random() > 0.3:
            base = opener[:-1] 
            punct = opener[-1]
            final_greeting = f"{base} {user_name}{punct}"
            
        if not include_question:
            return final_greeting

        # 3. Select Question
        questions = voice_profile.get("greeting_questions", [])
        if not questions:
            questions = [
                "What's the goal?",
                "What are you trying to figure out?",
                "What's the biggest challenge today?"
            ]
        question = random.choice(questions)
        return f"{final_greeting} {question}"

greeting_service = GreetingService()
