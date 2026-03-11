import random
import logging
import re
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)
COLLECTIVE_GREETING_RE = re.compile(r"\b(everyone|everybody|guys|team|friends|fam|family|folks|yall|y'all|chat)\b", re.IGNORECASE)
DIRECT_DM_OPENERS = ["Hi", "Hey", "Hello", "What's up"]
NAME_QUESTIONS = ["What's your name?", "What should I call you?"]


class GreetingService:
    """
    Handles conversational greeting logic.
    Ensures creator specific voice, avoids repetition, and applies constraints.
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

        openers = []
        if energy_bucket == "HIGH":
            openers = voice_profile.get("greeting_high_energy", []) or ["Let's go"]
        elif energy_bucket == "LOW":
            openers = voice_profile.get("greeting_short", []) or ["Hey"]
        else:
            openers = voice_profile.get("greeting_neutral", []) or ["Hey there"]

        if not openers:
            openers = voice_profile.get("greetings", ["Hey"])

        opener = random.choice(openers).strip()
        if COLLECTIVE_GREETING_RE.search(opener):
            opener = random.choice(DIRECT_DM_OPENERS)
        opener = COLLECTIVE_GREETING_RE.sub("", opener)
        opener = re.sub(r"\s+", " ", opener).strip(" ,.!?") or random.choice(DIRECT_DM_OPENERS)

        if not opener.endswith((".", "!", "?")):
            opener += "!" if energy_bucket == "HIGH" else "."

        final_greeting = opener
        clean_name = (user_name or "").strip()
        if clean_name and len(opener.split()) < 4:
            base = opener[:-1]
            punct = opener[-1]
            final_greeting = f"{base} {clean_name}{punct}"

        if not include_question:
            return final_greeting

        if not clean_name:
            question = random.choice(NAME_QUESTIONS)
            return f"{final_greeting} {question}"

        questions = voice_profile.get("greeting_questions", []) or [
            "What's the goal?",
            "What are you trying to figure out?",
            "What's the biggest challenge today?"
        ]
        question = random.choice(questions)
        return f"{final_greeting} {question}"


greeting_service = GreetingService()
