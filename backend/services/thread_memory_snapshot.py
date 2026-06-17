import json
import logging
import re
from typing import Any, Dict, List, Optional

from backend.db import db
import backend.rag as rag
from backend.settings import settings
from backend.services.conversation_memory_packet import (
    build_conversation_memory_packet,
    clean_list,
    clean_text,
    extract_recent_resources,
    packet_prompt_block,
)

logger = logging.getLogger(__name__)


SNAPSHOT_FIELDS = [
    "user_context",
    "goals",
    "preferences",
    "constraints",
    "answered_questions",
    "open_questions",
    "advice_given",
    "resources_shared",
]

SNAPSHOT_SCALAR_FIELDS = [
    "current_topic",
    "last_user_intent",
    "last_assistant_question",
    "last_response_summary",
    "pending_followup_target",
    "conversation_summary",
    "next_best_step",
]


class ThreadMemorySnapshotService:
    """Compact per-thread state for fast prompt reads and background updates."""

    def __init__(self):
        self._schema_ready: Optional[bool] = None

    def _default_snapshot(self) -> Dict[str, Any]:
        return {
            "current_topic": "",
            "last_user_intent": "",
            "last_assistant_question": "",
            "last_response_summary": "",
            "pending_followup_target": "",
            "conversation_summary": "",
            "user_context": [],
            "goals": [],
            "preferences": [],
            "constraints": [],
            "answered_questions": [],
            "open_questions": [],
            "advice_given": [],
            "resources_shared": [],
            "last_referenced_items": [],
            "next_best_step": "",
        }

    def _ensure_schema(self) -> bool:
        if self._schema_ready is not None:
            return self._schema_ready

        try:
            db.execute_update(
                """
                CREATE TABLE IF NOT EXISTS thread_memory_snapshots (
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
                    thread_id TEXT NOT NULL,
                    snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, creator_id, thread_id)
                )
                """
            )
            self._schema_ready = True
        except Exception as exc:
            logger.warning("Thread memory snapshot schema unavailable: %s", exc)
            self._schema_ready = False
        return bool(self._schema_ready)

    def load_snapshot(self, user_id: int, creator_id: int, thread_id: str) -> Dict[str, Any]:
        if not user_id or not creator_id or not thread_id or not self._ensure_schema():
            return self._default_snapshot()

        try:
            row = db.execute_one(
                """
                SELECT snapshot
                FROM thread_memory_snapshots
                WHERE user_id = %s AND creator_id = %s AND thread_id = %s
                """,
                (user_id, creator_id, str(thread_id)),
            )
        except Exception as exc:
            logger.warning("Thread memory snapshot load failed: %s", exc)
            return self._default_snapshot()

        if not row or not row.get("snapshot"):
            return self._default_snapshot()

        raw = row.get("snapshot")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        if not isinstance(raw, dict):
            raw = {}
        return self._sanitize_snapshot(raw)

    def get_prompt_block(self, user_id: int, creator_id: int, thread_id: str) -> str:
        snapshot = self.load_snapshot(user_id, creator_id, thread_id)
        if self._is_empty(snapshot):
            return ""

        lines = [
            "THREAD MEMORY SNAPSHOT (current chat state):",
            "Use this to continue the same conversation. Do not re-ask answered questions. Do not repeat advice or resources already given unless the user asks.",
        ]
        labels = {
            "user_context": "User context",
            "goals": "Goals",
            "preferences": "Preferences",
            "constraints": "Constraints",
            "answered_questions": "Answered questions",
            "open_questions": "Open questions to resolve",
            "advice_given": "Advice already given",
            "resources_shared": "Resources already shared",
        }
        for field, label in (
            ("current_topic", "Current topic"),
            ("conversation_summary", "Conversation summary"),
            ("pending_followup_target", "Pending follow-up target"),
        ):
            value = str(snapshot.get(field) or "").strip()
            if value:
                lines.append(f"- {label}: {value}")
        for field in SNAPSHOT_FIELDS:
            values = snapshot.get(field) or []
            if values:
                lines.append(f"- {labels[field]}: {'; '.join(values[:6])}")

        next_step = str(snapshot.get("next_best_step") or "").strip()
        if next_step:
            lines.append(f"- Next best step: {next_step}")

        return "\n".join(lines) + "\n"

    def get_runtime_prompt_block(
        self,
        user_id: int,
        creator_id: int,
        thread_id: str,
        current_user_message: str = "",
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Return the unified memory packet for runtime prompting.

        This is deterministic and bounded. It lets Gemini reason over clean
        memory without adding a model call to the chat hot path.
        """

        snapshot = self.load_snapshot(user_id, creator_id, thread_id)
        packet = build_conversation_memory_packet(
            current_user_message,
            history or [],
            snapshot=snapshot,
        )
        return packet_prompt_block(packet)

    def build_router_packet(
        self,
        question: str,
        history: Optional[List[Dict[str, Any]]] = None,
        *,
        user_id: int = 0,
        creator_id: int = 0,
        thread_id: str = "",
    ) -> Dict[str, Any]:
        snapshot = (
            self.load_snapshot(user_id, creator_id, thread_id)
            if user_id and creator_id and thread_id
            else self._default_snapshot()
        )
        return build_conversation_memory_packet(question, history or [], snapshot=snapshot)

    def update_after_turn(
        self,
        user_id: int,
        creator_id: int,
        thread_id: str,
        user_message: str,
        assistant_message: str,
        history: Optional[List[Dict[str, Any]]] = None,
        assistant_resources: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if not user_id or not creator_id or not thread_id or not self._ensure_schema():
            return

        current = self.load_snapshot(user_id, creator_id, thread_id)
        seeded = self._heuristic_patch(
            current,
            user_message,
            assistant_message,
            history or [],
            assistant_resources=assistant_resources or [],
        )
        if self._should_use_llm_update(seeded, user_message, assistant_message, history or [], assistant_resources or []):
            updated = self._llm_update_snapshot(
                seeded,
                user_message,
                assistant_message,
                history or [],
                assistant_resources=assistant_resources or [],
            )
            updated = self._heuristic_patch(
                updated,
                user_message,
                assistant_message,
                history or [],
                assistant_resources=assistant_resources or [],
            )
        else:
            updated = seeded
        updated = self._sanitize_snapshot(updated)

        try:
            db.execute_update(
                """
                INSERT INTO thread_memory_snapshots (user_id, creator_id, thread_id, snapshot, updated_at)
                VALUES (%s, %s, %s, %s::jsonb, NOW())
                ON CONFLICT (user_id, creator_id, thread_id)
                DO UPDATE SET snapshot = EXCLUDED.snapshot, updated_at = NOW()
                """,
                (user_id, creator_id, str(thread_id), json.dumps(updated)),
            )
        except Exception as exc:
            logger.warning("Thread memory snapshot save failed: %s", exc)

    def _llm_update_snapshot(
        self,
        current: Dict[str, Any],
        user_message: str,
        assistant_message: str,
        history: List[Dict[str, Any]],
        assistant_resources: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        recent_history = []
        for turn in (history or [])[-8:]:
            role = str(turn.get("role") or "").strip()
            content = str(turn.get("content") or "").strip()
            if role and content:
                recent_history.append(f"{role}: {content[:360]}")

        system_prompt = """
You update compact memory for one chat thread.
Return ONLY valid JSON with this exact shape:
{
  "current_topic": "",
  "last_user_intent": "",
  "last_assistant_question": "",
  "last_response_summary": "",
  "pending_followup_target": "",
  "conversation_summary": "",
  "user_context": [],
  "goals": [],
  "preferences": [],
  "constraints": [],
  "answered_questions": [],
  "open_questions": [],
  "advice_given": [],
  "resources_shared": [],
  "last_referenced_items": [],
  "next_best_step": ""
}

Rules:
- Preserve useful existing facts unless contradicted.
- Store only information useful for future replies in this same chat.
- If the assistant asked a question and the latest user message answers it, move it to answered_questions and remove it from open_questions.
- Treat short numeric answers, fragments, and "like X" replies as answers to the assistant's most recent question when the recent history makes the meaning clear.
- Keep open_questions only for important unknowns the creator still needs.
- advice_given should summarize what the assistant already recommended, so future replies do not repeat it.
- resources_shared should be short resource titles or topics already attached.
- last_referenced_items should hold the newest source/card/video/book/podcast references as short labels.
- current_topic should name the actual conversation thread, not just the latest words.
- conversation_summary should be one compact sentence that helps the next response continue naturally.
- pending_followup_target should hold the thing "it/that/the video" refers to, when relevant.
- next_best_step should be the most useful continuation for the next reply.
- Lists must be short, max 6 items each, each item max 12 words.
- Scalar fields must be short, max 18 words except conversation_summary max 35 words.
- Do not store profanity, catchphrases, or creator style as user preferences.
"""

        user_prompt = json.dumps(
            {
                "current_snapshot": current,
                "recent_history": recent_history,
                "latest_user_message": str(user_message or "")[:1200],
                "latest_assistant_message": str(assistant_message or "")[:1800],
                "assistant_resources": list(assistant_resources or [])[:5],
            },
            ensure_ascii=True,
        )

        try:
            response = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=getattr(settings, "MODEL_MEMORY", settings.ROUTER_MODEL),
                temperature=0.0,
                json_mode=True,
                max_tokens=450,
            )
            parsed = json.loads(response)
            if isinstance(parsed, dict):
                if not any(key in parsed for key in [*SNAPSHOT_FIELDS, "next_best_step"]):
                    return dict(current or {})
                return parsed
        except Exception as exc:
            logger.warning("Thread memory LLM update failed: %s", exc)
        return dict(current or {})

    def _heuristic_patch(
        self,
        snapshot: Dict[str, Any],
        user_message: str,
        assistant_message: str,
        history: List[Dict[str, Any]],
        assistant_resources: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        patched = dict(snapshot or {})
        lower_user = str(user_message or "").lower()
        lower_context = " ".join(
            str(turn.get("content") or "").lower()
            for turn in (history or [])[-6:]
        )

        if "soccer" in lower_user or "football" in lower_user:
            self._append_unique(patched, "user_context", "plays soccer")
        if "gym" in lower_user:
            self._append_unique(patched, "goals", "start going to the gym")
        if "business" in lower_user:
            self._append_unique(patched, "goals", "start a business")
        if "marketing agency" in lower_user or "marketing agency" in lower_context:
            self._append_unique(patched, "user_context", "runs a marketing agency")
        if (
            ("convert" in lower_user or "conversion" in lower_user or "close" in lower_user)
            and ("lead" in lower_user or "client" in lower_user or "customer" in lower_user)
        ) or (
            ("convert" in lower_context or "conversion" in lower_context or "close rate" in lower_context)
            and re.search(r"\b(?:like\s+)?\d+\b", lower_user)
        ):
            self._append_unique(patched, "constraints", "struggling to convert leads into high-paying customers")
        if "bit of both" in lower_user and "game" in lower_context:
            self._append_unique(patched, "preferences", "may train on soccer days and off days")

        resources = extract_recent_resources(history, assistant_resources=assistant_resources or [], limit=5)
        if resources:
            patched["last_referenced_items"] = [
                clean_text(item.get("title") or item.get("url"), limit=120)
                for item in resources
                if clean_text(item.get("title") or item.get("url"), limit=120)
            ][:5]
            for label in patched["last_referenced_items"][:3]:
                self._append_unique(patched, "resources_shared", label)
            patched["pending_followup_target"] = patched["last_referenced_items"][0]

        if self._looks_like_answer(user_message):
            open_questions = list(patched.get("open_questions") or [])
            answered = ""
            if open_questions:
                answered = open_questions.pop(0)
                patched["open_questions"] = open_questions
            else:
                answered = self._last_assistant_question(history)
            if answered:
                answer_text = self._contextualize_short_answer(answered, user_message)
                answered_label = self._clean_text(answered, max_len=72)
                self._append_unique(patched, "answered_questions", f"{answered_label} -> {answer_text}")

        asked = self._extract_questions(assistant_message)
        for question in asked[:2]:
            self._append_unique(patched, "open_questions", question)
        if asked:
            patched["last_assistant_question"] = asked[-1]

        advice = self._summarize_advice_heuristic(assistant_message)
        if advice:
            self._append_unique(patched, "advice_given", advice)

        current_topic = self._infer_current_topic(user_message, assistant_message, history, patched)
        if current_topic:
            patched["current_topic"] = current_topic
        response_summary = self._summarize_response_heuristic(assistant_message)
        if response_summary:
            patched["last_response_summary"] = response_summary
            if not patched.get("conversation_summary"):
                patched["conversation_summary"] = response_summary

        return patched

    def _sanitize_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        clean = self._default_snapshot()
        for field in SNAPSHOT_FIELDS:
            clean[field] = self._clean_list(snapshot.get(field), max_items=6)
        clean["advice_given"] = self._clean_list(snapshot.get("advice_given"), max_items=8)
        clean["last_referenced_items"] = self._clean_list(snapshot.get("last_referenced_items"), max_items=6)
        for field in SNAPSHOT_SCALAR_FIELDS:
            max_len = 220 if field == "conversation_summary" else 140
            clean[field] = self._clean_text(snapshot.get(field), max_len=max_len)
        next_step = self._clean_text(snapshot.get("next_best_step"), max_len=140)
        clean["next_best_step"] = next_step
        return clean

    def _is_empty(self, snapshot: Dict[str, Any]) -> bool:
        return (
            not any(snapshot.get(field) for field in SNAPSHOT_FIELDS)
            and not snapshot.get("last_referenced_items")
            and not any(snapshot.get(field) for field in SNAPSHOT_SCALAR_FIELDS)
        )

    def _clean_list(self, values: Any, max_items: int = 6) -> List[str]:
        if not isinstance(values, list):
            return []
        out: List[str] = []
        seen = set()
        for value in values:
            text = self._clean_text(value)
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                out.append(text)
            if len(out) >= max_items:
                break
        return out

    def _clean_text(self, value: Any, max_len: int = 120) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        text = text.strip("-:;,. ")
        if len(text) > max_len:
            text = text[:max_len].rsplit(" ", 1)[0].strip()
        return text

    def _append_unique(self, snapshot: Dict[str, Any], field: str, value: str) -> None:
        values = list(snapshot.get(field) or [])
        clean_value = self._clean_text(value)
        if not clean_value:
            return
        if clean_value.lower() not in {str(v).lower() for v in values}:
            values.append(clean_value)
        snapshot[field] = values

    def _extract_questions(self, text: str) -> List[str]:
        parts = re.findall(r"([^?]{8,160}\?)", str(text or ""))
        return [self._clean_text(part, max_len=120) for part in parts if part.strip()]

    def _looks_like_answer(self, text: str) -> bool:
        stripped = str(text or "").strip()
        if not stripped:
            return False
        if "?" in stripped and len(stripped) > 80:
            return False
        return len(stripped.split()) <= 18

    def _last_assistant_question(self, history: List[Dict[str, Any]]) -> str:
        for turn in reversed(history or []):
            if str(turn.get("role") or "") != "assistant":
                continue
            questions = self._extract_questions(str(turn.get("content") or turn.get("text") or ""))
            if questions:
                return questions[-1]
        return ""

    def _contextualize_short_answer(self, question: str, answer: str) -> str:
        question_clean = self._clean_text(question, max_len=100)
        answer_clean = self._clean_text(answer, max_len=80)
        number_match = re.search(r"\b(?:like\s+|about\s+|around\s+)?(\d+(?:\.\d+)?)\b", answer_clean.lower())
        if number_match and re.search(r"\blast\s+(?:ten|10)\b", question_clean.lower()):
            return f"about {number_match.group(1)} out of the last 10"
        return answer_clean

    def _summarize_advice_heuristic(self, assistant_message: str) -> str:
        text = str(assistant_message or "")
        lower = text.lower()
        if "full body" in lower:
            return "recommended full body training"
        if "compound" in lower or "squat" in lower:
            return "recommended compound lifts"
        if "recovery" in lower:
            return "emphasized recovery"
        if "test demand" in lower:
            return "recommended testing demand first"
        return ""

    def _should_use_llm_update(
        self,
        snapshot: Dict[str, Any],
        user_message: str,
        assistant_message: str,
        history: List[Dict[str, Any]],
        assistant_resources: List[Dict[str, Any]],
    ) -> bool:
        if str(getattr(settings, "THREAD_MEMORY_LLM_UPDATES_ENABLED", "true")).lower() in {"0", "false", "no", "off"}:
            return False
        combined = f"{user_message} {assistant_message}".strip()
        words = re.findall(r"[a-z0-9']+", combined.lower())
        if assistant_resources:
            return True
        if snapshot.get("open_questions") or snapshot.get("constraints") or snapshot.get("goals"):
            return len(words) >= 5
        if len(words) >= int(getattr(settings, "THREAD_MEMORY_LLM_MIN_SIGNAL_WORDS", 18)):
            return True
        if re.search(r"\b(?:business|agency|client|customer|lead|convert|sales|gym|fitness|goal|struggling|stuck|remember|prefer)\b", combined, re.IGNORECASE):
            return True
        return False

    def _infer_current_topic(
        self,
        user_message: str,
        assistant_message: str,
        history: List[Dict[str, Any]],
        snapshot: Dict[str, Any],
    ) -> str:
        lower_user = str(user_message or "").lower()
        lower_context = " ".join(str(turn.get("content") or "").lower() for turn in (history or [])[-6:])
        if "marketing agency" in lower_user or "marketing agency" in lower_context:
            if any(term in lower_user or term in lower_context for term in ("convert", "conversion", "close", "sales")):
                return "marketing agency lead conversion"
            return "marketing agency growth"
        if "sales script" in lower_user or "sales script" in lower_context:
            return "sales script and lead conversion"
        if "business" in lower_user and any(term in lower_user for term in ("start", "starting", "tryna", "trying")):
            return "starting a business"
        if "turn" in lower_user and "around" in lower_user:
            existing = str(snapshot.get("current_topic") or "")
            return existing or "creator turning point"
        resources = snapshot.get("last_referenced_items") or []
        if resources:
            return str(resources[0])
        clean_user = self._clean_text(user_message, max_len=90)
        if clean_user and len(clean_user.split()) >= 4 and "?" not in clean_user[:8]:
            return clean_user
        return self._clean_text(snapshot.get("current_topic"), max_len=90)

    def _summarize_response_heuristic(self, assistant_message: str) -> str:
        claims = []
        for sentence in re.split(r"(?<=[.!?])\s+", str(assistant_message or "")):
            clean = self._clean_text(sentence, max_len=120)
            if len(clean.split()) < 5:
                continue
            if re.search(r"\b(?:attached|link below|source below|copy)\b", clean, re.IGNORECASE):
                continue
            claims.append(clean)
            if len(claims) >= 2:
                break
        return " ".join(claims)


thread_memory_snapshot_service = ThreadMemorySnapshotService()
