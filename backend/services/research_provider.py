import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
import requests
from backend.db import db
from backend.settings import settings
import backend.rag as rag

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
            DO UPDATE SET 
                results = EXCLUDED.results,
                created_at = now()
        """
        try:
            db.execute_update(sql, (creator_id, query_hash, provider_name, json.dumps(results)))
        except Exception as e:
            logger.error(f"ResearchProvider: Cache write error: {e}")

    def _parse_json(self, text: str) -> Any:
        try:
            # First, try to extract directly via regex to ignore surrounding text
            # Matches: an array with objects, an empty array [], or a single object {}
            json_match = re.search(r'\[\s*\{.*\}\s*\]|\[\s*\]|\{.*\}', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
            
            # Fallback to aggressive markdown stripping if regex fails
            cleaned = text.strip()
            cleaned = re.sub(r'^```(?:json)?\n?', '', cleaned)
            cleaned = re.sub(r'\n?```$', '', cleaned)
            return json.loads(cleaned)
        except Exception as e:
            logger.warning(f"ResearchProvider: Failed to parse JSON: {e}")
            return None

    def _enforce_cog(self, candidates: List[Dict[str, Any]], creator_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
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
        creator_name = (creator_profile.get('name') or '').strip().lower()
        if not creator_name:
            creator_name = (creator_profile.get('handle') or '').strip().lstrip('@').replace('_', ' ').lower()
        
        verified = []
        collab_markers = ["interview", "podcast", "guest", "featuring", "presents", "collab", "conversation", "mentorship"]

        for c in candidates:
            url = c.get('url', "").lower()
            if not url: continue
            
            relation = "OTHER"
            score = 0.0
            
            # PHASE 1: Direct Ownership Check (URL/Domain)
            is_self = False
            if "youtube.com" in url or "youtu.be" in url:
                if yt_id and yt_id in url: is_self = True
                elif yt_handle and (f"@{yt_handle}" in url or f"/{yt_handle}" in url): is_self = True
            
            domain = urlparse(url).netloc.lower()
            if any(d in domain for d in official_domains): is_self = True
            if any(url.startswith(u) for u in course_base_urls): is_self = True

            # PHASE 2: Indirect Verification (Source/Channel Name)
            source = c.get('source', '').lower()
            if not is_self and source and creator_name:
                if creator_name in source: is_self = True

            if is_self:
                relation = "SELF"
                score = 1.0
            else:
                title = c.get('title', '').lower()
                snippet = c.get('snippet', '').lower()
                has_name = creator_name and creator_name in title
                has_marker = any(m in title or m in snippet for m in collab_markers)
                
                # Check if LLM already verified it as PUBLIC_FACTS
                llm_relation = c.get('relation', '').upper()
                
                if llm_relation == "PUBLIC_FACTS" and c.get('confidence', 0) >= 0.5:
                    relation = "AFFILIATED" # Map to AFFILIATED to pass filter
                    score = 0.7
                elif has_name and has_marker:
                    relation = "AFFILIATED"
                    score = 0.8
                elif has_name:
                    relation = "AFFILIATED" # Fallback if we know it's them but can't verify channel ID
                    score = 0.75 # Boost from 0.6 to pass thresholds more easily
                else:
                    relation = "OTHER"
                    score = 0.1
            
            if relation in ("SELF", "AFFILIATED"):
                c['relation'] = relation
                c['ownership_score'] = score
                c['confidence'] = min(1.0, c.get('confidence', 0.5) * score)
                logger.info(f"ResearchProvider: Accepted candidate '{c.get('title')}' as {relation} (score={score})")
                verified.append(c)
        
        verified.sort(key=lambda x: (x['relation'] == 'SELF', x['confidence']), reverse=True)
        return verified

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
        """
        Legacy research mode: Uses Gemini to synthesize search results.
        """
        if not self.enabled:
            return []

        creator_name = creator_profile.get('name', 'The Creator')
        creator_id = creator_profile.get('id')
        
        # Consistent exclusion logic
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

        # Temporal Context for Gemini
        now = datetime.now(timezone.utc)
        time_context = f"TODAY'S DATE: {now.strftime('%Y-%m-%d')}. CURRENT TIME: {now.strftime('%H:%M:%S')} UTC."

        prompt = f"""
{time_context}
You are an expert Research Assistant for {creator_name}. 
Your goal is to find UNIQUE resources (videos, articles, or course lessons) that help answer: "{query}"

CRITICAL CONSTRAINTS:
1. ONLY return content OWNED by {creator_name} (from their YouTube, Site, or course).
2. DO NOT return random content from other creators.
3. EXCLUDE these already shown titles: {seen_titles_ultra}
4. Provide a helpful 'snippet' explaining why this specific result helps answer the user's intent.

SEARCH FILTER:
If you use Google Search, try to isolate {creator_name}'s content:
- site:youtube.com "@{yt_handle or creator_name}"
- site:{creator_profile.get('official_domains', ['-'])[0]}

Output a JSON array of objects:
[
  {{
    "title": "Exact Title",
    "url": "Full URL",
    "snippet": "Specifically WHY this video/link is a good fit.",
    "resource_type": "video" | "article" | "course_lesson",
    "confidence": 0.0-1.0
  }}
]
Respond with JSON ONLY.
"""
        
        text = self._call_gemini_rest(prompt, search_enabled=True)
        if not text:
            return []
            
        candidates = self._parse_json(text)
        if not candidates or not isinstance(candidates, list):
            return []
            
        # 1. Unwrap Wrapper URLs (e.g. glasp.co/youtube/VIDEO_ID)
        for c in candidates:
            url = c.get("url", "")
            match = re.search(r'glasp\.co/youtube/([\w-]+)', url)
            if match:
                c["url"] = f"https://www.youtube.com/watch?v={match.group(1)}"
        
        verified = self._enforce_cog(candidates, creator_profile)
        
        # 2. Enforce STRICT_SELF if intent asks for video recommendations
        intent = (intent_metadata or {}).get("intent", "").upper()
        if "VIDEO" in intent or intent == "RECOMMEND":
            pc = creator_profile.get("platform_configs", {})
            yt_conf = pc.get("youtube", {}).get("social_confidence", 0.0)
            
            # Require high confidence identity to avoid false attribution
            if yt_conf >= 0.85:
                # Strip all AFFILIATED or OTHER content, strictly ensuring we own the video
                verified = [v for v in verified if v.get("relation") == "SELF" and ("youtube.com/watch?v=" in v.get("url", "") or "youtu.be/" in v.get("url", ""))]
            else:
                verified = [] # Fail closed
                
        self._save_cache(creator_id, query, "gemini", verified, cache_salt=cache_salt)
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

class SerpApiResearchProvider(ResearchProvider):
    def __init__(self):
        self.api_key = settings.SEARCH_API_KEY
        self.enabled = bool(self.api_key)
        self.base_url = "https://serpapi.com/search"

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
        creator_name = creator_profile.get('name', 'Creator')
        
        # Consistent exclusion logic
        seen_titles_ultra = []
        if conversation_history:
            for m in conversation_history[-15:]:
                content = (m.get("content") or m.get("text") or "").lower()
                quoted = re.findall(r'"([^"]+)"', content)
                natural = re.findall(r'(?:watch|check out|video|resource|lesson)\s+([\w\s\-\(\):]+)', content)
                for t in quoted + natural:
                    norm = re.sub(r'[^a-z0-9]', '', t)
                    if len(norm) > 6: seen_titles_ultra.append(norm)
        
        seen_titles_ultra = list(set(seen_titles_ultra))
        cache_salt = ",".join(sorted(seen_titles_ultra))
        
        cached = self._get_cache(creator_id, query, "serpapi", cache_salt=cache_salt)
        if cached:
            logger.info(f"SerpApiResearch: Cache hit for '{query}'")
            return cached

        # Prepare Search Query
        name = creator_profile.get('name', '')
        yt_handle = creator_profile.get('youtube_handle', '').strip('@')
        domains = creator_profile.get('official_domains') or []
        
        # Broaden the query to ensure we get results
        search_query = f"{name} {query}"
        if yt_handle:
            search_query += f" {yt_handle}"
        
        # We'll let GPT-5.2 filter the results rather than being too restrictive in the search query itself
        
        logger.info(f"SerpApiResearch: Searching Google for '{search_query}'")
        
        params = {
            "q": search_query,
            "api_key": self.api_key,
            "engine": "google",
            "num": 20
        }
        
        try:
            response = requests.get(self.base_url, params=params, timeout=15)
            logger.info(f"SerpApi Status: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"SerpApi Error {response.status_code}: {response.text}")
                return []
            
            data = response.json()
            logger.info(f"SerpApi Data Keys: {list(data.keys())}")
            organic_results = data.get("organic_results", [])
            logger.info(f"SerpApi: Found {len(organic_results)} organic results")
            
            if not organic_results:
                logger.warning(f"SerpApi: No organic results found for '{search_query}'. Full Data: {json.dumps(data)[:500]}...")
                return []
                
        except Exception as e:
            logger.error(f"SerpApi Exception: {e}")
            return []

        # Synthesis with GPT-5.2 (MODEL_VERIFY)
        logger.info(f"SerpApiResearch: Synthesizing {len(organic_results)} results with {settings.MODEL_VERIFY}")
        
        results_text = ""
        for i, r in enumerate(organic_results[:10]):
            results_text += f"[{i}] TITLE: {r.get('title')}\nURL: {r.get('link')}\nSOURCE: {r.get('source')}\nSNIPPET: {r.get('snippet')}\n\n"

        prompt = f"""
SEARCH RESULTS:
{results_text}

Synthesize them into a structured JSON array of resources.

CRITICAL CATEGORIES:
1. SELF/OWNED: Content owned by {creator_name} (YouTube channel, official site, courses).
2. AFFILIATED: High-quality appearances (podcasts, interviews, guest features).
3. PUBLIC_FACTS: For general knowledge queries (e.g. market prices, news, definitions) where the user isn't strictly asking about {creator_name}'s opinion.

CRITICAL CONSTRAINTS:
1. PRIORITIZE {creator_name}'s owned content if it exists for the query.
2. If the user is asking a GENERAL question (like "price of ETH"), provide the most accurate PUBLIC_FACTS from reputable sources.
3. DO NOT return random content from other creators as if it were {creator_name}'s.
4. Provide a helpful 'snippet' explaining why this specific result helps answer the user's intent.

Output a JSON array of objects:
[
  {{
    "title": "Exact Title",
    "url": "Full URL",
    "snippet": "Specifically WHY this video/link is a good fit.",
    "resource_type": "video" | "article" | "course_lesson",
    "relation": "SELF" | "AFFILIATED" | "PUBLIC_FACTS",
    "confidence": 0.0-1.0
  }}
]
Respond with JSON ONLY.
"""
        
        try:
            messages = [{"role": "system", "content": "You are a professional research synthesiser."}, {"role": "user", "content": prompt}]
            # Use MODEL_VERIFY as requested (GPT-5.2 level)
            text = rag.generate_chat_completion(messages, model=settings.MODEL_VERIFY, json_mode=True)
            
            if not text:
                return []
                
            # Use inherited parser and enforcer
            candidates = self._parse_json(text)
            
            if isinstance(candidates, dict):
                # Try to extract list if wrapped
                for key in ["results", "resources", "items"]:
                    if key in candidates and isinstance(candidates[key], list):
                        candidates = candidates[key]
                        break
                else:
                    # If it's a single result object, wrap it
                    if all(k in candidates for k in ["title", "url"]):
                        candidates = [candidates]
                    else:
                        candidates = []

            if not candidates or not isinstance(candidates, list):
                return []
                
            verified = self._enforce_cog(candidates, creator_profile)
            self._save_cache(creator_id, query, "serpapi", verified, cache_salt=cache_salt)
            return verified
            
        except Exception as e:
            logger.error(f"SerpApi Synthesis Error: {e}")
            return []

class OpenAIResearchProvider(ResearchProvider):
    def __init__(self):
        self.enabled = bool(settings.OPENAI_API_KEY)
        self.model = settings.MODEL_VERIFY  # gpt-5.2

    def _resolve_creator_name(self, creator_profile: Dict[str, Any]) -> str:
        """Robust creator name resolution — handles empty strings."""
        name = (creator_profile.get('name') or '').strip()
        if not name:
            handle = (creator_profile.get('handle') or '').strip().lstrip('@')
            name = handle.replace('_', ' ').title() if handle else 'Creator'
        return name

    def _validate_youtube_url(self, url: str) -> Optional[str]:
        """Validate a YouTube URL via oEmbed. Returns the real video title if valid, None if invalid/deleted."""
        import urllib.request
        import urllib.parse
        try:
            oembed_url = f"https://noembed.com/embed?url={urllib.parse.quote(url, safe='')}"
            req = urllib.request.Request(oembed_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode('utf-8'))
                # noembed returns an 'error' key if the video is unavailable
                if 'error' in data:
                    logger.warning(f"[URL-VALIDATE] Invalid/deleted YouTube URL: {url} — {data.get('error')}")
                    return None
                real_title = data.get('title', '').strip()
                logger.info(f"[URL-VALIDATE] Verified: '{real_title}' → {url}")
                return real_title if real_title else 'YouTube Video'
        except Exception as e:
            logger.warning(f"[URL-VALIDATE] Could not verify {url}: {e}")
            return None  # Can't verify, drop to be safe

    def _extract_topic_from_context(
        self, query: str, creator_name: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> str:
        """
        Build a rich, topic-specific search query from the user's question + conversation context.
        
        Solves: user says "ads" but we search "Jordan Welch ads" which is too vague.
        After this: we search "Jordan Welch facebook ads tutorial dropshipping" because
        the conversation reveals that's the real topic.
        """
        import re
        
        query_lower = query.lower().strip()
        words = query_lower.split()
        
        # ── Step 1: Detect if the query is too vague to search on its own ──
        # Short queries or follow-up questions need enrichment from conversation
        FOLLOW_UP_PATTERNS = [
            'any other', 'more video', 'another video', 'what else', 'something else',
            'different one', 'other one', 'similar', 'like that', 'related',
            'what about', 'recommend', 'reccomend', 'show me', 'give me',
        ]
        is_follow_up = any(pat in query_lower for pat in FOLLOW_UP_PATTERNS)
        is_short = len(words) <= 5
        
        # We NO LONGER return early here. Even long queries benefit from stripping 
        # filler words ("i wanna get into..."). We always attempt extraction.
        # Look at the last few messages to find what the user and bot were discussing
        topic_keywords = set()
        
        # Important topic words to extract from conversation context
        TOPIC_WORDS = {
            'ads', 'advertising', 'facebook ads', 'fb ads', 'tiktok ads', 'youtube ads',
            'google ads', 'paid ads', 'organic', 'dropshipping', 'ecommerce', 'e-commerce',
            'shopify', 'product research', 'scaling', 'targeting', 'creative', 'ugc',
            'branding', 'marketing', 'email', 'seo', 'content', 'mindset', 'motivation',
            'ai', 'automation', 'chatgpt', 'affiliate', 'amazon', 'fba', 'wholesale',
            'print on demand', 'coaching', 'mentorship', 'course', 'funnel', 'landing page',
            'copywriting', 'video ads', 'testing', 'winning products', 'supplier',
            'fulfillment', 'tiktok shop', 'instagram', 'reels', 'shorts', 'viral',
        }
        
        history = conversation_history or []
        recent_messages = history[-6:]  # Last 3 exchanges
        for msg in recent_messages:
            content = (msg.get('content') or '').lower()
            for tw in TOPIC_WORDS:
                if tw in content:
                    topic_keywords.add(tw)
        
        # Also extract topic words from the current query itself
        for tw in TOPIC_WORDS:
            if tw in query_lower:
                topic_keywords.add(tw)
        
        # ── Step 3: If it's a follow-up, find the ORIGINAL topic ──
        # When user says "any other videos?", find what topic they were originally asking about
        if is_follow_up and not topic_keywords and conversation_history:
            # Look further back to find the original request
            for msg in reversed(conversation_history[-10:]):
                if msg.get('role') == 'user':
                    user_msg = (msg.get('content') or '').lower()
                    # Skip if this is just another follow-up
                    if any(pat in user_msg for pat in FOLLOW_UP_PATTERNS):
                        continue
                    # Extract topic from the earlier user message
                    for tw in TOPIC_WORDS:
                        if tw in user_msg:
                            topic_keywords.add(tw)
                    if topic_keywords:
                        break
        
        # ── Step 4: Build the enriched query ──
        if topic_keywords:
            # Combine: original query keywords (minus filler) + topic context
            FILLER_WORDS = {'is', 'there', 'any', 'other', 'what', 'which', 'can', 'you', 
                          'would', 'recommend', 'me', 'to', 'a', 'the', 'for', 'about',
                          'i', 'wanna', 'want', 'get', 'into', 'show', 'best', 'specific',
                          'specifically', 'video', 'videos', 'watch', 'link', 'please',
                          'do', 'know', 'of', 'on', 'in', 'and', 'or', 'more', 'another',
                          'some', 'have', 'got', 'u', 'ur', 'reccomend', 'reccomendation'}
            
            meaningful_words = [w for w in words if w not in FILLER_WORDS and len(w) > 1]
            topic_parts = list(topic_keywords)
            
            # Don't duplicate — remove topic keywords that are already in meaningful_words
            for mw in meaningful_words:
                topic_parts = [tp for tp in topic_parts if mw not in tp]
            
            enriched = ' '.join(meaningful_words + topic_parts)
            if enriched.strip():
                return enriched.strip()
        
        return query

    def _is_echoed_title(
        self, title: str, query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> bool:
        """
        Detect when the search model echoes the user's question back as a video title.
        
        E.g., user asks "is there any other videos?" and the model returns a result
        with title "Is There Any Other Videos?" — this is clearly hallucinated.
        """
        if not title:
            return False
        
        title_lower = title.lower().strip().rstrip('?!.')
        query_lower = query.lower().strip().rstrip('?!.')
        
        # Direct echo of the current query
        if title_lower == query_lower:
            return True
        
        # Very high overlap (most of the words match)
        title_words = set(title_lower.split())
        query_words = set(query_lower.split())
        if len(title_words) >= 3 and len(query_words) >= 3:
            overlap = len(title_words & query_words) / max(len(title_words), len(query_words))
            if overlap >= 0.75:
                return True
        
        # Check against recent user messages in conversation history
        if conversation_history:
            for msg in conversation_history[-4:]:
                if msg.get('role') == 'user':
                    user_msg = (msg.get('content') or '').lower().strip().rstrip('?!.')
                    if title_lower == user_msg:
                        return True
                    user_words = set(user_msg.split())
                    if len(title_words) >= 3 and len(user_words) >= 3:
                        overlap = len(title_words & user_words) / max(len(title_words), len(user_words))
                        if overlap >= 0.75:
                            return True
        
        return False

    def _score_relevance(
        self, results: List[Dict[str, Any]], topic_query: str, search_intent: str
    ) -> List[Dict[str, Any]]:
        """
        Score and sort results by how well their title matches the topic query.
        
        This fixes the core problem: "I asked about ads but got general AI business videos."
        A video titled "I Spent $1M On Facebook Ads" gets a high score when researching "ads",
        while "Boring AI Business Model" gets a low score.
        """
        import re
        
        topic_lower = topic_query.lower()
        # Extract meaningful keywords from the topic query
        STOP_WORDS = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
                      'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
                      'should', 'may', 'might', 'shall', 'can', 'to', 'of', 'in', 'for',
                      'on', 'with', 'at', 'by', 'from', 'as', 'into', 'about', 'i', 'me',
                      'my', 'you', 'your', 'we', 'our', 'they', 'them', 'it', 'its', 'and',
                      'or', 'but', 'not', 'no', 'what', 'which', 'who', 'how', 'when', 'where',
                      'that', 'this', 'these', 'those', 'video', 'videos', 'watch', 'link'}
        
        topic_keywords = [w for w in re.split(r'\W+', topic_lower) if w and w not in STOP_WORDS and len(w) > 1]
        
        if not topic_keywords:
            # Can't score without keywords, just return as-is
            return results
        
        scored = []
        for r in results:
            # Prefer _real_title (oEmbed-validated) over generic extracted title
            title_lower = (r.get('_real_title') or r.get('title') or '').lower()
            snippet_lower = (r.get('snippet') or '').lower()
            
            # Count keyword hits in title (weighted 2x) and snippet (weighted 1x)
            title_hits = sum(1 for kw in topic_keywords if kw in title_lower)
            snippet_hits = sum(1 for kw in topic_keywords if kw in snippet_lower)
            relevance_score = (title_hits * 2) + snippet_hits
            
            r['_relevance_score'] = relevance_score
            
            domain = r.get('_domain', '')
            trust = 1.0 if ('youtube.com' in domain or 'youtu.be' in domain) else 0.2
            ownership = r.get('ownership_score', 0.0)
            r['_evidence_score'] = (ownership * 2) + trust + min(2.0, relevance_score / 2.0)
            
            scored.append(r)
        
        # Sort by relation SELF first, _evidence_score, confidence
        scored.sort(key=lambda x: (
            x.get('relation') == 'SELF',
            x.get('_evidence_score', 0),
            x.get('confidence', 0)
        ), reverse=True)
        
        # For VIDEO intent, apply strict filtering to drop generically matched videos
        if search_intent == 'VIDEO':
            strict_relevant = []
            requires_ads = any(kw in topic_keywords for kw in ['ads', 'advertising'])
            ADS_KEYWORDS = {"ad", "ads", "advert", "creative", "creative testing", "facebook", "meta", "tiktok", "google ads", "scaling", "campaign"}
            
            for r in scored:
                title_lower = (r.get('_real_title') or r.get('title') or '').lower()
                title_hits = sum(1 for kw in topic_keywords if kw in title_lower)
                
                if title_hits >= 1:
                    # If they asked for ads, the video MUST contain valid ad terms
                    if requires_ads and not any(kw in title_lower for kw in ADS_KEYWORDS):
                        continue
                        
                    strict_relevant.append(r)
            
            if strict_relevant:
                logger.info(f"[RELEVANCE] Kept {len(strict_relevant)} strictly relevant results, dropped {len(scored) - len(strict_relevant)}")
                scored = strict_relevant
            else:
                top_fallbacks = [r for r in scored if ('youtube.com' in r.get('url', '') or 'youtu.be' in r.get('url', '')) and (r.get('relation') == 'SELF' or r.get('confidence', 0) >= 0.6)]
                scored = top_fallbacks[:3]
                logger.info(f"[RELEVANCE] Strict filter caught all. Kept {len(scored)} top fallbacks instead.")
        
        # Clean up internal scoring keys
        for r in scored:
            r.pop('_relevance_score', None)
        
        return scored

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
        creator_name = self._resolve_creator_name(creator_profile)
        logger.info(f"OpenAISearch: creator_name='{creator_name}' | query='{query}'")

        # Check Cache
        cached = self._get_cache(creator_id, query, "openai_native_search_v3")
        if cached:
            logger.info(f"OpenAISearch: Cache hit for '{query}'")
            return cached

        import time as _time
        t0 = _time.time()
        query_lower = query.lower()

        # ─── TOPIC EXTRACTION ────────────────────────────────────────────────────
        # Build a rich, specific search query from the user's question + conversation context.
        # This is the #1 fix for "returns generic videos when user asks about a specific topic."
        
        topic_query = self._extract_topic_from_context(query, creator_name, conversation_history)
        logger.info(f"[SEARCH-TOPIC] raw='{query}' → enriched='{topic_query}'")

        # ─── INTENT CLASSIFICATION ──────────────────────────────────────────────
        video_intent_kws = [
            'video', 'watch', 'reel', 'short', 'clip', 'tutorial', 'how to', 'how do i',
            'guide', 'walkthrough', 'show me', 'best video', 'recommend', 'start', 'beginner',
            'dropshipping', 'ads', 'learn', 'strategy', 'tips', 'course video', 'training',
        ]
        product_intent_kws = [
            'course', 'program', 'coaching', 'buy', 'purchase', 'price', 'sign up',
            'viral vault', 'viralvault', 'offer', 'challenge', 'community', 'discord', 'membership',
        ]
        social_intent_kws = [
            'instagram', 'twitter', 'tiktok', 'linkedin', 'facebook page', 'social media', '@', 'profile',
            'handle', 'follow', 'x.com',
        ]

        is_video_intent   = resource_type == 'video' or any(kw in query_lower for kw in video_intent_kws)
        is_product_intent = any(kw in query_lower for kw in product_intent_kws)
        is_social_intent  = any(kw in query_lower for kw in social_intent_kws)

        if is_product_intent:
            search_intent = 'PRODUCT'
        elif is_social_intent:
            search_intent = 'SOCIAL'
        elif is_video_intent:
            search_intent = 'VIDEO'
        else:
            search_intent = 'GENERAL'

        # ─── NEW: PHRASE_HUNT MODE DETECTION ───
        search_mode = "DISCOVERY"
        topic_lower = topic_query.lower()
        phrase_markers = ["which video", "what video", "where did", "he say", "she say", "did he say", "did she say", "quote", "line", "said ", "says "]
        has_long_quote = bool(re.search(r'["\']([^"\']{35,})["\']', topic_lower)) # roughly 6+ words is 35+ chars
        if has_long_quote or any(m in topic_lower for m in phrase_markers):
            search_mode = "PHRASE_HUNT"
        
        extracted_phrase = ""
        if search_mode == "PHRASE_HUNT":
            extracted_phrase = self._extract_phrase_from_topic(topic_query)
        
        sanitized_topic = self._sanitize_topic_for_query(topic_query)
        # ───────────────────────────────────────

        logger.info(f"[SEARCH-INTENT] '{query}' → intent={search_intent} | mode={search_mode}")

        # ─── CONVERSATION-AWARE DEDUP ────────────────────────────────────────────
        # Extract URLs already shared in this conversation so we don't repeat them
        exclude_urls = set()
        if conversation_history:
            import re as _re_dedup
            for msg in conversation_history:
                if msg.get('role') == 'assistant':
                    content = msg.get('content') or ''
                    found = _re_dedup.findall(r'https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([^\s\)&\]"\']+)', content)
                    for vid_id in found:
                        exclude_urls.add(f'youtube.com/watch?v={vid_id.split("&")[0]}')
        
        exclude_instruction = ""
        if exclude_urls:
            exclude_list = "\n".join(f"  - {u}" for u in exclude_urls)
            exclude_instruction = f"\n\nDo NOT recommend these videos (already shared in this conversation):\n{exclude_list}\nFind DIFFERENT videos instead.\n"

        # ─── NATURAL LANGUAGE SEARCH PROMPTS ──────────────────────────────────────
        # Key insight: Don't force JSON output. Let the model answer naturally with
        # real URLs from web search. We extract URLs + titles from the response after.

        if search_intent == 'VIDEO':
            search_prompt = (
                f'What are {creator_name}\'s best YouTube videos specifically about {sanitized_topic}?\n\n'
                f'Search for: {creator_name} "{sanitized_topic}" site:youtube.com\n\n'
                f'For each video you find, list:\n'
                f'1. The exact video title\n'
                f'2. The full YouTube URL (e.g. https://www.youtube.com/watch?v=...)\n'
                f'3. A one-line description of why it\'s relevant to "{sanitized_topic}"\n\n'
                f'Only include videos that are DIRECTLY about {sanitized_topic}. '
                f'Do not include generic/unrelated videos.\n'
                f'Do NOT make up or guess YouTube URLs — only use URLs from actual search results.'
                f'{exclude_instruction}'
            )
            if search_mode == 'PHRASE_HUNT':
                search_prompt += "\nReturn youtube/watch urls if possible."

        elif search_intent == 'PRODUCT':
            search_prompt = (
                f'What products, courses, or programs does {creator_name} offer related to "{sanitized_topic}"?\n\n'
                f'Search for: {creator_name} {sanitized_topic} course OR program OR community\n\n'
                f'For each product found, list the name, the direct URL, and what it offers.\n'
                f'Also include any YouTube videos where {creator_name} talks about this product.'
                f'{exclude_instruction}'
            )

        elif search_intent == 'SOCIAL':
            platform_map = {
                'instagram': f'site:instagram.com {creator_name}',
                'tiktok': f'site:tiktok.com {creator_name}',
                'twitter': f'site:twitter.com OR site:x.com {creator_name}',
                'linkedin': f'site:linkedin.com {creator_name}',
                'facebook': f'site:facebook.com {creator_name}',
            }
            detected_platform = next((p for p in platform_map if p in query_lower), None)
            platform_query = platform_map.get(detected_platform, f'{creator_name} social media profiles')

            search_prompt = (
                f'Find the official social media profile for {creator_name}: {platform_query}\n\n'
                f'List the profile URL and handle.'
            )

        else:
            search_prompt = (
                f'Search the web for: {creator_name} "{sanitized_topic}"\n\n'
                f'List the most relevant results with their full URLs and a brief description.\n'
                f'Only include real URLs from actual search results.'
                f'{exclude_instruction}'
            )

        # ─── EXECUTE SEARCH ──────────────────────────────────────────────────────
        # GPT-5.2 web search only works via Responses API (not Chat Completions).
        # gpt-4o-search-preview works via Chat Completions.
        # Try Responses API (GPT-5.2) first for best quality, fall back to Chat Completions.
        results = []
        try:
            results = self._search_responses_api(search_prompt, creator_name)
        except Exception as e:
            logger.warning(f"OpenAISearch: Responses API (GPT-5.2) failed: {e}")
        
        if not results:
            try:
                results = self._search_chat_completions(search_prompt, creator_name)
            except Exception as e2:
                logger.warning(f"OpenAISearch: Chat Completions fallback also failed: {e2}")
                results = []
        
        # ─── UNWRAP URLs ─────────────────────────────────────────────────────────
        unwrapped_count = 0
        if results:
            for r in results:
                original_url = r.get('url', '')
                if 'glasp.co' in original_url and '/youtube/' in original_url:
                    match = re.search(r'/youtube/([A-Za-z0-9_-]{11})(?:[/?]|$)', original_url)
                    if match:
                        r['url'] = f"https://www.youtube.com/watch?v={match.group(1)}"
                        unwrapped_count += 1
                        logger.info(f"OpenAISearch: Unwrapped {original_url} -> {r['url']}")
        if unwrapped_count > 0:
            logger.info(f"OpenAISearch: unwrapped={unwrapped_count}")

        # ─── POST-SEARCH DEDUP ───────────────────────────────────────────────────
        if exclude_urls and results:
            before_count = len(results)
            results = [r for r in results if not any(ex in r.get('url', '') for ex in exclude_urls)]
            if len(results) < before_count:
                logger.info(f"[DEDUP] Removed {before_count - len(results)} already-shared videos")

        # ── NEW: PREFILTER + COG ──
        extracted_count = len(results)
        if results:
            results = self._prefilter_candidates(results, creator_profile)
        prefilter_count = len(results)
        
        if results:
            results = self._enforce_cog(results, creator_profile)
        cog_count = len(results)

        elapsed = _time.time() - t0
        logger.info(f"OpenAISearch: Completed in {elapsed:.1f}s — {extracted_count} raw results")

        if results:
            # ─── VALIDATION + RELEVANCE SCORING ──────────────────────────────────
            pre_validated = []
            for r in results:
                if not isinstance(r, dict) or not r.get('url'):
                    continue
                r.setdefault('confidence', 0.8)
                r.setdefault('relation', 'PUBLIC_FACTS')
                r.setdefault('resource_type', 'web')
                r.setdefault('snippet', '')
                r.setdefault('title', 'Untitled')

                url = r['url']
                title = r.get('title', '')
                
                # ── Anti-hallucination: detect echoed user questions as titles ──
                # If the title is basically the user's own question parroted back, it's fake
                if self._is_echoed_title(title, query, conversation_history):
                    logger.warning(f"[HALLUCINATION] Dropping echoed title: '{title}' → {url}")
                    continue

                # Allowed video URL patterns across platforms
                VIDEO_URL_PATTERNS = [
                    'youtube.com/watch?v=', 'youtu.be/',
                    'youtube.com/shorts/',
                    'instagram.com/p/', 'instagram.com/reel/',
                    'tiktok.com/',
                    'facebook.com/watch', 'facebook.com/reel',
                    'x.com/', 'twitter.com/',
                    'linkedin.com/posts/',
                ]
                is_video_url = any(pat in url for pat in VIDEO_URL_PATTERNS)

                # For VIDEO intent, only accept direct video URLs
                if search_intent == 'VIDEO' and not is_video_url:
                    logger.info(f"[URL-FILTER] Dropping non-video URL in VIDEO search: {url}")
                    continue
                
                # Tag the platform
                if 'youtube.com' in url or 'youtu.be' in url:
                    r.setdefault('platform', 'youtube')
                elif 'instagram.com' in url:
                    r.setdefault('platform', 'instagram')
                elif 'tiktok.com' in url:
                    r.setdefault('platform', 'tiktok')
                elif 'facebook.com' in url:
                    r.setdefault('platform', 'facebook')
                elif 'twitter.com' in url or 'x.com' in url:
                    r.setdefault('platform', 'twitter')
                elif 'linkedin.com' in url:
                    r.setdefault('platform', 'linkedin')

                pre_validated.append(r)

            # ── Parallel YouTube oEmbed validation ──
            youtube_items = [(i, r) for i, r in enumerate(pre_validated) if 'youtube.com' in r['url'] or 'youtu.be' in r['url']]
            
            if youtube_items:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                
                def validate_item(args):
                    idx, r = args
                    real_title = self._validate_youtube_url(r['url'])
                    return idx, real_title
                
                validation_results = {}
                with ThreadPoolExecutor(max_workers=min(len(youtube_items), 4)) as executor:
                    futures = {executor.submit(validate_item, item): item[0] for item in youtube_items}
                    for future in as_completed(futures):
                        try:
                            idx, real_title = future.result(timeout=6)
                            validation_results[idx] = real_title
                        except Exception:
                            validation_results[futures[future]] = None
                
                validated = []
                for i, r in enumerate(pre_validated):
                    if i in validation_results:
                        real_title = validation_results[i]
                        if real_title is None:
                            logger.warning(f"[URL-VALIDATE] Dropping invalid YouTube URL: {r['url']}")
                            continue
                        if r.get('title', '').strip().lower() in ('youtube video', 'untitled', ''):
                            r['title'] = real_title
                        else:
                            r['_real_title'] = real_title
                        r['resource_type'] = 'video'
                    validated.append(r)
            else:
                validated = pre_validated

            # ── RELEVANCE SCORING ──
            # Score each result by how well its title matches the sanitized topic query
            validated = self._score_relevance(validated, sanitized_topic, search_intent)

            # ── PHRASE_HUNT VERIFICATION ──
            verified_count = 0
            if search_mode == "PHRASE_HUNT" and extracted_phrase:
                verified = self._verify_phrase_in_candidates(validated[:8], extracted_phrase, creator_profile)
                if verified:
                    verified.sort(key=lambda x: (x.get('_phrase_match_strength', 0), x.get('confidence', 0)), reverse=True)
                    validated = verified
                    verified_count = len(validated)
                else:
                    validated = [r for r in validated[:3]]
                    for r in validated:
                        r['_phrase_verified'] = False

            results = validated
            logger.info(f"OpenAISearch: Pipeline counts - Candidates: {extracted_count} -> Prefilter: {prefilter_count} -> COG: {cog_count} -> Final: {len(results)}")
            logger.info(f"OpenAISearch: search_mode={search_mode} phrase_len={len(extracted_phrase) if extracted_phrase else 0}")
            if search_mode == "PHRASE_HUNT":
                logger.info(f"OpenAISearch: PHRASE_HUNT candidates={cog_count}, verified={verified_count}")

        # ── RETRY: If VIDEO search returned nothing relevant, try a more targeted query ──
        if search_intent == 'VIDEO' and not results:
            import re as _re
            topic_kws = [w for w in _re.split(r'\W+', topic_query.lower()) if len(w) > 2 and w not in {'the', 'and', 'for', 'how', 'what', 'you', 'video', 'videos'}]
            if topic_kws:
                quoted_terms = ' OR '.join(f'"{kw}"' for kw in topic_kws[:3])
                retry_prompt = (
                    f'Find YouTube videos by {creator_name} about: {quoted_terms}\n\n'
                    f'List each video with its title and full YouTube URL.\n'
                    f'Only include videos whose title contains at least one of: {", ".join(topic_kws)}\n'
                    f'Do NOT make up or guess YouTube URLs.'
                    f'{exclude_instruction}'
                )
                logger.info(f"[SEARCH-RETRY] Retrying with targeted query: {creator_name} {quoted_terms}")
                try:
                    retry_results = self._search_chat_completions(retry_prompt, creator_name)
                    if not retry_results:
                        retry_results = self._search_responses_api(retry_prompt, creator_name)
                    if retry_results:
                        # Accept all results with valid video URLs — don't filter by title
                        # because oEmbed hasn't validated titles yet (titles are still generic
                        # like "YouTube Video"). The main validation pipeline ran above, but
                        # retry results are new and need their own oEmbed validation.
                        valid_retry = [r for r in retry_results if isinstance(r, dict) and r.get('url')]
                        
                        # Run oEmbed validation on retry results inline
                        if valid_retry:
                            from concurrent.futures import ThreadPoolExecutor, as_completed
                            yt_items = [(i, r) for i, r in enumerate(valid_retry) if 'youtube.com' in r['url'] or 'youtu.be' in r['url']]
                            if yt_items:
                                def _val(args):
                                    idx, r = args
                                    return idx, self._validate_youtube_url(r['url'])
                                with ThreadPoolExecutor(max_workers=min(len(yt_items), 4)) as exe:
                                    futs = {exe.submit(_val, item): item[0] for item in yt_items}
                                    for fut in as_completed(futs):
                                        try:
                                            idx, real_title = fut.result(timeout=6)
                                            if real_title is None:
                                                valid_retry[idx] = None  # Mark for removal
                                            else:
                                                valid_retry[idx]['_real_title'] = real_title
                                                valid_retry[idx]['title'] = real_title
                                        except Exception:
                                            valid_retry[futs[fut]] = None
                                valid_retry = [r for r in valid_retry if r is not None]
                            
                            # Now filter by topic keywords using real titles
                            results = [
                                r for r in valid_retry
                                if any(kw in (r.get('title') or '').lower() for kw in topic_kws)
                            ]
                            logger.info(f"[SEARCH-RETRY] Got {len(results)} relevant results on retry (after oEmbed)")
                except Exception as e:
                    logger.warning(f"[SEARCH-RETRY] Retry failed: {e}")

        # Promote _real_title to title so users see proper video names
        for r in results:
            if r.get('_real_title'):
                r['title'] = r.pop('_real_title')
        
        if results:
            self._save_cache(creator_id, query, "openai_native_search_v3", results)

        return results


    def _search_responses_api(self, prompt: str, creator_name: str) -> List[Dict[str, Any]]:
        """Use OpenAI Responses API with web_search_preview tool (supports GPT-5.2)."""
        import openai
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        
        logger.info(f"OpenAISearch: Using raw httpx for Responses API with {self.model}")
        
        url = "https://api.openai.com/v1/responses"
        headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "tools": [{"type": "web_search_preview"}],
            "input": prompt,
        }
        
        import httpx
        text = ""
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                resp_json = resp.json()
            
            # Extract output text safely
            text = resp_json.get("output_text")
            if not text:
                text = ""
                # Traverse output blocks
                for item in resp_json.get("output", []):
                    if isinstance(item, dict):
                        if item.get("text"):
                            text += str(item["text"])
                        elif "content" in item and isinstance(item["content"], list):
                            for block in item["content"]:
                                if isinstance(block, dict) and block.get("text"):
                                    text += str(block["text"])
                                    
        except Exception as err:
            logger.warning(f"OpenAISearch: Raw Responses API call failed, triggering fallback: {err}")
            return []
            
        logger.info(f"OpenAISearch: Responses API returned {len(text)} chars")
        
        # 1. Try JSON
        json_results = self._extract_results_from_text(text, creator_name)
        
        # 2. Always fallback and combine
        text_results = self._extract_urls_from_text(text, creator_name)
        
        # Merge ignoring duplicates by URL
        seen = {r['url'] for r in json_results}
        combined = list(json_results)
        for r in text_results:
            if r['url'] not in seen:
                combined.append(r)
                seen.add(r['url'])
                
        logger.info(f"OpenAISearch: tool_results={len(json_results)} text_urls={len(text_results)}")
        return combined

    def _search_chat_completions(self, prompt: str, creator_name: str) -> List[Dict[str, Any]]:
        """Primary search: Use GPT-5.2 via Chat Completions with web search."""
        import openai
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        
        # gpt-4o-search-preview supports web search via Chat Completions
        # Note: GPT-5.2 does NOT support web_search_options in Chat Completions (use Responses API instead)
        models_to_try = [
            ("gpt-4o-search-preview", {"web_search_options": {}}),
            ("gpt-4o-mini-search-preview", {}),
        ]
        
        text = ""
        for model_name, extra_kwargs in models_to_try:
            try:
                logger.info(f"OpenAISearch: Trying {model_name} via Chat Completions")
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    **extra_kwargs,
                )
                text = response.choices[0].message.content or ""
                if text:
                    logger.info(f"OpenAISearch: {model_name} returned {len(text)} chars")
                    break
            except Exception as e:
                logger.warning(f"OpenAISearch: {model_name} failed: {e}")
                continue
        
        if not text:
            return []
        
        # Try JSON extraction
        json_results = self._extract_results_from_text(text, creator_name)
        
        # Primary extraction: pull URLs from natural language response
        text_results = self._extract_urls_from_text(text, creator_name)
        
        # Merge
        seen = {r['url'] for r in json_results}
        combined = list(json_results)
        for r in text_results:
            if r['url'] not in seen:
                combined.append(r)
                seen.add(r['url'])
                
        logger.info(f"OpenAISearch: tool_results={len(json_results)} text_urls={len(text_results)}")
        return combined

    def _extract_results_from_text(self, text: str, creator_name: str) -> List[Dict[str, Any]]:
        """Extract JSON results from model response text."""
        if not text:
            return []
        
        candidates = []
        text_strip = text.strip()
        if text_strip.startswith('['):
            try:
                match = re.search(r'\[.*\]', text, re.DOTALL)
                if match:
                    candidates = json.loads(match.group())
                else:
                    candidates = self._parse_json(text)
            except Exception as e:
                logger.warning(f"OpenAISearch: JSON parse failed (ignoring): {e}")
                
        if not isinstance(candidates, list):
            candidates = []
            
        if not candidates:
            return []
        
        # ── Anti-hallucination: drop fake/placeholder YouTube video IDs ──
        # Real YouTube video IDs are 11 chars and look random (e.g. dQw4w9WgXcQ)
        # Hallucinated ones tend to be short or obviously sequential (abc123, def456)
        FAKE_ID_PATTERNS = {'abc123', 'def456', 'ghi789', 'jkl012', 'mno345', 'xyz789',
                            'test123', '1234567', 'abcdefg', 'example', 'placeholder'}
        filtered = []
        for r in candidates:
            if not isinstance(r, dict):
                continue
            url = r.get('url', '')
            if ('youtube.com/watch?v=' in url or 'youtu.be/' in url):
                # Extract video ID
                vid_id = ''
                if 'v=' in url:
                    vid_id = url.split('v=')[1].split('&')[0].split('?')[0]
                elif 'youtu.be/' in url:
                    vid_id = url.split('youtu.be/')[-1].split('?')[0]
                # Real YT IDs are 11 chars; shorter ones or known fakes are hallucinated
                if len(vid_id) < 8 or vid_id.lower() in FAKE_ID_PATTERNS:
                    logger.info(f"[HALLUCINATION] Dropping fake YouTube video ID: {vid_id} → {url}")
                    continue
            filtered.append(r)
        
        return filtered

    def _extract_urls_from_text(self, text: str, creator_name: str) -> List[Dict[str, Any]]:
        """Extract URLs from natural language response. Handles markdown links and bare URLs."""
        import re as _re
        
        FAKE_ID_PATTERNS = {'abc123', 'def456', 'ghi789', 'jkl012', 'mno345', 'xyz789',
                            'test123', '1234567', 'abcdefg', 'example', 'placeholder'}
        
        results = []
        seen = set()
        
        def _is_fake_youtube(url: str) -> bool:
            """Check if a YouTube URL has a hallucinated video ID."""
            vid_id = ''
            if 'v=' in url:
                vid_id = url.split('v=')[1].split('&')[0].split('?')[0]
            elif 'youtu.be/' in url:
                vid_id = url.split('youtu.be/')[-1].split('?')[0]
            if vid_id and (len(vid_id) < 8 or vid_id.lower() in FAKE_ID_PATTERNS):
                logger.info(f"[HALLUCINATION] Dropping fake YouTube video ID: {vid_id} → {url}")
                return True
            return False
        
        def _add_result(url: str, title: str, snippet: str = ""):
            url = url.rstrip('.,;:)')
            if url in seen:
                return
            seen.add(url)
            
            is_youtube = 'youtube.com' in url or 'youtu.be' in url
            if is_youtube and _is_fake_youtube(url):
                return
            
            resource_type = "video" if is_youtube else "web"
            relation = "SELF" if creator_name.lower() in text.lower() and is_youtube else "PUBLIC_FACTS"
            
            results.append({
                "title": title or ("YouTube Video" if is_youtube else url.split('/')[-1][:80]),
                "url": url,
                "snippet": snippet[:300].strip() if snippet else "",
                "resource_type": resource_type,
                "relation": relation,
                "confidence": 0.8
            })
        
        # Phase 1: Extract markdown-style links [Title](URL) — these have proper titles
        md_links = _re.findall(r'\[([^\]]+)\]\((https?://[^\s\)]+)\)', text)
        for title, url in md_links:
            _add_result(url, title)
        
        # Phase 2: Extract numbered/bulleted list items with URLs
        # Matches patterns like: "1. Video Title - https://..." or "- Title: https://..."
        list_items = _re.findall(r'(?:^|\n)\s*(?:\d+[\.\)]\s*|[-•]\s*)(.+?)\s*[-–—:]\s*(https?://[^\s\)]+)', text)
        for title, url in list_items:
            _add_result(url, title.strip())
        
        # Phase 3: Extract bare URLs not already captured
        bare_urls = _re.findall(r'https?://[^\s\)\]"\']+', text)
        for url in bare_urls:
            url = url.rstrip('.,;:)')
            if url not in seen:
                # Try to find a title near the URL in the text
                nearby_title = ""
                url_pos = text.find(url)
                if url_pos > 0:
                    # Look at the line containing the URL for context
                    line_start = text.rfind('\n', max(0, url_pos - 200), url_pos)
                    line_text = text[line_start + 1:url_pos].strip() if line_start >= 0 else text[:url_pos].strip()
                    # Clean up common prefixes
                    line_text = _re.sub(r'^[\d+\.\)\-•\s]+', '', line_text).strip()
                    line_text = _re.sub(r'[\-–—:]+$', '', line_text).strip()
                    if len(line_text) > 5 and len(line_text) < 150:
                        nearby_title = line_text
                _add_result(url, nearby_title)
        
        logger.info(f"OpenAISearch: Extracted {len(results)} URLs from natural text response")
        return results

    def _prefilter_candidates(self, results: List[Dict[str, Any]], creator_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        from urllib.parse import urlparse
        kept = []
        junk_domains = ['subtitlecat.com', 'downsub.com', 'youtubetranscript.com']
        
        for r in results:
            url = r.get('url', '').lower()
            if not url:
                continue
            
            domain = urlparse(url).netloc.lower()
            r['_domain'] = domain
            
            if 'youtube.com' in domain or 'youtu.be' in domain:
                r['_prefilter_kept_reason'] = 'youtube'
                kept.append(r)
            elif any(j in domain for j in junk_domains) or url.endswith('.srt') or 'subtitle' in domain or 'transcript' in domain:
                r['_prefilter_kept_reason'] = 'blocked_domain'
            else:
                r['_prefilter_kept_reason'] = 'other'
                kept.append(r)
        
        return kept

    def _extract_phrase_from_topic(self, topic: str) -> str:
        """Extracts exact quoted phrases or common intent patterns up to 160 chars."""
        import re
        topic = topic.strip()
        # Extract quoted
        match = re.search(r'["\']([^"\']{10,160})["\']', topic)
        if match:
            return match.group(1).strip()
        
        # Extract following pattern
        pattern = r"(?:where|which video|what video).*(?:did he say|did she say|said|says)\s+(.+)"
        match = re.search(pattern, topic, re.IGNORECASE)
        if match:
            phrase = match.group(1).strip()
            # Remove any trailing punctuation fluff if it's naked
            return phrase[:160].strip()
        return ""

    def _sanitize_topic_for_query(self, topic: str) -> str:
        """Removes quotes, fluff, and truncates to ~12 words for cleaner web search."""
        import re
        # Remove anything in quotes
        sanitized = re.sub(r'["\'][^"\']*["\']', '', topic)
        
        # Define fluff
        FLUFF = {'yo', 'what\'s good', 'im', 'i\'m', 'hey', 'bro', 'gang', 'which', 'video', 'where', 'did', 'he', 'she', 'say', 'said', 'says', 'quote', 'line', 'the', 'a', 'an'}
        
        words = re.split(r'\s+', sanitized)
        filtered = [w for w in words if w.lower().strip("?,.!") not in FLUFF and len(w) > 1]
        
        return " ".join(filtered[:12])

    def _verify_phrase_in_candidates(self, candidates: List[Dict[str, Any]], phrase: str, creator_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        import re
        if not phrase:
            return candidates
            
        phrase_lower = phrase.lower()
        sig_words = [w for w in re.split(r'\W+', phrase_lower) if len(w) > 3]
        total_sig = len(sig_words)
        
        verified_candidates = []
        
        # Limit to top 5 YouTube candidates
        yt_candidates = [c for c in candidates if 'youtube.com' in c.get('url', '') or 'youtu.be' in c.get('url', '')][:5]
        
        for c in yt_candidates:
            url = c.get('url', '')
            transcript_text = None
            
            # 1. Try documents table
            doc = db.execute_one("SELECT content FROM documents WHERE url = %s", (url,))
            if doc and doc.get('content'):
                transcript_text = doc['content']
                
            # 2. Try search_items table
            if not transcript_text:
                item = db.execute_one("SELECT transcript FROM search_items WHERE source_url = %s", (url,))
                if item and item.get('transcript'):
                    transcript_text = item['transcript']
                    
            if not transcript_text:
                continue
                
            transcript_lower = transcript_text.lower()
            
            is_verified = False
            match_strength = 0.0
            
            if phrase_lower in transcript_lower:
                is_verified = True
                match_strength = 1.0
            elif total_sig > 0:
                # Soft match: check 400-char windows (split chunks)
                windows = [transcript_lower[i:i+400] for i in range(0, len(transcript_lower), 200)]
                best_ratio = 0.0
                for w in windows:
                    hits = sum(1 for word in set(sig_words) if word in w)
                    ratio = hits / total_sig
                    if ratio > best_ratio:
                        best_ratio = ratio
                
                if best_ratio >= 0.7:
                    is_verified = True
                    match_strength = best_ratio
                    
            if is_verified:
                c['_phrase_verified'] = True
                c['_phrase_match_strength'] = match_strength
                c['confidence'] = min(1.0, c.get('confidence', 0.8) + 0.1)
                verified_candidates.append(c)
                
        return verified_candidates

def get_research_provider() -> ResearchProvider:
    """Factory to return the appropriate research provider based on settings."""
    # Priority: OpenAI (if specifically requested by user via new default) -> SerpApi -> Gemini
    # We'll default to OpenAIResearchProvider now as requested.
    if settings.OPENAI_API_KEY:
        return OpenAIResearchProvider()
    if settings.SEARCH_API_KEY:
        return SerpApiResearchProvider()
    return GeminiResearchProvider()
