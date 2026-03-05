
import json
import logging
from typing import Dict, Any, Optional, List
from backend.db import db
from backend.services.intent_schemes import INTENT_SLOT_SCHEMES, SLOT_PRIORITY
import backend.rag as rag
from backend.settings import settings

logger = logging.getLogger(__name__)

class ConversationStateManager:
    def __init__(self, user_id: int, creator_id: int, thread_id: str):
        self.user_id = user_id
        self.creator_id = creator_id
        self.thread_id = thread_id
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        row = db.execute_one(
            "SELECT known_slots, last_question, last_intent, verbosity_pref, last_asked_slot, last_asked_question_variant, last_opener_filler, last_used_pause_marker, memory_loop FROM conversation_state WHERE user_id = %s AND creator_id = %s AND thread_id = %s",
            (self.user_id, self.creator_id, self.thread_id)
        )
        if row:
            return {
                "known_slots": row["known_slots"] if isinstance(row["known_slots"], dict) else json.loads(row["known_slots"] or '{}'),
                "last_question": row["last_question"] if isinstance(row["last_question"], dict) else json.loads(row["last_question"] or '{}'),
                "last_intent": row["last_intent"],
                "verbosity_pref": row["verbosity_pref"] or 'short',
                "last_asked_slot": row["last_asked_slot"],
                "last_asked_question_variant": row["last_asked_question_variant"],
                "last_opener_filler": row["last_opener_filler"],
                "last_used_pause_marker": row["last_used_pause_marker"],
                "memory_loop": row["memory_loop"] if isinstance(row["memory_loop"], dict) else json.loads(row["memory_loop"] or '{}')
            }
        return {
            "known_slots": {},
            "last_question": {},
            "last_intent": None,
            "verbosity_pref": "short",
            "last_asked_slot": None,
            "last_asked_question_variant": None,
            "last_opener_filler": None,
            "last_used_pause_marker": None,
            "memory_loop": {
                "user_goal": None,
                "skill_level": "unknown",
                "known_topics": [],
                "confused_topics": [],
                "current_topic": None,
                "previous_steps_given": [],
                "progress_stage": "starting",
                "last_recommendation": None,
                "user_preferences": {},
                "topic_depth_level": 0
            }
        }

    def save_state(self):
        db.execute_update(
            """
            INSERT INTO conversation_state (user_id, creator_id, thread_id, known_slots, last_question, last_intent, verbosity_pref, last_asked_slot, last_asked_question_variant, last_opener_filler, last_used_pause_marker, memory_loop, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id, creator_id, thread_id) DO UPDATE SET
                known_slots = EXCLUDED.known_slots,
                last_question = EXCLUDED.last_question,
                last_intent = EXCLUDED.last_intent,
                verbosity_pref = EXCLUDED.verbosity_pref,
                last_asked_slot = EXCLUDED.last_asked_slot,
                last_asked_question_variant = EXCLUDED.last_asked_question_variant,
                last_opener_filler = EXCLUDED.last_opener_filler,
                last_used_pause_marker = EXCLUDED.last_used_pause_marker,
                memory_loop = EXCLUDED.memory_loop,
                updated_at = EXCLUDED.updated_at
            """,
            (
                self.user_id,
                self.creator_id,
                self.thread_id,
                json.dumps(self.state["known_slots"]),
                json.dumps(self.state["last_question"]),
                self.state["last_intent"],
                self.state["verbosity_pref"],
                self.state["last_asked_slot"],
                self.state["last_asked_question_variant"],
                self.state["last_opener_filler"],
                self.state["last_used_pause_marker"],
                json.dumps(self.state["memory_loop"])
            )
        )

    def update_from_message(self, message: str, intent: str):
        """Extract slots from message and update known_slots."""
        self.state["last_intent"] = intent
        
        scheme = INTENT_SLOT_SCHEMES.get(intent)
        if not scheme:
            return

        all_slots = scheme["required"] + scheme["optional"]
        if not all_slots:
            return

        # Use LLM to extract slots
        extracted = self._extract_slots_llm(message, all_slots)
        
        # Merge new slots, only if they have actual values
        for k, v in extracted.items():
            if v and str(v).lower() not in ["null", "none", "unknown", ""]:
                self.state["known_slots"][k] = v

    def _extract_slots_llm(self, message: str, possible_slots: List[str]) -> Dict[str, Any]:
        system_prompt = f"""
You are a Slot Extractor for a Creator AI. Extract specific information from the user's message.
Possible slots to extract: {', '.join(possible_slots)}

Output ONLY a JSON object with the extracted keys. If a slot is not present, set it to null.
Do NOT hallucinate values.
"""
        try:
            resp = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"User Message: {message}"}
                ],
                model=settings.ROUTER_MODEL,
                temperature=0.0,
                json_mode=True
            )
            return json.loads(resp)
        except Exception as e:
            logger.error(f"Slot extraction failed: {e}")
            return {}

    def get_policy_decision(self, intent: str, answer_confidence: float = 1.0) -> str:
        """Decide ASK_ONE_QUESTION vs ANSWER_NOW."""
        scheme = INTENT_SLOT_SCHEMES.get(intent)
        if not scheme or not scheme["required"]:
            return "ANSWER_NOW"

        required = scheme["required"]
        filled = [s for s in required if s in self.state["known_slots"]]
        
        slot_sufficiency = len(filled) / len(required) if required else 1.0

        if slot_sufficiency < 0.6 or answer_confidence < 0.65:
            return "ASK_ONE_QUESTION"
        
        return "ANSWER_NOW"

    def get_best_question_slot(self, intent: str) -> Optional[str]:
        """Find the missing required slot with the highest priority."""
        scheme = INTENT_SLOT_SCHEMES.get(intent)
        if not scheme:
            return None

        required = scheme["required"]
        missing = [s for s in required if s not in self.state["known_slots"]]
        
        if not missing:
            return None

        # Sort by priority
        missing.sort(key=lambda s: SLOT_PRIORITY.get(s, 0), reverse=True)
        return missing[0]

    def set_last_asked(self, slot: str, variant: str):
        self.state["last_asked_slot"] = slot
        self.state["last_asked_question_variant"] = variant
