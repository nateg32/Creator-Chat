
import logging
import json
import re
from typing import Dict, Any, List, Optional, Tuple
from backend.db import db
import backend.rag as rag
from backend.services.research_provider import GeminiResearchProvider
from backend.settings import settings

from backend.services.decision_service import decision_service

logger = logging.getLogger(__name__)

class PersonalBioService:
    """
    Handles personal/biographical questions about the creator.
    Pipeline:
    1. Search internal knowledge (chunks, bios).
    2. If validation fails/low confidence -> Search Web (trusted sources).
    3. Determine Decision Move (Answer/Decline/Reframe) using policy.
    4. Synthesize answer in creator voice based on the chosen move.
    """

    def __init__(self):
        from backend.services.research_provider import get_research_provider
        self.researcher = get_research_provider()

    def handle_personal_question(
        self, 
        user_id: int, 
        creator_id: int, 
        question: str, 
        voice_profile: Dict[str, Any],
        creator_name: str,
        decision_policy: Dict[str, Any],
        creator_profile: Optional[Dict[str, Any]] = None,
        allow_web: bool = True,
    ) -> Dict[str, Any]:
        """
        Main entry point. Returns { "answer": str, "confidence": "HIGH"|"MEDIUM"|"LOW", "sources": [], "move": str }
        """
        logger.info(f"PersonalBioService: Processing '{question}' for creator {creator_id}")
        
        # 1. Classification
        q_type, topic, sufficiency = decision_service.classify_question(question, "personal_bio_question")
        
        # 2. Evidence Gathering
        internal_facts = self._search_internal_knowledge(creator_id, question, creator_profile=creator_profile)
        
        web_facts = []
        if allow_web and self._needs_more_evidence(internal_facts):
            logger.info("PersonalBioService: Internal evidence weak, checking web...")
            web_facts = self._search_web_evidence(creator_id, creator_name, question, creator_profile=creator_profile)
            
        all_evidence = internal_facts + web_facts
        
        # 3. Confidence Scoring (Basic logic for routing)
        confidence = "LOW"
        if all_evidence:
             max_sim = max([e.get("sim", 0) for e in all_evidence if "sim" in e] or [0.8])
             if max_sim > 0.85: confidence = "HIGH"
             elif max_sim > 0.7: confidence = "MEDIUM"

        # 4. Decision Router
        move = decision_service.choose_move(decision_policy, q_type, topic, confidence, sufficiency=sufficiency)
        logger.info(f"PersonalBioService: Decision Move = {move} (Topic: {topic}, Confidence: {confidence})")

        # 5. Synthesis
        synthesis = self._synthesize_answer(
            question, 
            all_evidence, 
            voice_profile, 
            creator_name, 
            move,
            topic
        )
        synthesis["move"] = move
        
        return synthesis

    def _search_internal_knowledge(self, creator_id: int, question: str, creator_profile: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        emb = rag.create_embedding(question)
        rows = db.execute_query("""
            SELECT content, metadata, 1 - (embedding <=> %s::vector) as sim
            FROM chunks 
            WHERE creator_id = %s
            AND 1 - (embedding <=> %s::vector) > 0.65
            ORDER BY sim DESC
            LIMIT 5
        """, (str(emb), creator_id, str(emb)))
        
        facts = []
        for r in rows:
            facts.append({
                "text": r["content"],
                "source": "internal",
                "sim": float(r["sim"])
            })

        profile = creator_profile or {}
        identity = profile.get("identity_fingerprint") or {}
        research = profile.get("research_summary") or {}
        if isinstance(identity, str):
            try:
                identity = json.loads(identity)
            except Exception:
                identity = {}
        if isinstance(research, str):
            try:
                research = json.loads(research)
            except Exception:
                research = {}

        def _push_fact(label: str, value: Any):
            if not value:
                return
            if isinstance(value, list):
                if not value:
                    return
                value_text = "; ".join(str(v) for v in value[:5] if v)
            elif isinstance(value, dict):
                value_text = json.dumps(value)
            else:
                value_text = str(value)
            value_text = value_text.strip()
            if not value_text:
                return
            facts.append({
                "text": f"{label}: {value_text}",
                "source": "profile",
                "sim": 0.9,
            })

        _push_fact("Bio", identity.get("bio"))
        _push_fact("Mission", identity.get("mission"))
        _push_fact("Worldview", identity.get("worldview"))
        _push_fact("Verified facts", identity.get("verified_facts"))
        _push_fact("Public consensus", research.get("public_consensus"))
        _push_fact("Creator claims", research.get("creator_claims"))
        _push_fact("Themes", research.get("themes"))
        return facts

    def _search_web_evidence(self, creator_id: int, creator_name: str, question: str, creator_profile: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        query = f"{creator_name} {question}".strip()
        try:
            if hasattr(self.researcher, "search_general"):
                results = self.researcher.search_general(query, creator_id, creator_profile=creator_profile)
            else:
                profile = dict(creator_profile or {})
                profile.setdefault("id", creator_id)
                profile.setdefault("name", creator_name)
                results = self.researcher.search(query, profile, resource_type="any", conversation_history=None)
        except Exception as e:
            logger.error(f"PersonalBioService: Web evidence search failed: {e}")
            return []

        normalized = []
        for result in results or []:
            title = (result.get("title") or "").strip()
            snippet = (result.get("snippet") or result.get("text") or "").strip()
            url = (result.get("url") or "").strip()
            if not any([title, snippet, url]):
                continue
            normalized.append({
                "text": " | ".join(part for part in [title, snippet] if part)[:500],
                "source": "web",
                "url": url,
                "title": title,
                "sim": 0.82,
            })
        return normalized

    def _needs_more_evidence(self, facts: List[Dict[str, Any]]) -> bool:
        if not facts: return True
        max_sim = max(f["sim"] for f in facts) if facts else 0
        if max_sim < 0.75: return True
        return False

    def _synthesize_answer(
        self, 
        question: str, 
        evidence: List[Dict[str, Any]], 
        voice_profile: Dict[str, Any],
        creator_name: str,
        move: str,
        topic: str
    ) -> Dict[str, Any]:
        
        evidence_text = "\n".join([f"- [{e.get('source', 'unknown')}]: {e.get('text', '')[:300]}" for e in evidence])
        vp_json = json.dumps(voice_profile, indent=2)
        
        # Move specific guidance
        move_guidance = ""
        if move == "ANSWER_DIRECTLY":
            move_guidance = "Answer the question directly and concisely based on the evidence."
        elif move == "ANSWER_WITH_QUALIFIER":
            move_guidance = "Answer cautiously. Start with something like 'From what I've shared publicly...' or 'If I recall correctly...'"
        elif move == "DECLINE_PRIVATE":
            move_guidance = "Do NOT answer. Respectfully decline by saying you keep that part of your life private."
        elif move == "DEFLECT_WITH_HUMOR":
            move_guidance = "Do NOT answer. Make a short, creator-appropriate joke or playful remark and pivot away."
        elif move == "REFRAME_TO_DOMAIN":
            move_guidance = "Briefly acknowledge the question (if benign) but immediately turn it into a lesson or principle related to your domain (business/training)."
        elif move == "BOUNDARY_PUSHBACK":
            move_guidance = "Firmly refuse to answer. Don't be rude, but be very clear it's not something you share."
        elif move == "ASK_CLARIFY":
            move_guidance = "The question is too vague. Ask a short, creator-natural clarifying question to understand what they specifically want to know about you."

        system_prompt = f"""
You are {creator_name}. 
DECISION MOVE: {move}
TOPIC: {topic}

CONVERSATIONAL GOAL: {move_guidance}

Voice Profile:
{vp_json}

RULES:
1. MAX 3 sentences. No paragraphs. No lists.
2. NO system language, NO "AI", NO "Note:", NO "Based on content".
3. NEVER invent facts. If the move is to answer but evidence is missing, pivot to DECLINE_PRIVATE.
4. Stay strictly in the creator's identity.

Move-Specific Logic:
- DECLINE_PRIVATE: "I keep that side of my life private." (or creator equivalent)
- UNCERTAINTY: "I haven't really talked about that publicly, so I wouldn't want to guess."
- NO DISCLAIMERS.

OUTPUT format (JSON):
{{
    "answer": "string (in creator voice)",
    "confidence": "HIGH" | "MEDIUM" | "LOW",
    "reasoning": "internal move check"
}}
"""
        user_prompt = f"""
User Question: {question}

Available Evidence:
{evidence_text}

Draft your response following the DECISION MOVE: {move}
"""
        try:
            resp = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model=settings.FINAL_RESPONSE_MODEL,
                temperature=0.0,
                json_mode=True
            )
            data = json.loads(resp)
            return {
                "answer": data.get("answer", "I haven't really talked about that publicly."),
                "confidence": data.get("confidence", "LOW"),
                "sources": evidence
            }
        except Exception as e:
            logger.error(f"Personal bio synthesis failed: {e}")
            return {
                "answer": self._generate_uncertain_response(voice_profile),
                "confidence": "LOW",
                "sources": []
            }

    def _generate_uncertain_response(self, voice_profile: Dict[str, Any]) -> str:
        return "I haven't really talked about that publicly, so I wouldn't want to guess."

personal_bio_service = PersonalBioService()
