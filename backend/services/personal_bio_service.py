
import logging
import json
import re
from typing import Dict, Any, List, Optional, Tuple
from backend.db import db
import backend.rag as rag
from backend.services.research_provider import GeminiResearchProvider
from backend.settings import settings

from backend.services.decision_service import decision_service
from backend.services.live_search_rules import build_live_search_query
from backend.services.search_decision_engine import SearchDecisionEngine

logger = logging.getLogger(__name__)

MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|November|December"
)

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
        conversation_history: Optional[List[Dict[str, str]]] = None,
        allow_web: bool = True,
    ) -> Dict[str, Any]:
        """
        Main entry point. Returns { "answer": str, "confidence": "HIGH"|"MEDIUM"|"LOW", "sources": [], "move": str }
        """
        resolved_question = decision_service.resolve_followup_question(question, conversation_history)
        contextual_question = self._contextualize_search_question(
            resolved_question,
            creator_name,
            conversation_history,
        )
        logger.info(
            "PersonalBioService: Processing '%s' for creator %s (resolved='%s', contextual='%s')",
            question,
            creator_id,
            resolved_question,
            contextual_question,
        )
        
        # 1. Classification
        q_type, topic, sufficiency = decision_service.classify_question(resolved_question, "personal_bio_question", conversation_history)
        public_fact_query = self._is_public_creator_fact_query(contextual_question, creator_name, creator_profile)
        researcher_enabled = bool(getattr(self.researcher, "enabled", True))
        effective_allow_web = bool(allow_web or (public_fact_query and researcher_enabled))
        
        # 2. Evidence Gathering
        internal_facts = self._search_internal_knowledge(creator_id, contextual_question, creator_profile=creator_profile)
        
        web_facts = []
        if effective_allow_web and (public_fact_query or self._needs_more_evidence(internal_facts)):
            logger.info("PersonalBioService: Internal evidence weak, checking web...")
            web_facts = self._search_web_evidence(
                creator_id,
                creator_name,
                contextual_question,
                creator_profile=creator_profile,
                conversation_history=conversation_history,
            )
            
        all_evidence = internal_facts + web_facts

        if public_fact_query:
            direct_public_answer = self._answer_public_creator_fact(resolved_question, all_evidence, creator_name)
            if direct_public_answer:
                return {
                    "answer": direct_public_answer,
                    "confidence": "HIGH" if web_facts else "MEDIUM",
                    "sources": all_evidence,
                    "move": "ANSWER_PUBLIC_FACT",
                }

            synthesized_public_answer = self._synthesize_public_fact_answer(
                resolved_question,
                all_evidence,
                voice_profile,
                creator_name,
            )
            if synthesized_public_answer:
                return {
                    "answer": synthesized_public_answer,
                    "confidence": "HIGH" if web_facts else "MEDIUM",
                    "sources": all_evidence,
                    "move": "ANSWER_PUBLIC_FACT",
                }

            return {
                "answer": self._public_fact_fallback(resolved_question, creator_name),
                "confidence": "LOW",
                "sources": all_evidence,
                "move": "DIRECT_TO_OFFICIAL_SOURCE",
            }
        
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
            resolved_question, 
            all_evidence, 
            voice_profile, 
            creator_name, 
            move,
            topic
        )
        synthesis["move"] = move
        
        return synthesis

    def _contextualize_search_question(
        self,
        question: str,
        creator_name: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        return build_live_search_query(
            question,
            conversation_history,
            creator_name=creator_name,
        )

    def _is_public_creator_fact_query(
        self,
        question: str,
        creator_name: str,
        creator_profile: Optional[Dict[str, Any]] = None,
    ) -> bool:
        creator_payload = dict(creator_profile or {})
        creator_payload.setdefault("name", creator_name)
        decision = SearchDecisionEngine(creator_payload).pre_retrieval_decision(question)
        return bool(decision.should_search)

    def _search_internal_knowledge(self, creator_id: int, question: str, creator_profile: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        emb = rag.create_embedding(question)
        retrieved = rag.retrieve_chunks(
            creator_id=creator_id,
            query_embedding=emb,
            top_k=5,
            max_distance=0.35,
        )

        facts = []
        for chunk in retrieved:
            facts.append({
                "text": chunk.get("content", ""),
                "source": "internal",
                "title": chunk.get("title"),
                "url": chunk.get("url"),
                "sim": max(0.0, 1.0 - float(chunk.get("distance", 1.0)))
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

    def _search_web_evidence(
        self,
        creator_id: int,
        creator_name: str,
        question: str,
        creator_profile: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> List[Dict[str, Any]]:
        lowered_question = str(question or "").lower()
        profile = dict(creator_profile or {})
        profile.setdefault("id", creator_id)
        profile.setdefault("name", creator_name)
        search_engine = SearchDecisionEngine(profile)

        subject_hint = self._extract_subject(question, [])
        queries = [question.strip(), f"{creator_name} {question}".strip()]
        if any(token in lowered_question for token in ["book", "published", "publication", "release", "released", "launched", "launch", "come out", "write", "wrote", "written"]):
            queries.append(f"{creator_name} book published".strip())
            queries.append(f"{creator_name} first book".strip())
            queries.append(f"site:amazon.com {creator_name} book".strip())
            queries.append(f"site:audible.com {creator_name} book".strip())
            queries.append(f"site:penguinrandomhouse.com {creator_name} book".strip())
            queries.append(f"site:goodreads.com {creator_name} book".strip())
            if subject_hint and subject_hint.lower() not in {"it", "that", "this", "the book", "your book"}:
                queries.append(f'"{subject_hint}" published')
                queries.append(f'"{subject_hint}" release date')
                queries.append(f'site:amazon.com "{subject_hint}"')
                queries.append(f'site:audible.com "{subject_hint}"')
                queries.append(f'site:goodreads.com "{subject_hint}"')
            for term in search_engine.creator_terms:
                if len(term.split()) >= 2:
                    queries.append(f'"{term}" published')
                    queries.append(f'"{term}" release date')
                    queries.append(f'site:amazon.com "{term}"')
                    queries.append(f'site:audible.com "{term}"')
                    queries.append(f'site:goodreads.com "{term}"')

        results = []
        seen_queries = set()
        for query in queries:
            normalized_query = re.sub(r"\s+", " ", query).strip()
            if not normalized_query or normalized_query.lower() in seen_queries:
                continue
            seen_queries.add(normalized_query.lower())
            try:
                if callable(getattr(self.researcher, "grounded_overview", None)):
                    overview = self.researcher.grounded_overview(
                        normalized_query,
                        profile,
                        conversation_history=conversation_history,
                    ) or {}
                    results = list(overview.get("results") or [])
                elif hasattr(self.researcher, "search_general"):
                    results = self.researcher.search_general(normalized_query, creator_id, creator_profile=creator_profile)
                else:
                    results = self.researcher.search(
                        normalized_query,
                        profile,
                        resource_type="any",
                        conversation_history=conversation_history,
                    )
            except Exception as e:
                logger.error(f"PersonalBioService: Web evidence search failed for query '{normalized_query}': {e}")
                continue
            if results:
                break

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

    def _evidence_blob(self, evidence: List[Dict[str, Any]]) -> str:
        return " ".join(str(item.get("text") or "") for item in evidence if item.get("text"))

    def _extract_subject(self, question: str, evidence: List[Dict[str, Any]]) -> str:
        patterns = [
            re.compile(r"(?:when|where|what year|what date|which month)\s+(?:was|did)\s+(.+?)\s+(?:published|launch(?:ed)?|release(?:d)?|come out)", re.IGNORECASE),
            re.compile(r"(?:when)\s+(?:did)\s+(?:you|u)\s+write\s+(.+)", re.IGNORECASE),
            re.compile(r"where can i (?:buy|get|find|purchase)\s+(.+)", re.IGNORECASE),
        ]
        normalized_question = re.sub(r"\s+", " ", str(question or "")).strip(" ?!.")
        for pattern in patterns:
            match = pattern.search(normalized_question)
            if match:
                subject = re.sub(r"\s+", " ", match.group(1)).strip(" \"'")
                if subject:
                    return subject

        for item in evidence:
            title = str(item.get("title") or "").strip()
            if title:
                return title
        return ""

    def _answer_public_creator_fact(self, question: str, evidence: List[Dict[str, Any]], creator_name: str) -> str:
        blob = self._evidence_blob(evidence)
        lowered_question = str(question or "").lower()
        subject = self._extract_subject(question, evidence)
        subject = subject or "It"

        full_date = re.search(rf"\b({MONTH_PATTERN})\s+\d{{1,2}},\s+\d{{4}}\b", blob, re.IGNORECASE)
        month_year = re.search(rf"\b({MONTH_PATTERN})\s+\d{{4}}\b", blob, re.IGNORECASE)
        year = re.search(r"\b(20\d{2}|19\d{2})\b", blob)

        if any(token in lowered_question for token in ["when", "published", "publication", "release", "released", "launched", "launch", "come out", "what year", "what date", "which month"]):
            if full_date:
                return f"{subject} was published on {full_date.group(0)}."
            if month_year:
                return f"{subject} was published in {month_year.group(0)}."
            if year:
                return f"{subject} was published in {year.group(1)}."

        if any(token in lowered_question for token in ["where can i buy", "where do i buy", "where can i get", "where can i find", "purchase"]):
            domains = {
                (item.get("url") or "").lower(): item.get("url")
                for item in evidence
                if item.get("url")
            }
            mentions_amazon = "amazon" in blob.lower() or any("amazon." in key for key in domains)
            mentions_audible = "audible" in blob.lower() or any("audible." in key for key in domains)
            mentions_publisher = any(
                marker in blob.lower()
                for marker in ["penguin", "publisher", "harper", "random house", "simon", "press"]
            )
            options = []
            if mentions_amazon:
                options.append("Amazon")
            if mentions_audible:
                options.append("Audible")
            if mentions_publisher:
                options.append("the publisher page")
            if not options:
                options = ["Amazon", "Audible", "the publisher page"]
            if len(options) == 1:
                option_text = options[0]
            elif len(options) == 2:
                option_text = f"{options[0]} or {options[1]}"
            else:
                option_text = f"{options[0]}, {options[1]}, or {options[2]}"
            return f"You can get {subject} on {option_text}."

        return ""

    def _synthesize_public_fact_answer(
        self,
        question: str,
        evidence: List[Dict[str, Any]],
        voice_profile: Dict[str, Any],
        creator_name: str,
    ) -> str:
        if not evidence:
            return ""

        evidence_text = "\n".join([f"- [{e.get('source', 'unknown')}]: {e.get('text', '')[:300]}" for e in evidence])
        vp_json = json.dumps(voice_profile, indent=2)

        system_prompt = f"""
You are {creator_name}.

This is a public factual question about your own public work, products, books, releases, platforms, or stats.

Voice Profile:
{vp_json}

RULES:
1. Answer directly from the evidence in 1-2 sentences.
2. If the evidence contains a date, title, platform, or availability detail, lead with that concrete fact.
3. Never say "I haven't talked about that publicly" about your own public work.
4. Never say "I don't have that in front of me" about your own book, product, or release.
5. Never invent facts. If the evidence is still insufficient, direct the user to a concrete official source.

Return JSON:
{{
  "answer": "string"
}}
"""
        user_prompt = f"""
User Question: {question}

Evidence:
{evidence_text}
"""
        try:
            resp = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=settings.FINAL_RESPONSE_MODEL,
                temperature=0.0,
                json_mode=True,
            )
            data = json.loads(resp)
            return str(data.get("answer") or "").strip()
        except Exception as e:
            logger.error(f"Public fact synthesis failed: {e}")
            return ""

    def _public_fact_fallback(self, question: str, creator_name: str) -> str:
        lowered = str(question or "").lower()
        if "book" in lowered or "publish" in lowered or "publication" in lowered or "release" in lowered:
            return (
                "I want to give you the right date on that. Check my Amazon listing, Audible, "
                "or the publisher page for the exact publication info."
            )
        if any(token in lowered for token in ["where can i buy", "purchase", "course", "program"]):
            return (
                "I want to point you to the right place on that. Check my official website, "
                "course page, or verified profile links for the current listing."
            )
        return (
            "I want to give you the right answer on that. Check my official website, "
            "verified profiles, or the primary listing page for the exact current details."
        )

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
