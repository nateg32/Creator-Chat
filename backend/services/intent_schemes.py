
INTENT_SLOT_SCHEMES = {
    "start_goal": {
        "required": ["goal_type", "experience_level"],
        "optional": ["timeframe", "constraints"],
        "description": "Starting a new fitness or business program/goal."
    },
    "recommend_content": {
        "required": ["target_topic"],
        "optional": ["format_preference", "learning_phase"],
        "description": "Requesting a specific video or resource recommendation."
    },
    "how_to": {
        "required": ["current_context", "specific_problem"],
        "optional": ["previous_attempts", "constraints"],
        "description": "Asking for step-by-step instructions or troubleshooting."
    },
    "small_talk": {
        "required": [],
        "optional": ["mood", "user_status"],
        "description": "Casual conversation or greetings."
    },
    "identity": {
        "required": [],
        "optional": ["user_name"],
        "description": "Asking about the bot's identity or the creator."
    }
}

# Mapping of slot -> priority (for asking questions)
SLOT_PRIORITY = {
    "goal_type": 100,
    "experience_level": 90,
    "target_topic": 100,
    "specific_problem": 100,
    "current_context": 80,
    "timeframe": 50,
    "format_preference": 40
}
