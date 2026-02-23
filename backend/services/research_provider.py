import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
import requests
from db import db
from settings import settings

logger = logging.getLogger(__name__)

class ResearchProvider(ABC):
    @abstractmethod
    def search(self, query: str, creator_profile: Dict[str, Any], resource_type: str = "any", conversation_history: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, Any]]:
        pass

    def _get_cache(self, creator_id: int, query: str, provider_name: str, cache_salt: str = "") -> Optional[List[Dict[str, Any]]]:
        combined = f"{query.lower().strip()}:{cache_salt}"
        query_hash = hashlib.sha256(combined.encode()).hexdigest()
        sql = """
            SELECT results FROM search_cache 
            WHERE creator_id = %s AND query_hash = %s AND provider = %s
            AND created_at > %s
        """
        expiry = datetime.now(timezone.utc) - timedelta(hours=6)
        try:
            row = db.execute_one(sql, (creator_id, query_hash, provider_name, expiry))
            return row['results'] if row else None
        except Exception as e:
            logger.error(f"ResearchProvider: Cache read error: {e}")
            return None

    def _save_cache(self, creator_id: int, query: str, provider_name: str, results: List[Dict[str, Any]], cache_salt: str = ""):
        combined = f"{query.lower().strip()}:{cache_salt}"
        query_hash = hashlib.sha256(combined.encode()).hexdigest()
        sql = """
            INSERT INTO search_cache (creator_id, query_hash, provider, results, created_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (creator_id, query_hash, provider) 
            DO UPDATE SET results = EXCLUDED.results, created_at = now()
        """
        try:
            db.execute_update(sql, (creator_id, query_hash, provider_name, json.dumps(results)))
        except Exception as e:
            logger.error(f"ResearchProvider: Cache write error: {e}")

class GeminiResearchProvider(ResearchProvider):
    def __init__(self):
        self.enabled = bool(settings.GOOGLE_API_KEY)
        self.api_key = settings.GOOGLE_API_KEY
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

    def _call_gemini_rest(self, prompt: str, search_enabled: bool = True) -> Optional[str]:
        if not self.enabled: return None
        
        url = f"{self.base_url}?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}] if search_enabled else []
        }
        
        import time
        for attempt in range(2): # Only 1 retry for real-time latency
            try:
                response = requests.post(url, json=payload, timeout=15) # Increased timeout for Google Search
                if response.status_code == 429:
                    wait = 2 # Short wait for rate limit
                    logger.warning(f"GeminiResearch: 429 Rate Limit. Waiting {wait}s... (Attempt {attempt+1}/2)")
                    time.sleep(wait)
                    continue
                    
                if response.status_code != 200:
                    logger.error(f"GeminiResearch REST Error {response.status_code}: {response.text}")
                    return None
                
                data = response.json()
                if "candidates" in data and data["candidates"]:
                    parts = data["candidates"][0].get("content", {}).get("parts", [])
                    
                    # Combine all text parts
                    full_text = ""
                    for p in parts:
                        if "text" in p:
                            full_text += p["text"]
                    
                    if full_text:
                        return full_text
                return None
            except Exception as e:
                logger.error(f"GeminiResearch REST Exception: {e}")
                if attempt < 2:
                    time.sleep(2)
                    continue
                return None
        return None

    def search(
        self, 
        query: str, 
        creator_profile: Dict[str, Any], 
        resource_type: str = "any", 
        conversation_history: Optional[List[Dict[str, str]]] = None,
        intent_metadata: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
            
        creator_id = creator_profile['id']
        
        # Consistently extract seen titles using alpha-numeric normalization
        seen_titles_ultra = []
        if conversation_history:
            for m in conversation_history[-15:]:
                content = (m.get("content") or m.get("text") or "").lower()
                # Extract quoted titles
                quoted = re.findall(r'"([^"]+)"', content)
                # Catch "watch [title]" patterns
                natural = re.findall(r'(?:watch|check out|video|resource|lesson)\s+([\w\s\-\(\):]+)', content)
                for t in quoted + natural:
                    norm = re.sub(r'[^a-z0-9]', '', t)
                    if len(norm) > 6: seen_titles_ultra.append(norm)
        
        seen_titles_ultra = list(set(seen_titles_ultra))
        cache_salt = ",".join(sorted(seen_titles_ultra))
        
        # Check cache once with clean salt
        cached = self._get_cache(creator_id, query, "gemini", cache_salt=cache_salt)
        if cached:
            logger.info(f"GeminiResearch: Cache hit for '{query}' (seen={len(seen_titles_ultra)})")
            return cached

        yt_handle = creator_profile.get('youtube_handle')
        yt_id = creator_profile.get('youtube_channel_id')
        
        # Fallback to platform_configs if primary columns are empty
        configs = creator_profile.get('platform_configs') or {}
        yt_config = configs.get('youtube', {})
        if not yt_handle:
            yt_handle = yt_config.get('handle') or yt_config.get('username')
        if not yt_id:
            yt_id = yt_config.get('channel_id') or yt_config.get('id')

        domains = creator_profile.get('official_domains') or []
        search_targets = []
        if yt_handle: search_targets.append(f"YouTube @{yt_handle.strip('@')}")
        if yt_id: search_targets.append(f"YouTube channel {yt_id}")
        if domains: search_targets.append(f"Official domains: {', '.join(domains)}")
        target_str = " AND ".join(search_targets)

        logger.info(f"GeminiResearch: Searching for '{query}' (Excluding {len(seen_titles_ultra)} previous titles)")
        
        exclude_instruction = ""
        if seen_titles_ultra:
            # We don't send the scrambled alpha-numeric to the LLM, we send the original if we can, 
            # but for exclusions, the LLM is smart enough if we provide a few readable examples.
            # For now, we'll extract readable seen titles for the prompt but use IDs/Norm for the actual filter.
            readable_seen = []
            for m in conversation_history[-10:]:
                readable_seen.extend(re.findall(r'"([^"]+)"', m.get("content","")+m.get("text","")))
            
            if readable_seen:
                titles_str = ", ".join([f"'{t[:40]}'" for t in list(set(readable_seen))[:5]])
                exclude_instruction = f"5. EXCLUDE these already recommended resources (STRICT): {titles_str}. Do NOT return them."

        # Prepare specific guidance from intent metadata
        user_level = (intent_metadata or {}).get("user_level", "unknown")
        learning_phase = (intent_metadata or {}).get("learning_phase", "overview")
        thematic_keywords = (intent_metadata or {}).get("topic_depth", "")

        prompt = f"""

Find as many UNIQUE {creator_profile.get('name')} resources (videos, articles, or lessons) as possible that relate to the intent: "{query}".

YOU ARE A CHANNEL EXPLORER & RESEARCH AGENT.
Your goal is to provide deep and varied content from this channel: {target_str}.

User Context: {user_level} level, currently in the '{learning_phase}' phase.
Thematic guidance: {thematic_keywords}

CRITICAL CONSTRAINTS:
1. PRIMARY TARGET: ONLY return content OWNED by this creator on their official channels ({target_str}).
2. GUEST/COLLAB (SECOND PRIORITY): If and ONLY if you cannot find enough specific content on the main channel, you may include high-quality appearances by {creator_profile.get('name')} on other channels (e.g., podcasts, interviews, collaborations). 
3. DO NOT return random videos from other creators that just mention the name. The creator MUST be the primary speaker/teacher in the resource.
4. CONTENT > TITLES: Find videos where the creator TEACHES this topic, even if the title is generic.
5. {exclude_instruction}
6. NO REPEATS: You MUST return DIFFERENT content from the archives if the user is asking for more.

Output a JSON array of objects (limit to top 40 unique fits):
[
  {{
    "title": "Exact Title",
    "url": "Full URL",
    "snippet": "Specifically WHY this video is the best fit for a {user_level} trader interested in {query}. Mention what they will learn.",
    "resource_type": "video" | "article" | "course_lesson",
    "is_playlist": boolean,
    "series_index": number (optional),
    "confidence": 0.0-1.0
  }}
]
Respond with JSON ONLY.
"""
        
        text = self._call_gemini_rest(prompt)
        if not text:
            return []

    def _parse_json(self, text: str) -> Any:
        try:
            # Clean markdown formatting
            cleaned = text.strip()
            if cleaned.startswith("```"):
                # Remove ```json or ``` at start and ``` at end
                cleaned = re.sub(r'^```(?:json)?\n?', '', cleaned)
                cleaned = re.sub(r'\n?```$', '', cleaned)
            
            # Simple fallback for missing commas or other minor issues 
            # (In a real app, you might use 'dirtyjson' or similar)
            return json.loads(cleaned)
        except Exception:
            # Try regex extraction if direct parse fails
            try:
                json_match = re.search(r'\[\s*\{.*\}\s*\]|\{.*\}', cleaned, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group(0))
            except Exception as e:
                logger.error(f"GeminiResearch: Failed to parse JSON even after cleanup: {e}")
            return None

    def search(
        self, 
        query: str, 
        creator_profile: Dict[str, Any], 
        resource_type: str = "any", 
        conversation_history: Optional[List[Dict[str, str]]] = None,
        intent_metadata: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
            
        creator_id = creator_profile['id']
        
        # Consistently extract seen titles using alpha-numeric normalization
        seen_titles_ultra = []
        if conversation_history:
            for m in conversation_history[-15:]:
                content = (m.get("content") or m.get("text") or "").lower()
                # Extract quoted titles
                quoted = re.findall(r'"([^"]+)"', content)
                # Catch "watch [title]" patterns
                natural = re.findall(r'(?:watch|check out|video|resource|lesson)\s+([\w\s\-\(\):]+)', content)
                for t in quoted + natural:
                    norm = re.sub(r'[^a-z0-9]', '', t)
                    if len(norm) > 6: seen_titles_ultra.append(norm)
        
        seen_titles_ultra = list(set(seen_titles_ultra))
        cache_salt = ",".join(sorted(seen_titles_ultra))
        
        # Check cache once with clean salt
        cached = self._get_cache(creator_id, query, "gemini", cache_salt=cache_salt)
        if cached:
            logger.info(f"GeminiResearch: Cache hit for '{query}' (seen={len(seen_titles_ultra)})")
            return cached

        yt_handle = creator_profile.get('youtube_handle')
        yt_id = creator_profile.get('youtube_channel_id')
        
        # Fallback to platform_configs if primary columns are empty
        configs = creator_profile.get('platform_configs') or {}
        yt_config = configs.get('youtube', {})
        if not yt_handle:
            yt_handle = yt_config.get('handle') or yt_config.get('username')
        if not yt_id:
            yt_id = yt_config.get('channel_id') or yt_config.get('id')
        if not yt_handle and not yt_id:
            # Only use platforms if no youtube-specific ID found
            platforms = creator_profile.get('platforms') or []
            yt_p = next((p for p in platforms if p.get('platform') == 'youtube'), {})
            yt_handle = yt_p.get('handle')
            yt_id = yt_p.get('channel_id')

        domains = creator_profile.get('official_domains') or []
        search_targets = []
        if yt_handle: search_targets.append(f"YouTube @{yt_handle.strip('@')}")
        if yt_id: search_targets.append(f"YouTube channel {yt_id}")
        if domains: search_targets.append(f"Official domains: {', '.join(domains)}")
        target_str = " AND ".join(search_targets)

        logger.info(f"GeminiResearch: Searching for '{query}' (Excluding {len(seen_titles_ultra)} previous titles)")
        
        exclude_instruction = ""
        if seen_titles_ultra:
            readable_seen = []
            for m in conversation_history[-10:]:
                readable_seen.extend(re.findall(r'"([^"]+)"', m.get("content","")+m.get("text","")))
            
            if readable_seen:
                titles_str = ", ".join([f"'{t[:40]}'" for t in list(set(readable_seen))[:5]])
                exclude_instruction = f"5. EXCLUDE these already recommended resources (STRICT): {titles_str}. Do NOT return them."

        # Prepare specific guidance from intent metadata
        user_level = (intent_metadata or {}).get("user_level", "unknown")
        learning_phase = (intent_metadata or {}).get("learning_phase", "overview")
        thematic_keywords = (intent_metadata or {}).get("topic_depth", "")

        history_context = ""
        if conversation_history:
            recent_msgs = [f"{m.get('role', 'user').upper()}: {m.get('content', m.get('text', ''))}" for m in conversation_history[-4:]]
            history_context = (
                "Recent Conversation Context:\n" + "\n".join(recent_msgs) + 
                "\n\nCRITICAL CONTEXT RULE: If the user's intent is ambiguous (e.g. 'do you have a link', 'what was that video'), "
                "use the Recent Conversation Context to determine exactly WHICH video or topic they are asking about, and search for that specific video."
            )

        prompt = f"""
Find as many UNIQUE {creator_profile.get('name')} resources (videos, articles, or lessons) as possible that relate to the intent: "{query}".

{history_context}

YOU ARE A CHANNEL EXPLORER & RESEARCH AGENT.
Your goal is to provide deep and varied content from this channel: {target_str}.

User Context: {user_level} level, currently in the '{learning_phase}' phase.
Thematic guidance: {thematic_keywords}

CRITICAL CONSTRAINTS:
1. PRIMARY TARGET: ONLY return content OWNED by this creator on their official channels ({target_str}).
2. GUEST/COLLAB (SECOND PRIORITY): If and ONLY if you cannot find enough specific content on the main channel, you may include high-quality appearances by {creator_profile.get('name')} on other channels (e.g., podcasts, interviews, collaborations). 
3. DO NOT return random videos from other creators that just mention the name. The creator MUST be the primary speaker/teacher in the resource.
4. CONTENT > TITLES: Find videos where the creator TEACHES this topic, even if the title is generic.
5. {exclude_instruction}
6. NO REPEATS: You MUST return DIFFERENT content from the archives if the user is asking for more.

Output a JSON array of objects (limit to top 40 unique fits).
Respond with JSON ONLY. Do not add conversational text.

[
  {{
    "title": "Exact Title",
    "url": "Full URL",
    "snippet": "Specifically WHY this video is the best fit for a {user_level} trader interested in {query}. Mention what they will learn.",
    "resource_type": "video" | "article" | "course_lesson",
    "is_playlist": boolean,
    "confidence": 0.0-1.0
  }}
]
"""
        
        text = self._call_gemini_rest(prompt)
        if not text:
            return []

        candidates = self._parse_json(text)
        if not candidates or not isinstance(candidates, list):
            return []

        verified = self._enforce_cog(candidates, creator_profile)
        accepted_count = len(verified)
        logger.info(f"GeminiResearch Results: Query='{query}', Accepted={accepted_count}")
        
        self._save_cache(creator_id, query, "gemini", verified, cache_salt=cache_salt)
        return verified

    def _enforce_cog(self, candidates: List[Dict[str, Any]], creator_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Same as before, logic is solid
        yt_id = (creator_profile.get('youtube_channel_id') or "").lower()
        yt_handle = (creator_profile.get('youtube_handle') or "").lower().strip("@")
        
        configs = creator_profile.get('platform_configs') or {}
        yt_config = configs.get('youtube', {})
        if not yt_handle:
            yt_handle = (yt_config.get('handle') or yt_config.get('username') or "").lower().strip("@")
        if not yt_id:
            yt_id = (yt_config.get('channel_id') or yt_config.get('id') or "").lower()

        official_domains = [d.lower() for d in (creator_profile.get('official_domains') or [])]
        course_base_urls = [u.lower() for u in (creator_profile.get('course_base_urls') or [])]
        creator_name = (creator_profile.get('name') or "").lower()
        
        verified = []
        collab_markers = ["interview", "podcast", "guest", "featuring", "presents", "collab", "conversation", "mentorship"]

        for c in candidates:
            url = c.get('url', "").lower()
            if not url: continue
            
            relation = "OTHER"
            score = 0.0
            
            is_self = False
            if "youtube.com" in url or "youtu.be" in url:
                if yt_id and yt_id in url: is_self = True
                elif yt_handle and (f"@{yt_handle}" in url or f"/{yt_handle}" in url): is_self = True
            
            domain = urlparse(url).netloc.lower()
            if any(d in domain for d in official_domains): is_self = True
            if any(url.startswith(u) for u in course_base_urls): is_self = True

            if is_self:
                relation = "SELF"
                score = 1.0
            else:
                title = c.get('title', '').lower()
                snippet = c.get('snippet', '').lower()
                has_name = creator_name and creator_name in title
                has_marker = any(m in title or m in snippet for m in collab_markers)
                
                if has_name and has_marker:
                    relation = "AFFILIATED"
                    score = 0.8
                elif has_name:
                    relation = "OTHER"
                    score = 0.3
            
            if relation in ("SELF", "AFFILIATED"):
                c['relation'] = relation
                c['ownership_score'] = score
                c['confidence'] = min(1.0, c.get('confidence', 0.5) * score)
                verified.append(c)
        
        verified.sort(key=lambda x: (x['relation'] == 'SELF', x['confidence']), reverse=True)
        return verified

    def search_general(self, query: str, creator_id: int, creator_profile: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if not self.enabled: return []
        
        cached = self._get_cache(creator_id, query, "gemini_general")
        if cached: return cached

        # Incorporate social links for grounding if available
        grounding_context = ""
        if creator_profile:
            links = []
            if creator_profile.get('youtube_handle'): links.append(f"YouTube: @{creator_profile['youtube_handle']}")
            if creator_profile.get('official_domains'): links.append(f"Official Website: {', '.join(creator_profile['official_domains'])}")
            
            # Extract from platform_configs if available
            p_configs = creator_profile.get('platform_configs') or {}
            for platform, cfg in p_configs.items():
                if isinstance(cfg, dict) and (cfg.get('url') or cfg.get('handle')):
                    links.append(f"{platform.capitalize()}: {cfg.get('url') or cfg.get('handle')}")
            
            if links:
                grounding_context = "\nPRIMARY SOURCES TO VERIFY AGAINST:\n" + "\n".join([f"- {l}" for l in links])

        prompt = f"""
Answer the general query: "{query}".
{grounding_context}

Find reliable public information. 
STRICT RULE: Do not guess. If something is not confirmed in the primary sources or high-authority public records, mark it as 'unknown'.
        
Output a JSON array of objects. 
Respond ONLY with the JSON array. Do not add intro or outro.

[
  {{
    "title": "Source title",
    "url": "URL",
    "snippet": "Key information",
    "is_public_info": true
  }}
]
"""
        text = self._call_gemini_rest(prompt, search_enabled=True)
        if not text: return []

        results = self._parse_json(text)
        if not results or not isinstance(results, list):
            return []

        self._save_cache(creator_id, query, "gemini_general", results)
        return results

    def research_links(self, links: List[str], creator_name: str) -> Dict[str, Any]:
        """Phase 1: Deep scan of provided social/web links."""
        if not self.enabled or not links:
            return {}

        logger.info(f"GeminiResearch: Performing deep link scan for {creator_name} ({len(links)} links)")
        
        prompt = f"""
        YOU ARE A DIGITAL INVESTIGATOR. 
        Your goal is to extract every possible public detail about the creator "{creator_name}" from these links:
        {', '.join(links)}

        For each link, scan the "profile surfaces":
        1. Bio/Description
        2. Display names/Handles
        3. External links (link-in-bio, website, merch)
        4. Career clues (job titles, companies)
        5. Location (if public)
        6. Brand language (taglines, slogans)
        7. Pinned/Featured content themes
        8. Education/Background mentioned

        STRICT RULES:
        - Do not guess personal PII.
        - Separate "verified facts" (confirmed background) from "claims" (what the creator says).
        - Explicitly mark anything you search for but can't find as "unknown".
        - AVOID SENSITIVE PII (No private addresses, personal phones).

        Output a JSON object:
        {{
            "identity": {{
                "full_name": "...",
                "job_titles": ["..."],
                "location": "...",
                "verified_background": ["fact1", "fact2"]
            }},
            "brand": {{
                "taglines": ["..."],
                "mission": "...",
                "products_services": ["..."]
            }},
            "platforms": {{
                "platform_name": {{
                    "bio": "...",
                    "handle": "...",
                    "themes": ["..."]
                }}
            }},
            "creator_claims": ["..."],
            "unknown_fields": ["..."]
        }}
        """
        
        text = self._call_gemini_rest(prompt, search_enabled=True)
        if not text:
            return {}

        return self._parse_json(text) or {}

    def research_dossier(self, creator_name: str, initial_clues: Dict[str, Any]) -> Dict[str, Any]:
        """Phase 3 Upgrade: Investigative dossier using Google Search consensus."""
        if not self.enabled:
            return {}

        logger.info(f"GeminiResearch: Building investigative dossier for {creator_name}...")
        
        # Prepare the investigative context
        clues_str = json.dumps(initial_clues)
        current_date = datetime.now().strftime("%B %d, %Y")
        
        prompt = f"""
        YOU ARE A HIGH-LEVEL INVESTIGATIVE JOURNALIST. 
        TODAY'S DATE IS {current_date}.
        Build a complete "Public Domain Dossier" for the creator: "{creator_name}".
        
        USE YOUR GOOGLE SEARCH TOOL TO INVESTIGATE THE FOLLOWING VECTORS:
        1. BIOGRAPHICAL: Age, birthplace, early education, family background (if public).
        2. BUSINESS HISTORY: All companies founded. Find SPECIFIC BRAND NAMES of their stores (e.g., "Sleep Band", "Pluto Deals").
        3. WINNING PRODUCTS: The exact names of their "winners" or most viral products.
        4. WEALTH & EXITS: Publicly reported net worth, specific business sale/exit prices (e.g., "sold to AutoDS for X million").
        5. CONTROVERSIES: Any public drama, lawsuits, or significant criticisms (for persona boundary setting).
        6. KEY RELATIONSHIPS: Business partners, mentors, or high-profile friendships.

        INITIAL CLUES TO START FROM:
        {clues_str}

        STRICT RULES:
        - RELAXED CONSTRAINTS FOR PUBLIC FACTS: If a fact (like age or first business name) is discussed on podcasts, in interviews, or documented on the web, INCLUDE IT. 
        - DO NOT be overly defensive about "sharing private info" if the info is already a matter of public record.
        - Mark the "CONCORDANCE": For each fact, indicate if it's "High Certainty" (multiple sources) or "Web Consensus" (reported but unverified).
        - AVOID SENSITIVE PII: No home addresses or personal phone numbers.

        Output a JSON object:
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
            "affiliations": ["partners/mentors"],
            "public_consensus_facts": {{
                "fact_name": "value"
            }}
        }}
        """
        
        text = self._call_gemini_rest(prompt, search_enabled=True)
        if not text:
            logger.warning(f"GeminiResearch: Dossier returned NO TEXT for {creator_name}")
            return {}

        results = self._parse_json(text) or {}
        logger.info(f"GeminiResearch: Dossier synthesized for {creator_name}. Keys found: {list(results.keys())}")
        return results
