
import json
import logging
from typing import List, Dict, Any, Optional
from backend.db import db
import backend.rag as rag
from backend.settings import settings

logger = logging.getLogger(__name__)

class MemoryService:
    """
    Handles conversational memory: extracting facts from user messages
    and retrieving relevant context for natural recall.
    """
    
    def get_relevant_context(self, user_id: int, creator_id: int, thread_id: str, current_message: str) -> List[Dict[str, Any]]:
        """
        Retrieve up to 2 relevant facts based on the current message.
        Uses semantic similarity (mocked for now with keyword matching) and recency.
        """
        if not user_id or not creator_id or not thread_id:
            return []
            
        row = db.execute_one(
            "SELECT facts FROM conversation_memories WHERE user_id = %s AND creator_id = %s AND thread_id = %s",
            (user_id, creator_id, thread_id)
        )
        
        if not row or not row.get("facts"):
            return []
            
        all_facts = row["facts"]
        if isinstance(all_facts, str):
            all_facts = json.loads(all_facts)
            
        # FILTERING LOGIC
        # 1. Decay/filtering (not implemented complex logic yet, just take top relevant)
        # 2. Semantic match: check if fact keywords appear in current message or related concepts
        # For V1, we return the 2 most recent high-confidence facts that share keywords
        
        relevant = []
        msg_lower = current_message.lower()
        
        # Simple keyword matching for relevance
        normalized_facts = []
        for i, f in enumerate(all_facts):
            # Score relevance: 1.0 if keywords match, 0.5 otherwise (recency bias)
            score = 0.5
            val = str(f.get("value", "")).lower()
            slot = str(f.get("slot", "")).lower()
            
            # If the user is talking about this topic now, it's highly relevant
            if slot in msg_lower or val in msg_lower:
                score = 1.0
            
            # Boost goals/constraints
            if slot in ["goal", "constraint", "preference"]:
                score += 0.2
                
            f["_match_score"] = score
            f["_index"] = i
            normalized_facts.append(f)
            
        # Sort by score desc, then recency (index desc)
        normalized_facts.sort(key=lambda x: (x["_match_score"], x["_index"]), reverse=True)
        
        # Take top 2
        return normalized_facts[:2]

    def update_memory(self, user_id: int, creator_id: int, thread_id: str, message: str):
        """
        Extract facts from the message and update the store.
        Should be called after response generation to not block critical path if possible,
        or just accept the latency.
        """
        if not user_id or not creator_id or not thread_id:
            return

        # lightweight extraction
        new_facts = self._extract_facts(message)
        if not new_facts:
            return
            
        # Update DB
        # We need to merge with existing facts (deduplicate? update values?)
        # For V1, just append or update if slot exists.
        
        # Fetch existing
        row = db.execute_one(
            "SELECT facts FROM conversation_memories WHERE user_id = %s AND creator_id = %s AND thread_id = %s",
            (user_id, creator_id, thread_id)
        )
        
        existing_facts = []
        if row and row.get("facts"):
            existing_facts = row["facts"]
            if isinstance(existing_facts, str):
                existing_facts = json.loads(existing_facts)
        
        # Merge logic
        # If slot exists, update value and timestamp/confidence.
        # Else append.
        
        for new_f in new_facts:
            found = False
            for old_f in existing_facts:
                if old_f["slot"] == new_f["slot"]:
                    old_f["value"] = new_f["value"]
                    old_f["confidence"] = new_f["confidence"]
                    # old_f["updated_at"] = now...
                    found = True
                    break
            if not found:
                existing_facts.append(new_f)
        
        # Save back
        # Use upsert
        db.execute_update("""
            INSERT INTO conversation_memories (user_id, creator_id, thread_id, facts, last_interaction)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (user_id, creator_id, thread_id) 
            DO UPDATE SET facts = EXCLUDED.facts, last_interaction = NOW()
        """, (user_id, creator_id, thread_id, json.dumps(existing_facts)))
        
        logger.info(f"Updated memory for user {user_id}: {len(new_facts)} new facts.")

    def _extract_facts(self, text: str) -> List[Dict[str, Any]]:
        """
        Use LLM to extract 0-3 key facts.
        """
        system_prompt = """
        You are a Fact Extraction specialized AI.
        Extract key user information from the message for conversational memory.
        Focus on:
        - Goals (e.g. "want to lose weight", "building a SaaS")
        - Constraints (e.g. "only have 30 mins", "low budget")
        - Personal details (e.g. "I'm 30", "I live in NY")
        - Preferences (e.g. "I hate running", "prefer video format")
        
        Output JSON list of objects: {"slot": "category", "value": "short_summary", "confidence": 0.0-1.0}
        Only output high-confidence, non-trivial facts.
        If nothing relevant, output [].
        Max 3 facts.
        """
        
        try:
            resp = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                model=settings.ROUTER_MODEL, # fast model
                temperature=0.0,
                json_mode=True
            )
            data = json.loads(resp)
            if isinstance(data, list):
                return [f for f in data if f.get("confidence", 0) > 0.7]
            if isinstance(data, dict) and "facts" in data:
                return [f for f in data["facts"] if f.get("confidence", 0) > 0.7]
            return []
        except Exception as e:
            logger.error(f"Fact extraction failed: {e}")
            return []

memory_service = MemoryService()
