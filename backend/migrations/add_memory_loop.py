
from db import db
import logging

logger = logging.getLogger(__name__)

def migrate():
    """Add memory_loop column to conversation_state."""
    logger.info("Adding memory_loop column to conversation_state...")
    
    query = """
    ALTER TABLE conversation_state 
    ADD COLUMN IF NOT EXISTS memory_loop JSONB DEFAULT '{
      "user_goal": null,
      "skill_level": "unknown",
      "known_topics": [],
      "confused_topics": [],
      "current_topic": null,
      "previous_steps_given": [],
      "progress_stage": "starting",
      "last_recommendation": null,
      "user_preferences": {}
    }'::jsonb;
    """
    
    try:
        db.execute_update(query)
        logger.info("Successfully added memory_loop column.")
    except Exception as e:
        logger.error(f"Migration failed: {e}")

if __name__ == "__main__":
    migrate()
