
import logging
import json
from typing import Dict, Any, List, Optional
import rag
from settings import settings

logger = logging.getLogger(__name__)

class MemoryLoopService:
    """
    Manages the Conversation Memory Loop.
    Model: GPT-4.1
    """

    def extract_memory_updates(
        self, 
        current_message: str, 
        current_memory: Dict[str, Any], 
        user_state: Dict[str, Any],
        history: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """
        Updates the conversation_memory object based on the new message.
        """
        history_str = ""
        if history:
            history_str = "\n".join([f"{m['role']}: {m['content']}" for m in history[-10:]])

        system_prompt = f"""
        You are a Conversation Memory Manager. 
        Your task is to update the 'conversation_memory' JSON object.
        
        CURRENT MEMORY:
        {json.dumps(current_memory, indent=2)}
        
        USER STATE:
        {json.dumps(user_state, indent=2)}
        
        RULES:
        1. user_goal: If the user explicitly states a goal, update it.
        2. current_topic: The primary focus of the discussion right now.
        3. skill_level: update if user shows more/less proficiency.
        4. known_topics / confused_topics: APPEND new topics. Do not overwrite.
        5. progress_stage: starting|learning|practicing|refining. Advance when appropriate.
        6. recent_emotion: store the latest emotion intensity.
        
        Output ONLY the updated JSON memory object.
        """

        user_prompt = f"History:\n{history_str}\n\nLast User Message: {current_message}"

        try:
            resp = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model=settings.MODEL_MEMORY,
                temperature=0.0,
                json_mode=True
            )
            updated_memory = json.loads(resp)
            
            # Sanitization Merge
            for list_field in ["known_topics", "confused_topics", "previous_steps_given"]:
                old_list = current_memory.get(list_field, [])
                new_list = updated_memory.get(list_field, [])
                # Ensure we only append unique items
                merged = sorted(list(set(old_list + new_list)))
                updated_memory[list_field] = merged
                
            return updated_memory
        except Exception as e:
            logger.error(f"Memory update failed: {e}")
            return current_memory

    def get_memory_guidance(self, memory: Dict[str, Any]) -> str:
        """
        Converts memory JSON into a string for LLM prompting.
        """
        guidance = "CONVERSATION MEMORY HIGHLIGHTS:\n"
        if memory.get("user_goal"):
            guidance += f"- Goal: {memory['user_goal']}\n"
        if memory.get("current_topic"):
            guidance += f"- Topic: {memory['current_topic']}\n"
        if memory.get("confused_topics"):
            guidance += f"- Confused about: {', '.join(memory['confused_topics'])}\n"
        if memory.get("previous_steps_given"):
            guidance += f"- Steps already given: {', '.join(memory['previous_steps_given'][-3:])}\n"
        
        guidance += f"- Progress: {memory.get('progress_stage', 'starting')}\n"
        return guidance

memory_loop_service = MemoryLoopService()
