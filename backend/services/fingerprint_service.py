import logging
import json
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from backend.db import db
from backend.personality_analyzer import PersonalityAnalyzer
from backend.services.research_provider import GeminiResearchProvider
from backend.settings import settings
from backend.rag import get_client

logger = logging.getLogger(__name__)


def _flatten_strings(value):
    out = []
    if isinstance(value, str) and value.strip():
        out.append(value.strip())
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_flatten_strings(v))
    elif isinstance(value, list):
        for item in value:
            out.extend(_flatten_strings(item))
    return out


def _dedupe_keep_order(values, limit=None):
    seen = set()
    result = []
    for value in values:
        key = str(value).strip()
        if not key:
            continue
        low = key.lower()
        if low in seen:
            continue
        seen.add(low)
        result.append(key)
        if limit and len(result) >= limit:
            break
    return result


def _build_identity_fingerprint(name: str, link_identity: Dict[str, Any], investigative_dossier: Dict[str, Any], voice_fingerprint: Dict[str, Any]) -> Dict[str, Any]:
    identity = (link_identity or {}).get("identity") or {}
    brand = (link_identity or {}).get("brand") or {}
    platforms = (link_identity or {}).get("platforms") or {}
    biography = (investigative_dossier or {}).get("biography") or {}
    business_evolution = (investigative_dossier or {}).get("business_evolution") or []
    specific_wins = (investigative_dossier or {}).get("specific_wins") or []
    consensus = (investigative_dossier or {}).get("public_consensus_facts") or {}

    bio_bits = []
    early_life = biography.get("early_life")
    location = identity.get("location") or biography.get("birthplace")
    if identity.get("full_name"):
        bio_bits.append(identity.get("full_name"))
    if identity.get("job_titles"):
        bio_bits.append(", ".join(identity.get("job_titles")[:3]))
    if location:
        bio_bits.append(f"based around {location}")
    if early_life:
        bio_bits.append(str(early_life))

    verified_facts = []
    verified_facts.extend(identity.get("verified_background") or [])
    verified_facts.extend(consensus.values() if isinstance(consensus, dict) else [])
    verified_facts.extend((investigative_dossier or {}).get("net_worth_milestones") or [])

    businesses = []
    for item in business_evolution:
        if isinstance(item, dict):
            name_part = item.get("name")
            outcome_part = item.get("outcome")
            if name_part and outcome_part:
                businesses.append(f"{name_part}: {outcome_part}")
            elif name_part:
                businesses.append(name_part)
    businesses.extend((voice_fingerprint.get("content_truth") or {}).get("businesses") or [])

    products = []
    for item in specific_wins:
        if isinstance(item, dict):
            product = item.get("product")
            impact = item.get("revenue_or_impact")
            if product and impact:
                products.append(f"{product}: {impact}")
            elif product:
                products.append(product)
    products.extend((voice_fingerprint.get("content_truth") or {}).get("products") or [])
    products.extend(brand.get("products_services") or [])

    themes = []
    for platform_info in platforms.values() if isinstance(platforms, dict) else []:
        if isinstance(platform_info, dict):
            themes.extend(platform_info.get("themes") or [])
    themes.extend(voice_fingerprint.get("recurring_themes") or [])

    mission = brand.get("mission") or next(iter(voice_fingerprint.get("worldview", {}).get("core_beliefs", []) or []), None)

    return {
        "bio": ". ".join(_dedupe_keep_order(bio_bits, limit=4)) or f"{name} public profile synthesized from ingested content and verified web research.",
        "mission": mission,
        "is_verified": bool(verified_facts or identity.get("verified_background") or consensus),
        "job_titles": _dedupe_keep_order(identity.get("job_titles") or [], limit=5),
        "verified_facts": _dedupe_keep_order(verified_facts, limit=8),
        "businesses": _dedupe_keep_order(businesses, limit=8),
        "products": _dedupe_keep_order(products, limit=8),
        "themes": _dedupe_keep_order(themes, limit=8),
        "affiliations": _dedupe_keep_order((investigative_dossier or {}).get("affiliations") or [], limit=6),
        "controversies": _dedupe_keep_order((investigative_dossier or {}).get("controversies_and_boundaries") or [], limit=6),
        "creator_claims": _dedupe_keep_order((link_identity or {}).get("creator_claims") or [], limit=6),
        "public_consensus": _dedupe_keep_order(_flatten_strings(consensus), limit=8),
    }


def _has_meaningful_dossier(dossier: Dict[str, Any]) -> bool:
    if not isinstance(dossier, dict) or not dossier:
        return False
    for key in (
        "biography",
        "business_evolution",
        "specific_wins",
        "net_worth_milestones",
        "controversies_and_boundaries",
        "affiliations",
        "public_consensus_facts",
    ):
        value = dossier.get(key)
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and any(str(v).strip() for v in value):
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _load_cached_dossier_from_creator(creator_id: int) -> Dict[str, Any]:
    row = db.execute_one("SELECT research_summary FROM creators WHERE id = %s", (creator_id,))
    summary = (row or {}).get("research_summary") or {}
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except Exception:
            summary = {}
    if not isinstance(summary, dict):
        return {}
    dossier = summary.get("investigative_dossier") or {}
    return dossier if _has_meaningful_dossier(dossier) else {}


def _normalize_search_hits(results: List[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    normalized = []
    seen = set()
    for item in results or []:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        snippet = (item.get("snippet") or "").strip()
        key = (url or title).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append({
            "title": title,
            "url": url,
            "snippet": snippet,
            "source": item.get("source"),
        })
        if len(normalized) >= limit:
            break
    return normalized


def _fallback_openai_dossier(
    creator_id: int,
    creator_name: str,
    creator_profile: Dict[str, Any],
    initial_clues: Dict[str, Any],
) -> Dict[str, Any]:
    if not settings.OPENAI_API_KEY:
        return {}

    try:
        from backend.services.research_provider import OpenAIResearchProvider
    except Exception as exc:
        logger.warning(f"FingerprintService: OpenAI dossier fallback unavailable: {exc}")
        return {}

    provider = OpenAIResearchProvider()
    if not getattr(provider, "enabled", False):
        return {}

    queries = [
        f"{creator_name} biography age birthplace background",
        f"{creator_name} business history companies brands",
        f"{creator_name} products programs offers course business",
        f"{creator_name} controversy criticism lawsuit podcast interview",
    ]
    aggregated = []
    for query in queries:
        try:
            aggregated.extend(provider.search(query, creator_profile, resource_type="web"))
        except Exception as exc:
            logger.warning(f"FingerprintService: OpenAI dossier query failed for '{query}': {exc}")

    hits = _normalize_search_hits(aggregated, limit=14)
    if not hits:
        return {}

    client = get_client()
    prompt = f"""
You are building a public-domain investigative dossier for {creator_name}.

Use only the evidence provided below plus the initial clues. Do not invent facts.
If a field is unknown, leave it blank or return an empty list/object.

INITIAL CLUES:
{json.dumps(initial_clues)}

SEARCH EVIDENCE:
{json.dumps(hits)}

Return JSON only:
{{
  "biography": {{
    "age": "...",
    "birthplace": "...",
    "early_life": "summarized",
    "certainty": "low|med|high"
  }},
  "business_evolution": [
    {{
      "name": "...",
      "year": "...",
      "outcome": "...",
      "role": "..."
    }}
  ],
  "specific_wins": [
    {{
      "product": "...",
      "niche": "...",
      "revenue_or_impact": "..."
    }}
  ],
  "net_worth_milestones": ["..."],
  "controversies_and_boundaries": ["..."],
  "affiliations": ["..."],
  "public_consensus_facts": {{
    "fact_name": "value"
  }}
}}
"""
    response = client.chat.completions.create(
        model=settings.MODEL_CLASSIFICATION,
        messages=[
            {"role": "system", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    content = response.choices[0].message.content
    try:
        parsed = json.loads(content)
    except Exception:
        logger.warning("FingerprintService: OpenAI dossier fallback returned invalid JSON.")
        return {}
    return parsed if _has_meaningful_dossier(parsed) else {}

class FingerprintService:
    """
    Orchestrates the Style Fingerprint system:
    1. Public Identity & Background (Biographical research)
    2. Voice & Style Profile (Linguistic analysis)
    """

    def __init__(self):
        from backend.services.research_provider import get_research_provider, GeminiResearchProvider
        # Fingerprint research phases (links + dossier) are implemented on Gemini provider.
        # Prefer Gemini when GOOGLE_API_KEY is present; otherwise fall back to default provider factory.
        self.researcher = GeminiResearchProvider() if settings.GOOGLE_API_KEY else get_research_provider()
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
            investigative_dossier = {}
            research_quality = "full"
            if hasattr(self.researcher, "research_dossier"):
                investigative_dossier = self.researcher.research_dossier(name, clues)
                if not isinstance(investigative_dossier, dict):
                    investigative_dossier = {}
            else:
                logger.warning("FingerprintService: active research provider has no research_dossier(); skipping dossier phase.")

            if not _has_meaningful_dossier(investigative_dossier):
                cached_dossier = _load_cached_dossier_from_creator(creator_id)
                if cached_dossier:
                    logger.info(f"FingerprintService: Reusing cached dossier for {name} ({creator_id}).")
                    investigative_dossier = cached_dossier
                    research_quality = "cached"

            if not _has_meaningful_dossier(investigative_dossier):
                creator_profile = {
                    "id": creator_id,
                    "name": name,
                    "handle": creator.get("handle"),
                    "platform_configs": configs,
                    "official_domains": domains,
                }
                fallback_dossier = _fallback_openai_dossier(creator_id, name, creator_profile, clues)
                if fallback_dossier:
                    logger.info(f"FingerprintService: OpenAI dossier fallback succeeded for {name}.")
                    investigative_dossier = fallback_dossier
                    research_quality = "fallback"

            if not _has_meaningful_dossier(investigative_dossier):
                research_quality = "partial"
                investigative_dossier = {}

            # 4. Phase 4: Synthesis (Research Summary)
            logger.info(f"FingerprintService Phase 4: Synthesizing research summary...")
            research_summary = {
                "identity_research": link_identity,
                "investigative_dossier": investigative_dossier,
                "creator_stated_claims": link_identity.get("creator_claims", []),
                "content_milestones": voice_fingerprint.get("content_truth", {}),
                "unknown_fields": link_identity.get("unknown_fields", []),
                "research_quality": research_quality,
                "last_updated": datetime.now(timezone.utc).isoformat()
            }

            # 5. Build a richer identity layer from web + content.
            identity_fingerprint = _build_identity_fingerprint(name, link_identity, investigative_dossier, voice_fingerprint)

            # 6. Phase 5: Persona Narrative Alignment (soul.md)
            soul_md = await self._generate_soul_md(name, creator_id, research_summary, voice_fingerprint, voice_fingerprint)

            # 7. Final DB Update
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
                    json.dumps(identity_fingerprint),
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

    async def _generate_soul_md(self, name: str, creator_id: int, research_summary: Dict[str, Any], voice: Dict[str, Any], style_fingerprint: Dict[str, Any]) -> str:
        """Synthesizes the deep research summary and style fingerprint into an 18-section soul.md document."""
        logger.info(f"FingerprintService: Synthesizing 18-section soul.md for {name}...")

        context_text = f"""
        NAME: {name}
        RESEARCH_SUMMARY: {json.dumps(research_summary)}
        VOICE_PROFILE: {json.dumps(voice)}
        STYLE_FINGERPRINT_V2: {json.dumps(style_fingerprint)}
        """

        prompt = f"""
        You are a master persona architect. Your goal is to create a 'soul.md' file that serves as the definitive persona anchor for {name}.

        This document will be used to keep an AI assistant strictly in character.

        KNOWLEDGE VS PERSONA RULE:
        - VOICE_PROFILE defines HOW they sound.
        - STYLE_FINGERPRINT_V2 defines what makes them DISTINCT from other creators.
        - RESEARCH_SUMMARY defines WHO they are in current reality.
        - CONFLICT RESOLUTION: if transcripts conflict with the search dossier on facts, the search dossier is the truth.
        - DIFFERENTIAL PRIORITY: make the creator feel uniquely identifiable, not just well-described.

        WRITE THESE 18 SECTIONS EXACTLY:
        1. CORE IDENTITY LAYER
        2. BEHAVIORAL PATTERNS LAYER
        3. LINGUISTIC DNA LAYER
        4. STRUCTURAL RESPONSE BLUEPRINT
        5. COGNITIVE STYLE
        6. HUMOR DETECTION LAYER
        7. CONFLICT & BOUNDARY RULES
        8. PUBLIC IDENTITY GUARDRAILS
        9. PERSONA INTEGRITY RULES
        10. EMOTIONAL SIGNATURE
        11. AUDIENCE PERCEPTION MODEL
        12. POWER DYNAMICS MODEL
        13. DIFFERENTIAL DNA
        14. ANTI-PERSONA RULES
        15. MODE MATRIX
        16. PRESSURE / STRESS BEHAVIOR
        17. DISTINGUISHING TELLS
        18. GOLDEN REPLIES

        SECTION REQUIREMENTS:
        - Sections 13-18 must draw heavily from STYLE_FINGERPRINT_V2.
        - DIFFERENTIAL DNA: what makes {name} unlike adjacent creators in the same niche.
        - ANTI-PERSONA RULES: what would make {name} sound fake, generic, or like somebody else.
        - MODE MATRIX: how they behave in greeting, teaching, comfort, rebuke, story, sales, uncertainty, and boundary mode.
        - PRESSURE / STRESS BEHAVIOR: how their voice changes when challenged, when the user is ashamed, when they need to convict, and when they need to protect privacy.
        - DISTINGUISHING TELLS: specific worldview, cadence, and analogy patterns that should appear naturally.
        - GOLDEN REPLIES: 1-2 short example replies for greeting, comfort, rebuke, teaching, boundary, and uncertainty.

        FINAL RULE:
        Do not write like a biography. Write like: 'This is who this creator is. This is how they think. This is how they speak. This is how they react.'
        It should feel like you reverse-engineered their brain.

        Output the result as a raw Markdown document.
        """

        try:
            from backend.rag import generate_chat_completion
            resp = generate_chat_completion(
                messages=[
                    {"role": "system", "content": "You are a master of persona synthesis. Create a deep, authentic, and strict soul.md document based on the provided research using the 18-section differential persona blueprint."},
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
