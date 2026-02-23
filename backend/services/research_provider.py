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
import rag

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
            # Clean markdown formatting
            cleaned = text.strip()
            if cleaned.startswith("```"):
                # Remove ```json or ``` at start and ``` at end
                cleaned = re.sub(r'^```(?:json)?\n?', '', cleaned)
                cleaned = re.sub(r'\n?```$', '', cleaned)
            
            # Simple fallback for missing commas or other minor issues 
            return json.loads(cleaned)
        except Exception:
            # Try regex extraction if direct parse fails
            try:
                json_match = re.search(r'\[\s*\{.*\}\s*\]|\{.*\}', cleaned, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group(0))
            except Exception as e:
                logger.error(f"ResearchProvider: Failed to parse JSON even after cleanup: {e}")
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
        creator_name = (creator_profile.get('name') or "").lower()
        
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
        
        verified = self._enforce_cog(candidates, creator_profile)
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
        self.model = settings.MODEL_VERIFY # gpt-5.2 or fallback

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

        # Check Cache
        cached = self._get_cache(creator_id, query, "openai_search")
        if cached:
            logger.info(f"OpenAIResearch: Cache hit for '{query}'")
            return cached

        # Investigative Prompt for ChatGPT-native search
        # We explicitly request it to use its search capability and return structured results.
        prompt = f"""
        INVESTIGATE: "{query}" relative to the creator "{creator_name}".
        
        TASK:
        Use your web search capabilities to find the most accurate, current information or resources (videos, articles) that answer this query.
        
        CRITICAL CATEGORIES:
        1. SELF/OWNED: Content owned by {creator_name} (YouTube, site, courses).
        2. PUBLIC_FACTS: Reliable external data (prices, news, general facts).
        
        Output a JSON array of objects:
        [
          {{
            "title": "Exact Title",
            "url": "Full URL",
            "snippet": "Summary of findings",
            "resource_type": "video" | "article" | "web",
            "relation": "SELF" | "PUBLIC_FACTS",
            "confidence": 0.0-1.0
          }}
        ]
        Respond with JSON ONLY.
        """

        from concurrent.futures import ThreadPoolExecutor
        
        try:
            from services.search_engine import SearchEngine
            engine = SearchEngine()
            
            # STEP 1: Deep Investigation (Instructional Call)
            # Use a faster model for query generation to reduce latency
            investigation_prompt = f"""
            User Query: "{query}"
            Identify potential titles and years of {creator_name}'s very first YouTube upload.
            Generate 3 highly specific search queries to find the exact video and its narrative story.
            IMPORTANT: Every query MUST include the name "{creator_name}".
            Example: "{creator_name} earliest upload 2017", "YouTube first video {creator_name}".
            
            Output ONLY a JSON array of 3 strings.
            """
            
            logger.info(f"OpenAIResearch: Deep Investigation - Generating queries for '{query}'")
            iq_text = rag.generate_chat_completion(
                [{"role": "user", "content": investigation_prompt}],
                model=settings.MODEL_FALLBACK_SMART, # Faster model (e.g. GPT-4o)
                json_mode=False
            )
            
            search_queries = [f"{creator_name} {query}"]
            try:
                match = re.search(r'\[.*\]', iq_text, re.DOTALL)
                if match:
                    parsed_iq = json.loads(match.group())
                    if isinstance(parsed_iq, list):
                        # Ensure every query is anchored to the creator
                        for q_cand in parsed_iq:
                            if creator_name.lower() not in q_cand.lower():
                                q_cand = f"{creator_name} {q_cand}"
                            search_queries.append(q_cand)
                        search_queries = list(set(search_queries))[:5]
            except Exception as e:
                logger.warning(f"OpenAIResearch: Failed to parse queries from {iq_text[:100]}: {e}")

            # STEP 2: Parallel Multi-Pass Retrieval
            all_raw = []
            seen_urls = set()
            
            logger.info(f"OpenAIResearch: Parallel Retrieval - Running {len(search_queries)} passes")
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(engine.search, sq, 6): sq for sq in search_queries}
                for future in futures:
                    try:
                        res = future.result()
                        for r in res:
                            url = r.get('link') or r.get('url')
                            if url and url not in seen_urls:
                                all_raw.append(r)
                                seen_urls.add(url)
                    except Exception as e:
                        logger.error(f"Search pass failed for {futures[future]}: {e}")
            
            if not all_raw:
                logger.warning(f"OpenAIResearch: No results found after {len(search_queries)} passes.")
                return []
                
            # STEP 3: Multi-Layer Synthesis (Matching ChatGPT 5.2 Quality)
            # We use MODEL_VERIFY (gpt-5.2) for the high-IQ synthesis only.
            results_text = "\n".join([
                f"[{i}] {r.get('title')}\nURL: {r.get('link') or r.get('url')}\nSnippet: {r.get('snippet')}\n"
                for i, r in enumerate(all_raw[:15])
            ])
            
            synthesis_prompt = f"""
            GOAL: Match the depth and narrative quality of ChatGPT 5.2.
            CREATOR: {creator_name}
            QUERY: {query}
            
            WEB DATA:
            {results_text}
            
            TASK:
            1. Find the definitive answer (e.g. {creator_name}'s first video).
            2. For the main answer, provide a "NARRATIVE SNIPPET" (3-4 sentences total).
            3. CRITICAL: If you found a result that matches {creator_name}'s channel or handles, assign confidence 0.95-1.0. 
            4. Output only the JSON array of objects below.
            
            [
              {{
                "title": "Exact Title",
                "url": "Full URL",
                "snippet": "Detailed narrative summary with context",
                "resource_type": "video" | "article" | "web",
                "relation": "SELF" | "PUBLIC_FACTS",
                "confidence": 0.0-1.0
              }}
            ]
            """
            
            logger.info(f"OpenAIResearch: Final Synthesis - Analyzing {len(all_raw)} results with {self.model}")
            text = rag.generate_chat_completion(
                [{"role": "user", "content": synthesis_prompt}], 
                model=self.model, 
                json_mode=False
            )
            
            if not text:
                return []
                
            candidates = []
            try:
                match = re.search(r'\[.*\]', text, re.DOTALL)
                if match:
                    candidates = json.loads(match.group())
                else:
                    candidates = self._parse_json(text)
            except Exception as e:
                logger.error(f"OpenAIResearch: Final parse error: {e}")
                return []
                
            if not candidates or not isinstance(candidates, list):
                logger.warning(f"OpenAIResearch: No candidates extracted from LLM text: {text[:200]}...")
                return []
                
            logger.info(f"OpenAIResearch: Synthesis raw candidates: {json.dumps(candidates, indent=2)}")
            verified = self._enforce_cog(candidates, creator_profile)
            logger.info(f"OpenAIResearch: Verified results after COG: {len(verified)}")
            self._save_cache(creator_id, query, "openai_search", verified)
            return verified
            
        except Exception as e:
            logger.error(f"OpenAIResearch Error: {e}")
            return []

def get_research_provider() -> ResearchProvider:
    """Factory to return the appropriate research provider based on settings."""
    # Priority: OpenAI (if specifically requested by user via new default) -> SerpApi -> Gemini
    # We'll default to OpenAIResearchProvider now as requested.
    if settings.OPENAI_API_KEY:
        return OpenAIResearchProvider()
    if settings.SEARCH_API_KEY:
        return SerpApiResearchProvider()
    return GeminiResearchProvider()
