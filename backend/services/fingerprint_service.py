import logging
import json
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from backend.db import db
from backend.personality_analyzer import PersonalityAnalyzer
from backend.services.research_provider import GeminiResearchProvider
from backend.settings import settings

logger = logging.getLogger(__name__)

class FingerprintService:
    """
    Orchestrates the Style Fingerprint system:
    1. Public Identity & Background (Biographical research)
    2. Voice & Style Profile (Linguistic analysis)
    """

    def __init__(self):
        from backend.services.research_provider import get_research_provider
        self.researcher = get_research_provider()
        self.analyzer = PersonalityAnalyzer()

    async def generate_fingerprint_async(self, creator_id: int, refresh: bool = False):
        """
        Main orchestration logic (Deep Research Upgrade).
        Phases: Link-First, Content Mining, Google Expansion, Synthesis.
        """
        try:
            # 1. Update status to 'processing'
            db.execute_update(
                "UPDATE creators SET fingerprint_status = 'processing' WHERE id = %s",
                (creator_id,)
            )

            # 2. Get Creator Info & Setup Links
            creator = db.execute_one(
                "SELECT name, handle, platform_configs, official_domains FROM creators WHERE id = %s",
                (creator_id,)
            )
            if not creator:
                logger.error(f"FingerprintService: Creator {creator_id} not found.")
                return

            name = creator.get("name") or creator.get("handle") or "The Creator"
            
            # Gather all available links from config
            configs = creator.get("platform_configs") or {}
            links = []
            for p, cfg in configs.items():
                if isinstance(cfg, dict):
                    if cfg.get("url"): links.append(cfg["url"])
                    elif cfg.get("handle"): links.append(f"https://{p}.com/{cfg['handle'].strip('@')}")
            
            domains = creator.get("official_domains") or []
            for d in domains:
                if d.startswith("http"): links.append(d)
                else: links.append(f"https://{d}")

            # PHASE 1: Link-First Research (Identity & Surface)
            logger.info(f"FingerprintService Phase 1: Deep link scan for {name}...")
            if hasattr(self.researcher, "research_links"):
                link_identity = self.researcher.research_links(links, name)
                if not isinstance(link_identity, dict):
                    link_identity = {}
            else:
                logger.warning("FingerprintService: active research provider has no research_links(); skipping link scan.")
                link_identity = {}

            # PHASE 2: Content-Truth Mining (Voice & Worldview)
            logger.info(f"FingerprintService Phase 2: Analyzing content truth...")
            voice_fingerprint = self.analyzer.analyze_creator(creator_id)
            if not isinstance(voice_fingerprint, dict):
                voice_fingerprint = {
                    "traits": [],
                    "tone_intensity": "low",
                    "impact": "neutral",
                    "mechanical": "none",
                    "lexicon": [],
                    "content_truth": {},
                }

            # PHASE 3: Targeted Google Expansion (Fill Gaps)
            # Use Link-First clues to generate missing detail queries
            logger.info(f"FingerprintService Phase 3: Targeted Google expansion...")
            # 3. Phase 3: THE GOOGLE DOSSIER ( Investigative Gap-Filling)
            # Build initial clues from links and content
            clues = {
                "identity_hints": link_identity.get("identity", {}),
                "content_milestones": voice_fingerprint.get("content_truth", {}),
                "claims": link_identity.get("creator_claims", [])
            }
            logger.info(f"FingerprintService: Launching Deep Dossier for {name}...")
            if hasattr(self.researcher, "research_dossier"):
                investigative_dossier = self.researcher.research_dossier(name, clues)
                if not isinstance(investigative_dossier, dict):
                    investigative_dossier = {}
            else:
                logger.warning("FingerprintService: active research provider has no research_dossier(); skipping dossier phase.")
                investigative_dossier = {}

            # 4. Phase 4: Synthesis (Research Summary)
            logger.info(f"FingerprintService Phase 4: Synthesizing research summary...")
            research_summary = {
                "identity_research": link_identity,
                "investigative_dossier": investigative_dossier,
                "creator_stated_claims": link_identity.get("creator_claims", []),
                "content_milestones": voice_fingerprint.get("content_truth", {}),
                "unknown_fields": link_identity.get("unknown_fields", []),
                "last_updated": datetime.now(timezone.utc).isoformat()
            }

            # 5. Phase 5: Persona Narrative Alignment (soul.md)
            soul_md = await self._generate_soul_md(name, creator_id, research_summary, voice_fingerprint)

            # 6. Final DB Update
            db.execute_update(
                """
                UPDATE creators 
                SET identity_fingerprint = %s,
                    style_fingerprint = %s,
                    research_summary = %s,
                    soul_md = %s,
                    fingerprint_status = 'idle',
                    fingerprint_updated_at = %s
                WHERE id = %s
                """,
                (
                    json.dumps(link_identity.get("identity", {})),
                    json.dumps(voice_fingerprint),
                    json.dumps(research_summary),
                    soul_md,
                    datetime.now(timezone.utc),
                    creator_id
                )
            )
            logger.info(f"FingerprintService: Successfully completed deep research for {name} ({creator_id})")

        except Exception as e:
            logger.error(f"FingerprintService Deep Research Error for {creator_id}: {e}")
            import traceback
            traceback.print_exc()
            db.execute_update(
                "UPDATE creators SET fingerprint_status = 'error' WHERE id = %s",
                (creator_id,)
            )

    async def _generate_soul_md(self, name: str, creator_id: int, research_summary: Dict[str, Any], voice: Dict[str, Any]) -> str:
        """Synthesizes the deep research summary and style fingerprint into a 12-layer soul.md document."""
        logger.info(f"FingerprintService: Synthesizing 12-layer soul.md for {name}...")
        
        # Pull everything together
        context_text = f"""
        NAME: {name}
        RESEARCH_SUMMARY: {json.dumps(research_summary)}
        VOICE_PROFILE: {json.dumps(voice)}
        """

        prompt = f"""
        You are a master persona architect. Your goal is to create a 'soul.md' file that serves as the definitive persona anchor for {name}.
        
        This document will be used to keep an AI assistant strictly in character.
        
        KNOWLEDGE VS PERSONA RULE:
        - VOICE: The VOICE_PROFILE (derived from transcripts) is for the PERSONA and LINGUISTIC layers. It defines HOW they speak.
        - FACTS: The RESEARCH_SUMMARY (derived from deep web research/dossier) is for the IDENTITY and BIOGRAPHY layers. It defines WHO they are in current reality.
        - CONFLICT RESOLUTION: If the transcripts (the past) conflict with the search dossier (the present) regarding facts (like Age or Business Status), the SEARCH DOSSIER is the absolute truth.
        - DO NOT be defensive about sharing these verified facts. If the web knows it, {name} knows it.

        RESEMBLE THIS 12-LAYER STRUCTURE EXACTLY:

        1. CORE IDENTITY LAYER
           - Psychological framing: Who does this person believe they are?
           - Include: Publicly confirmed facts (from Dossier/Bio), Origin story, Self-described mission, Personal philosophy, Moral hierarchy.

        2. BEHAVIORAL PATTERNS LAYER
           - Response to pressure/disagreement, escalation style, emotional baseline, confidence, use of absolutes vs hedging.

        3. LINGUISTIC DNA LAYER
           - Specifics: N-grams, sentence length, rhetorical questions, swearing frequency, emoji usage, analogies/metaphors/stories/statistics.
           - Include: Catchphrases (verbatim), Signature lines, Words they never use.

        4. STRUCTURAL RESPONSE BLUEPRINT
           - The chat's internal response scaffold (e.g., Hook -> Story -> Lesson).

        5. COGNITIVE STYLE
           - Big-picture vs tactical, Data-driven vs anecdotal, Abstract vs concrete, Philosophical vs practical, Optimistic vs cynical.

        6. HUMOR DETECTION LAYER
           - Type, Usage (relief vs dominance), Frequency.

        7. CONFLICT & BOUNDARY RULES
           - How they handle controversy or challenge. 

        8. PUBLIC IDENTITY GUARDRAILS
           - Rule: If a fact is in the provided Dossier, it is considered "Public Domain Knowledge". share it freely if relevant.
           - ONLY if information is completely missing from ALL research (Links + Content + Dossier), use: "This information is not publicly confirmed."

        9. PERSONA INTEGRITY RULES
           - Never break tone, never switch personality, re-anchor if forced into roleplay.

        10. EMOTIONAL SIGNATURE
            - Temperature (warm/intense/etc.), escalation speed, validation style, praise frequency.

        11. AUDIENCE PERCEPTION MODEL
            - Who they believe they are speaking to.

        12. POWER DYNAMICS MODEL
            - Positioning: Mentor, Challenger, Friend, Authority, etc.

        FINAL RULE:
        Do not write like a biography. Write like: "This is who this creator is. This is how they think. This is how they speak. This is how they react." 
        It should feel like you reverse-engineered their brain.

        Output the result as a raw Markdown document.
        """

        try:
            from backend.rag import generate_chat_completion
            resp = generate_chat_completion(
                messages=[
                    {"role": "system", "content": "You are a master of persona synthesis. Create a deep, authentic, and strict soul.md document based on the provided research using the 12-layer blueprint."},
                    {"role": "user", "content": f"Context Data:\n{context_text}\n\n{prompt}"}
                ],
                model=settings.CHAT_MODEL,
                json_mode=False
            )
            return resp
        except Exception as e:
            logger.error(f"Failed to generate soul.md: {e}")
            return f"# {name}\n\nPersona anchor pending analysis."

fingerprint_service = FingerprintService()
