import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Set
from urllib.parse import parse_qs, urlparse
import requests
from backend.db import db
from backend.settings import settings
import backend.rag as rag
from backend.services.live_search_rules import needs_fresh_public_web_search

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

    def _normalize_netloc(self, netloc: str) -> str:
        host = (netloc or "").lower().strip()
        if host.startswith("www."):
            host = host[4:]
        if host.startswith("m."):
            host = host[2:]
        return host

    def _normalize_handle(self, value: Any) -> str:
        value = str(value or "").strip().lower().lstrip("@")
        return re.sub(r"[^a-z0-9._-]+", "", value)

    def _infer_platform_from_url(self, url: str) -> str:
        host = self._normalize_netloc(urlparse(url or "").netloc)
        if "youtube.com" in host or "youtu.be" in host:
            return "youtube"
        if "instagram.com" in host:
            return "instagram"
        if "tiktok.com" in host:
            return "tiktok"
        if "facebook.com" in host or "fb.watch" in host:
            return "facebook"
        if "x.com" in host or "twitter.com" in host:
            return "twitter"
        if "linkedin.com" in host:
            return "linkedin"
        return "web"

    def _is_direct_video_url(self, url: str) -> bool:
        url_lower = (url or "").lower()
        return any(pattern in url_lower for pattern in [
            "youtube.com/watch?v=", "youtube.com/shorts/", "youtu.be/",
            "instagram.com/reel/", "instagram.com/reels/", "instagram.com/tv/", "instagram.com/p/",
            "tiktok.com/",
            "facebook.com/watch", "facebook.com/reel", "facebook.com/share/v/", "fb.watch/",
            "x.com/", "twitter.com/",
        ])

    def _is_placeholder_url(self, url: str) -> bool:
        if not url:
            return True
        url_lower = url.lower()
        placeholder_tokens = {
            "video_id", "reel_id", "post_id", "short_id", "clip_id", "tiktok_id",
            "fb_video_id", "content_id", "youtube_id", "placeholder", "example",
        }
        if any(token in url_lower for token in placeholder_tokens):
            return True
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if "v" in query:
            vid = (query.get("v") or [""])[0].strip()
            if not vid or vid.lower() in placeholder_tokens or vid.upper() == "VIDEO_ID":
                return True
        path_tokens = [t for t in re.split(r"[/?&=._-]+", parsed.path.lower()) if t]
        return any(token in placeholder_tokens for token in path_tokens)

    def _extract_owner_signals_from_url(self, url: str) -> Dict[str, Any]:
        parsed = urlparse(url or "")
        host = self._normalize_netloc(parsed.netloc)
        path_segments = [seg for seg in parsed.path.split("/") if seg]
        platform = self._infer_platform_from_url(url)
        handles: Set[str] = set()
        ids: Set[str] = set()

        if platform == "youtube" and path_segments:
            first = path_segments[0]
            if first.startswith("@"):
                handles.add(self._normalize_handle(first))
            elif first in {"channel", "user", "c"} and len(path_segments) > 1:
                candidate = self._normalize_handle(path_segments[1])
                if first == "channel":
                    ids.add(candidate)
                elif candidate:
                    handles.add(candidate)
        elif platform == "instagram" and path_segments:
            first = self._normalize_handle(path_segments[0])
            if first and first not in {"reel", "reels", "p", "tv", "stories", "explore"}:
                handles.add(first)
        elif platform == "tiktok":
            for seg in path_segments:
                if seg.startswith("@"):
                    handles.add(self._normalize_handle(seg))
                    break
        elif platform == "facebook" and host != "fb.watch" and path_segments:
            first = self._normalize_handle(path_segments[0])
            if first and first not in {"watch", "reel", "share", "video", "videos", "groups", "events"}:
                handles.add(first)
        elif platform == "twitter" and path_segments:
            first = self._normalize_handle(path_segments[0])
            if first and first not in {"i", "search", "home", "explore"}:
                handles.add(first)
        elif platform == "linkedin" and len(path_segments) >= 2:
            candidate = self._normalize_handle(path_segments[1])
            if candidate:
                handles.add(candidate)

        return {
            "platform": platform,
            "host": host,
            "handles": handles,
            "ids": ids,
        }

    def _extract_owner_signals_from_candidate(self, candidate: Dict[str, Any], platform: str) -> Dict[str, Set[str]]:
        meta = candidate.get("metadata", {}) or {}
        source = str(candidate.get("source") or candidate.get("source_opt") or "")
        fields = [
            meta.get("channel_id"), meta.get("channel_name"), meta.get("author"), meta.get("owner"),
            meta.get("username"), meta.get("handle"), meta.get("uploader"), meta.get("page_name"),
            source,
        ]
        handles: Set[str] = set()
        ids: Set[str] = set()
        text = " ".join(str(f or "") for f in fields).lower()

        for match in re.findall(r"@([a-z0-9._-]{3,})", text):
            handles.add(self._normalize_handle(match))

        if platform == "youtube":
            for match in re.findall(r"\b(uc[a-z0-9_-]{10,})\b", text):
                ids.add(match.lower())
        else:
            for token in re.findall(r"\b[a-z0-9._-]{3,}\b", text):
                normalized = self._normalize_handle(token)
                if normalized and token not in {"youtube", "instagram", "facebook", "tiktok", "twitter", "linkedin"}:
                    handles.add(normalized)

        return {"handles": handles, "ids": ids}

    def _collect_verified_creator_identities(self, creator_profile: Dict[str, Any]) -> Dict[str, Any]:
        configs = creator_profile.get("platform_configs") or {}
        creator_handle = self._normalize_handle(creator_profile.get("handle"))
        verified = {
            "domains": set(),
            "urls": set(),
            "course_urls": set(),
            "platform_handles": {},
            "platform_ids": {},
        }

        for domain in (creator_profile.get("official_domains") or []):
            norm = self._normalize_netloc(domain)
            if norm:
                verified["domains"].add(norm)
        for domain in (creator_profile.get("course_domains") or []):
            norm = self._normalize_netloc(domain)
            if norm:
                verified["domains"].add(norm)
        for raw_url in (creator_profile.get("course_base_urls") or []):
            cleaned = str(raw_url or "").strip().rstrip("/").lower()
            if cleaned:
                verified["course_urls"].add(cleaned)
                verified["urls"].add(cleaned)
                verified["domains"].add(self._normalize_netloc(urlparse(cleaned).netloc))

        def add_platform_signal(platform: str, handle: Any = "", ident: Any = "", verified_url: Any = "", confidence: float = 1.0):
            if confidence < 0.8:
                return
            platform = (platform or "").lower().strip() or "web"
            if handle:
                verified["platform_handles"].setdefault(platform, set()).add(self._normalize_handle(handle))
            if ident:
                verified["platform_ids"].setdefault(platform, set()).add(self._normalize_handle(ident))
            if verified_url:
                cleaned = str(verified_url).strip().rstrip("/").lower()
                if cleaned:
                    verified["urls"].add(cleaned)
                    verified["domains"].add(self._normalize_netloc(urlparse(cleaned).netloc))

        if creator_handle:
            add_platform_signal("web", creator_handle)
        add_platform_signal("youtube", creator_profile.get("youtube_handle"), creator_profile.get("youtube_channel_id"))

        for platform, cfg in configs.items():
            if not isinstance(cfg, dict):
                continue
            add_platform_signal(
                platform,
                cfg.get("handle") or cfg.get("username"),
                cfg.get("channel_id") or cfg.get("id"),
                cfg.get("verified_url") or cfg.get("url"),
                float(cfg.get("social_confidence") or 0.0),
            )

        return verified


    def _enforce_cog(self, candidates: List[Dict[str, Any]], creator_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        creator_name = (creator_profile.get('name') or '').strip().lower()
        creator_handle = self._normalize_handle(creator_profile.get('handle'))
        if not creator_name and creator_handle:
            creator_name = creator_handle.replace('_', ' ')

        verified_identities = self._collect_verified_creator_identities(creator_profile)
        creator_tokens = [t for t in re.split(r'\W+', creator_name) if len(t) > 2]
        affiliated_markers = {'interview', 'podcast', 'guest', 'featuring', 'hosted by', 'conversation', 'sermon', 'message', 'church', 'conference', 'ministries', 'ministry'}

        verified = []
        for candidate in candidates:
            url = str(candidate.get('url') or '').strip()
            if not url:
                continue
            lower_url = url.lower()
            title = str(candidate.get('title') or '').lower()
            snippet = str(candidate.get('snippet') or '').lower()
            source = str(candidate.get('source') or candidate.get('source_opt') or '').lower()
            haystack = ' '.join([title, snippet, source])

            url_signals = self._extract_owner_signals_from_url(lower_url)
            platform = (candidate.get('platform') or url_signals['platform'] or 'web').lower()
            meta_signals = self._extract_owner_signals_from_candidate(candidate, platform)
            candidate_handles = set(url_signals['handles']) | set(meta_signals['handles'])
            candidate_ids = set(url_signals['ids']) | set(meta_signals['ids'])
            host = url_signals['host']

            direct_self = False
            verified_platform_handles = set(verified_identities['platform_handles'].get(platform, set()))
            verified_platform_ids = set(verified_identities['platform_ids'].get(platform, set()))

            if any(lower_url.startswith(prefix) for prefix in verified_identities['urls'] if prefix):
                direct_self = True
            elif any(lower_url.startswith(prefix) for prefix in verified_identities['course_urls'] if prefix):
                direct_self = True
            elif host in verified_identities['domains'] and platform == 'web':
                direct_self = True
            elif verified_platform_handles and candidate_handles.intersection(verified_platform_handles):
                direct_self = True
            elif verified_platform_ids and candidate_ids.intersection(verified_platform_ids):
                direct_self = True

            relation = 'OTHER'
            score = 0.0
            if direct_self:
                relation = 'SELF'
                score = 1.0
            else:
                has_creator_name = bool(creator_name and creator_name in haystack)
                token_hits = sum(1 for token in creator_tokens if token in haystack)
                has_affiliate_marker = any(marker in haystack for marker in affiliated_markers)
                domain_affiliated = any(term in host for term in ['podcast', 'church', 'ministr', 'conference'])
                creator_threshold = max(1, min(2, len(creator_tokens)))
                if has_creator_name and has_affiliate_marker and (token_hits >= creator_threshold or domain_affiliated):
                    relation = 'AFFILIATED'
                    score = 0.84 if domain_affiliated else 0.78

            if relation in {'SELF', 'AFFILIATED'}:
                candidate['platform'] = platform
                candidate['relation'] = relation
                candidate['ownership_score'] = score
                candidate['confidence'] = min(1.0, float(candidate.get('confidence', 0.5) or 0.5) * score)
                logger.info(f"ResearchProvider: Accepted candidate '{candidate.get('title')}' as {relation} (score={score})")
                verified.append(candidate)

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
                verified = [
                    v for v in verified
                    if v.get("relation") == "SELF" and self._is_direct_video_url(v.get("url", ""))
                ]
            else:
                verified = []  # Fail closed
                
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


    def _title_only_result(self, title: str, snippet: str = "", confidence: float = 0.45) -> Optional[Dict[str, Any]]:
        title = (title or "").strip()
        if not title or title.lower() in {'youtube video', 'untitled', 'video'}:
            return None
        return {
            "title": title,
            "url": "",
            "snippet": (snippet or "")[:300],
            "resource_type": "video",
            "relation": "SELF",
            "confidence": confidence,
            "_title_only": True,
        }

    def _recover_video_urls_by_title(self, creator_name: str, title_only_candidates: List[Dict[str, Any]], exclude_instruction: str = "") -> List[Dict[str, Any]]:
        recovered: List[Dict[str, Any]] = []
        seen_titles = set()
        for candidate in title_only_candidates[:4]:
            title = (candidate.get('title') or '').strip()
            if not title or title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())
            prompt = (
                f'Find the direct public URL for this exact creator-owned video by {creator_name}.\n\n'
                f'Exact title: "{title}"\n\n'
                'Allowed platforms: YouTube, Instagram Reels, TikTok, Facebook Reels/Watch, Twitter/X.\n'
                'Return only direct post/video URLs from those platforms.\n'
                'If you cannot verify a direct URL from actual search results, return an empty array [].'
                f'{exclude_instruction}'
            )
            try:
                attempts = self._search_responses_api(prompt, creator_name)
                if not attempts:
                    attempts = self._search_chat_completions(prompt, creator_name)
                for result in attempts:
                    url = result.get('url') or ''
                    if not url or self._is_placeholder_url(url) or not self._is_direct_video_url(url):
                        continue
                    result.setdefault('title', title)
                    result.setdefault('resource_type', 'video')
                    result.setdefault('relation', 'SELF')
                    result.setdefault('confidence', 0.72)
                    result['platform'] = self._infer_platform_from_url(url)
                    recovered.append(result)
                    break
            except Exception as e:
                logger.warning(f'[SEARCH-RECOVERY] Failed title recovery for {title!r}: {e}')
        return recovered

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

    def _select_event_public_results(
        self, results: List[Dict[str, Any]], creator_profile: Dict[str, Any], topic_query: str
    ) -> List[Dict[str, Any]]:
        creator_name = (creator_profile.get('name') or '').strip().lower()
        creator_tokens = [t for t in re.split(r'\W+', creator_name) if len(t) > 2]
        verified = self._collect_verified_creator_identities(creator_profile)
        topic_terms = [t for t in re.split(r'\W+', (topic_query or '').lower()) if len(t) > 2]
        event_terms = {'event', 'conference', 'gathering', 'register', 'registration', 'ticket', 'tickets', 'venue', 'arena', 'summit', 'access', 'prayer', 'livestream'}

        selected: List[Dict[str, Any]] = []
        for result in results:
            url = str(result.get('url') or '').strip()
            if not url:
                continue
            host = self._normalize_netloc(urlparse(url).netloc)
            text_blob = ' '.join([
                str(result.get('title') or ''),
                str(result.get('snippet') or ''),
                url,
            ]).lower()
            topic_hits = sum(1 for term in topic_terms if term in text_blob)
            creator_hits = 1 if creator_name and creator_name in text_blob else 0
            creator_hits += sum(1 for token in creator_tokens if token in text_blob)
            has_event_term = any(term in text_blob for term in event_terms)
            trusted_host = host in verified['domains'] or any(term in host for term in ['church', 'ministr', 'conference', 'arena', 'event', 'ticket'])
            if not has_event_term or not trusted_host:
                continue
            if topic_hits < 1 and creator_hits < 1:
                continue

            candidate = dict(result)
            candidate.setdefault('platform', self._infer_platform_from_url(url))
            candidate['relation'] = 'SELF' if host in verified['domains'] else 'PUBLIC_FACTS'
            candidate['ownership_score'] = max(float(candidate.get('ownership_score') or 0.0), 0.62 if host in verified['domains'] else 0.46)
            candidate['confidence'] = max(float(candidate.get('confidence') or 0.0), 0.78 if host in verified['domains'] else 0.68)
            selected.append(candidate)

        selected.sort(key=lambda item: (
            item.get('relation') == 'SELF',
            float(item.get('confidence') or 0.0),
        ), reverse=True)
        return selected

    def _score_relevance(
        self, results: List[Dict[str, Any]], topic_query: str, search_intent: str
    ) -> List[Dict[str, Any]]:
        """
        Score and sort results by how well their title matches the topic query.
        """
        import re

        topic_lower = (topic_query or "").lower()
        stop_words = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'shall', 'can', 'to', 'of', 'in', 'for',
            'on', 'with', 'at', 'by', 'from', 'as', 'into', 'about', 'i', 'me',
            'my', 'you', 'your', 'we', 'our', 'they', 'them', 'it', 'its', 'and',
            'or', 'but', 'not', 'no', 'what', 'which', 'who', 'how', 'when', 'where',
            'that', 'this', 'these', 'those', 'video', 'videos', 'watch', 'link'
        }
        topic_keywords = [w for w in re.split(r'\W+', topic_lower) if w and w not in stop_words and len(w) > 1]
        if not topic_keywords:
            return results

        scored = []
        for result in results:
            title_lower = (result.get('_real_title') or result.get('title') or '').lower()
            snippet_lower = (result.get('snippet') or '').lower()
            title_hits = sum(1 for kw in topic_keywords if kw in title_lower)
            snippet_hits = sum(1 for kw in topic_keywords if kw in snippet_lower)
            relevance_score = (title_hits * 2) + snippet_hits
            result['_relevance_score'] = relevance_score
            domain = result.get('_domain', '')
            trust = 1.0 if ('youtube.com' in domain or 'youtu.be' in domain) else 0.2
            ownership = result.get('ownership_score', 0.0)
            result['_evidence_score'] = (ownership * 2) + trust + min(2.0, relevance_score / 2.0)
            scored.append(result)

        scored.sort(key=lambda x: (
            x.get('relation') == 'SELF',
            x.get('_evidence_score', 0),
            x.get('confidence', 0)
        ), reverse=True)
        return scored

    def search(
        self,
        query: str,
        creator_profile: Dict[str, Any],
        resource_type: str = "any",
        conversation_history: Optional[List[Dict[str, str]]] = None,
        intent_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []

        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        t0 = _time.time()
        creator_id = creator_profile.get('id')
        creator_name = self._resolve_creator_name(creator_profile)
        query_lower = (query or '').lower().strip()
        topic_query = self._extract_topic_from_context(query, creator_name, conversation_history)
        sanitized_topic = self._sanitize_topic_for_query(topic_query or query)
        extracted_phrase = self._extract_phrase_from_topic(query)
        search_mode = 'PHRASE_HUNT' if extracted_phrase else 'STANDARD'

        event_fact_query = ((intent_metadata or {}).get("intent") or "").upper() == "EVENT_PUBLIC_FACTS" or needs_fresh_public_web_search(query, conversation_history)

        if resource_type == 'video':
            search_intent = 'VIDEO'
        elif event_fact_query:
            search_intent = 'EVENT'
        elif any(token in query_lower for token in ['course', 'program', 'community', 'product']):
            search_intent = 'PRODUCT'
        elif any(token in query_lower for token in ['instagram', 'tiktok', 'facebook', 'twitter', 'x.com', 'linkedin', 'profile']):
            search_intent = 'SOCIAL'
        else:
            search_intent = 'WEB'

        exclude_urls = []
        if conversation_history:
            recent_blob = ' '.join((m.get('content') or m.get('text') or '') for m in conversation_history[-12:])
            exclude_urls = re.findall(r"https?://[^\s\)\]\"']+", recent_blob)
        exclude_instruction = ''
        if exclude_urls:
            joined = ', '.join(exclude_urls[:8])
            exclude_instruction = f"\nDo not return these URLs again: {joined}."

        cache_salt = f"{search_intent}:{search_mode}:{sanitized_topic.lower()}"
        if creator_id:
            cached = self._get_cache(creator_id, query, 'openai_native_search_v3', cache_salt=cache_salt)
            if cached:
                logger.info(f"OpenAISearch: Cache hit for '{query}'")
                return cached

        if search_intent == 'VIDEO':
            search_prompt = (
                f"Find creator-owned or strongly affiliated videos for {creator_name} specifically about {sanitized_topic}.\n\n"
                "Allowed platforms: YouTube, Instagram Reels, TikTok, Facebook Reels/Watch, Twitter/X.\n"
                "Return only direct public post/video URLs.\n"
                "If a result is from a church, podcast, interview, or conference, include it only if the creator is clearly the speaker or guest.\n"
                "Do not guess URLs. Do not use placeholders like VIDEO_ID, REEL_ID, or POST_ID."
                f"{exclude_instruction}"
            )
        elif search_intent == 'EVENT':
            search_prompt = (
                f"Find current public event information for {creator_name} related to {sanitized_topic}.\n"
                "Prioritize official event pages, registration pages, venue pages, church or ministry pages, and recent announcements.\n"
                "Focus on next or upcoming dates, venue, registration, livestream details, and status.\n"
                "Prefer official sources, but include high confidence public event pages when they clearly refer to the same event.\n"
                "Return real URLs only.\n"
                f"{exclude_instruction}"
            )
        elif search_intent == 'PRODUCT':
            search_prompt = (
                f"Find official products, courses, or programs from {creator_name} related to {sanitized_topic}.\n"
                "Return only official pages or direct videos where the creator discusses the product."
                f"{exclude_instruction}"
            )
        elif search_intent == 'SOCIAL':
            search_prompt = (
                f"Find the official social profile or page for {creator_name} related to this request: {query}.\n"
                "Return only official public URLs if you can verify them."
                f"{exclude_instruction}"
            )
        else:
            search_prompt = (
                f"Find reliable public web results about {creator_name} related to {sanitized_topic}.\n"
                "Prefer official creator-owned pages and strongly related appearances.\n"
                "Return real URLs only."
                f"{exclude_instruction}"
            )

        results = []
        try:
            results = self._search_responses_api(search_prompt, creator_name)
        except Exception as exc:
            logger.warning(f"OpenAISearch: Responses API failed: {exc}")
        if not results:
            try:
                results = self._search_chat_completions(search_prompt, creator_name)
            except Exception as exc:
                logger.warning(f"OpenAISearch: Chat Completions fallback failed: {exc}")
                results = []

        title_only_candidates = [r for r in results if isinstance(r, dict) and r.get('_title_only')]
        results = [r for r in results if isinstance(r, dict) and r.get('url')]

        if exclude_urls and results:
            results = [r for r in results if not any(ex in (r.get('url') or '') for ex in exclude_urls)]

        if results:
            results = self._prefilter_candidates(results, creator_profile)
            enforced_results = self._enforce_cog(results, creator_profile)
            if search_intent == 'EVENT' and not enforced_results:
                enforced_results = self._select_event_public_results(results, creator_profile, sanitized_topic or query)
            results = enforced_results

        pre_validated = []
        for result in results:
            url = result.get('url') or ''
            if not url or self._is_placeholder_url(url):
                continue
            if self._is_echoed_title(result.get('title', ''), query, conversation_history):
                continue
            is_video_url = self._is_direct_video_url(url)
            if search_intent == 'VIDEO' and not is_video_url:
                continue
            result.setdefault('confidence', 0.8)
            result.setdefault('relation', 'PUBLIC_FACTS')
            result.setdefault('snippet', '')
            result.setdefault('title', 'Untitled')
            result.setdefault('resource_type', 'video' if is_video_url else 'web')
            result.setdefault('platform', self._infer_platform_from_url(url))
            pre_validated.append(result)

        youtube_items = [(i, r) for i, r in enumerate(pre_validated) if 'youtube.com' in (r.get('url') or '') or 'youtu.be' in (r.get('url') or '')]
        if youtube_items:
            validation_results = {}
            def validate_item(args):
                idx, result = args
                return idx, self._validate_youtube_url(result['url'])
            with ThreadPoolExecutor(max_workers=min(len(youtube_items), 4)) as executor:
                futures = {executor.submit(validate_item, item): item[0] for item in youtube_items}
                for future in as_completed(futures):
                    try:
                        idx, real_title = future.result(timeout=6)
                        validation_results[idx] = real_title
                    except Exception:
                        validation_results[futures[future]] = None
            validated = []
            for idx, result in enumerate(pre_validated):
                if idx in validation_results:
                    real_title = validation_results[idx]
                    if real_title is None:
                        continue
                    if result.get('title', '').strip().lower() in ('youtube video', 'untitled', ''):
                        result['title'] = real_title
                    else:
                        result['_real_title'] = real_title
                validated.append(result)
        else:
            validated = pre_validated

        results = self._score_relevance(validated, sanitized_topic or query, search_intent)

        if search_intent == 'VIDEO' and title_only_candidates and len(results) < 2:
            recovered = self._recover_video_urls_by_title(creator_name, title_only_candidates, exclude_instruction=exclude_instruction)
            if recovered:
                recovered = self._enforce_cog(recovered, creator_profile)
                existing_urls = {r.get('url') for r in results}
                for recovered_result in recovered:
                    if recovered_result.get('url') not in existing_urls:
                        recovered_result.setdefault('platform', self._infer_platform_from_url(recovered_result.get('url', '')))
                        recovered_result.setdefault('resource_type', 'video')
                        results.append(recovered_result)
                        existing_urls.add(recovered_result.get('url'))
                results = self._score_relevance(results, sanitized_topic or query, search_intent)

        if search_intent == 'VIDEO':
            strict_results = []
            for result in results:
                confidence = float(result.get('confidence') or 0.0)
                relation = result.get('relation') or 'OTHER'
                topic_score = float(result.get('_relevance_score') or 0.0)
                if relation == 'SELF' and confidence >= 0.72 and topic_score >= 1:
                    strict_results.append(result)
                elif relation == 'AFFILIATED' and confidence >= 0.8 and topic_score >= 1:
                    strict_results.append(result)
            results = strict_results

        for result in results:
            if result.get('_real_title'):
                result['title'] = result.pop('_real_title')

        if creator_id and results:
            self._save_cache(creator_id, query, 'openai_native_search_v3', results, cache_salt=cache_salt)

        elapsed = _time.time() - t0
        logger.info(f"OpenAISearch: Completed in {elapsed:.1f}s - {len(results)} final results")
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
        seen = {(r.get('url') or f"title:{(r.get('title') or '').lower().strip()}") for r in json_results}
        combined = list(json_results)
        for r in text_results:
            dedupe_key = r.get('url') or f"title:{(r.get('title') or '').lower().strip()}"
            if dedupe_key not in seen:
                combined.append(r)
                seen.add(dedupe_key)
                
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
        seen = {(r.get('url') or f"title:{(r.get('title') or '').lower().strip()}") for r in json_results}
        combined = list(json_results)
        for r in text_results:
            dedupe_key = r.get('url') or f"title:{(r.get('title') or '').lower().strip()}"
            if dedupe_key not in seen:
                combined.append(r)
                seen.add(dedupe_key)
                
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
        from urllib.parse import parse_qs, urlparse
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

