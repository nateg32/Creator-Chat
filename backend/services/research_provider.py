import hashlib
import ipaddress
import json
import logging
import re
import socket
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Set
from urllib.parse import parse_qs, urlparse
import requests
from backend.db import db
from backend.settings import settings
import backend.rag as rag
from backend.services.live_search_rules import needs_fresh_public_web_search
from backend.services.creator_fact_policy import classify_creator_fact_query

logger = logging.getLogger(__name__)


def _settings_value(name: str, default: Any = "") -> Any:
    return getattr(settings, name, default)


def _gemini_api_key() -> str:
    return str(_settings_value("GEMINI_API_KEY") or _settings_value("GOOGLE_API_KEY") or "")


_GROUNDING_REDIRECT_HOSTS = {
    "vertexaisearch.cloud.google.com",
    "vertexaisearch.cloud.googleusercontent.com",
}
_GROUNDING_REDIRECT_QUERY_KEYS = (
    "url",
    "q",
    "target",
    "dest",
    "destination",
    "redirect",
    "redirect_url",
)
_BARE_DOMAIN_RE = re.compile(r"^(?:www\.)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s]*)?$")


def _is_safe_public_url(url: str, allowed_hosts: Optional[Set[str]] = None) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    if allowed_hosts and host not in allowed_hosts:
        return False
    if host in {"localhost", "localhost.localdomain"}:
        return False
    try:
        addr = ipaddress.ip_address(host)
        return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_unspecified)
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False
        for item in resolved:
            raw_addr = (item[4] or [""])[0].split("%", 1)[0]
            try:
                addr = ipaddress.ip_address(raw_addr)
            except ValueError:
                continue
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_unspecified:
                return False
        return True


def _normalize_grounding_url(raw_url: str, title: str = "") -> str:
    url = str(raw_url or "").strip()
    if not url:
        return ""

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]

    is_redirect = host in _GROUNDING_REDIRECT_HOSTS or "grounding-api-redirect" in (parsed.path or "")
    if not is_redirect:
        return url

    query = parse_qs(parsed.query or "")
    for key in _GROUNDING_REDIRECT_QUERY_KEYS:
        for candidate in query.get(key) or []:
            candidate = str(candidate or "").strip()
            if candidate.startswith(("http://", "https://")):
                return candidate

    bare_title = str(title or "").strip()
    if bare_title and _BARE_DOMAIN_RE.fullmatch(bare_title):
        if not bare_title.startswith(("http://", "https://")):
            bare_title = f"https://{bare_title}"
        return bare_title

    return url

class ResearchProvider(ABC):
    @abstractmethod
    def search(self, query: str, creator_profile: Dict[str, Any], resource_type: str = "any", conversation_history: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, Any]]:
        pass

    def lookup_public_fact(
        self,
        query: str,
        creator_profile: Dict[str, Any],
        *,
        fact_field: str = "",
        entity_subject: str = "",
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        return {}

    def lookup_creator_entities(
        self,
        query: str,
        creator_profile: Dict[str, Any],
        *,
        entity_type: str = "",
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        return {}

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

    def _coerce_platform_configs(self, creator_profile: Dict[str, Any]) -> Dict[str, Any]:
        configs = (creator_profile or {}).get("platform_configs") or {}
        if isinstance(configs, str):
            try:
                configs = json.loads(configs)
            except Exception:
                configs = {}
        return configs if isinstance(configs, dict) else {}

    def _creator_niche_text(self, creator_profile: Dict[str, Any]) -> str:
        profile = creator_profile or {}
        raw_parts = [
            profile.get("creator_category"),
            profile.get("creator_archetype"),
        ]
        style_fp = profile.get("style_fingerprint") or {}
        if isinstance(style_fp, str):
            try:
                style_fp = json.loads(style_fp)
            except Exception:
                style_fp = {}
        if isinstance(style_fp, dict):
            search_profile = style_fp.get("search_profile") or {}
            domain_map = style_fp.get("domain_map") or {}
            if isinstance(search_profile, dict):
                raw_parts.extend([
                    search_profile.get("primary_category"),
                    search_profile.get("creator_lane"),
                    " ".join(str(item) for item in (search_profile.get("search_identity_terms") or [])[:4])
                    if isinstance(search_profile.get("search_identity_terms"), list)
                    else "",
                    " ".join(str(item) for item in (search_profile.get("disambiguation_terms") or [])[:4])
                    if isinstance(search_profile.get("disambiguation_terms"), list)
                    else "",
                ])
            if isinstance(domain_map, dict):
                raw_parts.extend([
                    domain_map.get("creator_lane"),
                    " ".join(str(item) for item in (domain_map.get("strong_topics") or [])[:4])
                    if isinstance(domain_map.get("strong_topics"), list)
                    else "",
                ])
        stronghold = profile.get("stronghold_json") or {}
        if isinstance(stronghold, str):
            try:
                stronghold = json.loads(stronghold)
            except Exception:
                stronghold = {}
        if isinstance(stronghold, dict):
            raw_parts.extend([
                stronghold.get("primary_domain"),
                stronghold.get("creator_focus"),
                " ".join(str(item) for item in (stronghold.get("secondary_domains") or [])[:2])
                if isinstance(stronghold.get("secondary_domains"), list)
                else "",
            ])
        configs = self._coerce_platform_configs(profile)
        enabled_platforms = [
            key.replace("_", " ")
            for key, cfg in configs.items()
            if isinstance(cfg, dict) and cfg.get("enabled") is True
        ]
        raw_parts.extend(enabled_platforms[:2])
        text = re.sub(r"\s+", " ", " ".join(str(part or "") for part in raw_parts)).strip()
        generic = {"general", "creator", "content creator", "influencer", "youtube", "youtuber", "podcast", "social media"}
        return "" if text.lower() in generic else " ".join(text.split()[:8])

    def _creator_identity_anchor(self, creator_profile: Dict[str, Any]) -> str:
        profile = creator_profile or {}
        name = re.sub(r"\s+", " ", str(profile.get("name") or profile.get("handle") or "creator").strip())
        handle = self._normalize_handle(profile.get("handle"))
        configs = self._coerce_platform_configs(profile)
        for cfg in configs.values():
            if not handle and isinstance(cfg, dict):
                handle = self._normalize_handle(cfg.get("handle") or cfg.get("username"))
        niche = self._creator_niche_text(profile)
        parts: List[str] = []
        if name:
            parts.append(f'"{name}"' if len(name.split()) >= 2 else name)
        if handle:
            parts.append(f"@{handle}")
        if niche:
            parts.append(niche)
        return " ".join(parts).strip() or name or "creator"

    def _creator_niche_terms(self, creator_profile: Dict[str, Any]) -> List[str]:
        niche = self._creator_niche_text(creator_profile)
        stop = {
            "the", "and", "for", "with", "from", "about", "creator", "content",
            "youtube", "instagram", "tiktok", "facebook", "twitter", "linkedin",
        }
        return [
            term
            for term in re.split(r"[^a-z0-9]+", niche.lower())
            if len(term) >= 4 and term not in stop
        ][:8]

    def _candidate_identity_signals(
        self,
        candidate: Dict[str, Any],
        creator_profile: Dict[str, Any],
        *,
        platform: str = "",
        candidate_handles: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        name = re.sub(r"\s+", " ", str((creator_profile or {}).get("name") or "")).strip().lower()
        handle = self._normalize_handle((creator_profile or {}).get("handle"))
        configs = self._coerce_platform_configs(creator_profile or {})
        if not handle:
            for cfg in configs.values():
                if isinstance(cfg, dict):
                    handle = self._normalize_handle(cfg.get("handle") or cfg.get("username"))
                    if handle:
                        break

        title = str(candidate.get("title") or "").lower()
        snippet = str(candidate.get("snippet") or "").lower()
        source = str(candidate.get("source") or candidate.get("source_opt") or "").lower()
        url = str(candidate.get("url") or "").lower()
        haystack = " ".join([title, snippet, source, url])
        title_has_name = bool(name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", title))
        haystack_has_name = bool(name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", haystack))
        tokens = [t for t in re.split(r"\W+", name) if len(t) > 2]
        all_name_tokens = bool(tokens) and sum(1 for token in tokens if token in haystack) >= len(tokens)
        handle_values = set(candidate_handles or set())
        handle_match = bool(handle and (handle in handle_values or re.search(rf"(?<![a-z0-9._-])@?{re.escape(handle)}(?![a-z0-9._-])", haystack)))
        niche_terms = self._creator_niche_terms(creator_profile or {})
        niche_hits = sum(1 for term in niche_terms if term in haystack)
        return {
            "title_has_name": title_has_name,
            "haystack_has_name": haystack_has_name,
            "all_name_tokens": all_name_tokens,
            "handle_match": handle_match,
            "niche_hits": niche_hits,
            "has_identity": bool(title_has_name or haystack_has_name or all_name_tokens or handle_match),
        }

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
        if "spotify.com" in host:
            return "spotify"
        if "podcasts.apple.com" in host:
            return "apple_podcasts"
        return "web"

    def _is_direct_video_url(self, url: str) -> bool:
        parsed = urlparse(url or "")
        host = self._normalize_netloc(parsed.netloc)
        path = (parsed.path or "").strip("/")
        query = parse_qs(parsed.query or "")

        if "youtube.com" in host:
            if query.get("v"):
                return True
            return path.startswith("shorts/")
        if "youtu.be" in host:
            return bool(path)
        if "instagram.com" in host:
            first = path.split("/", 1)[0].lower() if path else ""
            return first in {"reel", "reels", "p", "tv"}
        if "tiktok.com" in host:
            return "/video/" in f"/{path.lower()}/"
        if "facebook.com" in host or "fb.watch" in host:
            lowered = f"/{path.lower()}/"
            return (
                lowered.startswith("/watch/")
                or "/watch/" in lowered
                or lowered.startswith("/reel/")
                or "/reel/" in lowered
                or lowered.startswith("/share/v/")
                or "videos/" in lowered
            )
        if "x.com" in host or "twitter.com" in host:
            lowered = f"/{path.lower()}/"
            return "/status/" in lowered
        return False

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
        if isinstance(configs, str):
            try:
                configs = json.loads(configs)
            except Exception:
                configs = {}
        if not isinstance(configs, dict):
            configs = {}
        creator_handle = self._normalize_handle(creator_profile.get("handle"))
        verified = {
            "domains": set(),
            "urls": set(),
            "course_urls": set(),
            "platform_handles": {},
            "platform_ids": {},
        }

        def coerce_list(value: Any) -> List[Any]:
            if not value:
                return []
            if isinstance(value, list):
                return value
            if isinstance(value, tuple):
                return list(value)
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return []
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        return parsed
                except Exception:
                    pass
                return [part.strip() for part in re.split(r"[,\n]+", stripped) if part.strip()]
            return [value]

        for domain in coerce_list(creator_profile.get("official_domains")):
            norm = self._normalize_netloc(domain)
            if norm:
                verified["domains"].add(norm)
        for domain in coerce_list(creator_profile.get("course_domains")):
            norm = self._normalize_netloc(domain)
            if norm:
                verified["domains"].add(norm)
        for raw_url in coerce_list(creator_profile.get("course_base_urls")):
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
            identity_signals = self._candidate_identity_signals(
                candidate,
                creator_profile,
                platform=platform,
                candidate_handles=candidate_handles,
            )

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
                has_creator_name = bool(identity_signals["haystack_has_name"])
                token_hits = sum(1 for token in creator_tokens if token in haystack)
                has_affiliate_marker = any(marker in haystack for marker in affiliated_markers)
                domain_affiliated = any(term in host for term in ['podcast', 'church', 'ministr', 'conference'])
                creator_threshold = max(1, min(2, len(creator_tokens)))
                has_niche_baseline = bool(self._creator_niche_terms(creator_profile))
                strong_identity_context = bool(
                    identity_signals["handle_match"]
                    or identity_signals["niche_hits"] > 0
                    or (identity_signals["title_has_name"] and not has_niche_baseline)
                )
                if (
                    identity_signals["has_identity"]
                    and has_affiliate_marker
                    and (token_hits >= creator_threshold or domain_affiliated)
                    and strong_identity_context
                ):
                    relation = 'AFFILIATED'
                    score = 0.84 if domain_affiliated else 0.82
                elif candidate.get('is_public_info') and identity_signals["has_identity"]:
                    # Gemini-grounded public fact result (e.g. Amazon book listing, Wikipedia, Goodreads).
                    # Gemini already validated this result was returned for a query about this creator.
                    # Trust it when the creator's name appears in the content.
                    relation = 'PUBLIC_FACT_VERIFIED'
                    score = float(candidate.get('confidence') or 0.82)
                    if not strong_identity_context:
                        # Same-name collisions often mention the creator name only in a snippet while
                        # the actual page title/domain belongs to someone else. Keep it available only
                        # when the later topic scorer can prove relevance.
                        score = min(score, 0.56)

            if relation in {'SELF', 'AFFILIATED', 'PUBLIC_FACT_VERIFIED'}:
                candidate['platform'] = platform
                candidate['relation'] = relation
                candidate['ownership_score'] = score
                base_confidence = float(candidate.get('confidence', 0.5) or 0.5)
                candidate['confidence'] = min(1.0, max(base_confidence * score, score))
                logger.info(f"ResearchProvider: Accepted candidate '{candidate.get('title')}' as {relation} (score={score})")
                verified.append(candidate)

        verified.sort(key=lambda x: (x['relation'] == 'SELF', x['confidence']), reverse=True)
        return verified

class GeminiResearchProvider(ResearchProvider):
    def __init__(self):
        self.enabled = bool(_gemini_api_key())
        self.api_key = _gemini_api_key()
        self.model_name = _settings_value("GEMINI_GROUNDING_MODEL", "gemini-2.0-flash")
        self.base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"

    def _resolve_creator_name(self, creator_profile: Dict[str, Any]) -> str:
        return (
            creator_profile.get("name")
            or creator_profile.get("handle")
            or "The Creator"
        )

    def _build_grounding_query_plan(
        self,
        query: str,
        creator_profile: Dict[str, Any],
        conversation_history: Optional[List[Dict[str, str]]] = None,
        max_queries: int = 4,
    ) -> List[str]:
        creator_name = self._resolve_creator_name(creator_profile)
        creator_anchor = self._creator_identity_anchor(creator_profile)
        creator_niche = self._creator_niche_text(creator_profile)
        queries: List[str] = []

        # Detect bibliographic queries — books, co-authorship, publications, biography facts.
        # These need to search Amazon, Goodreads, Wikipedia, and publisher/press sites, not just
        # creator-owned URLs. A co-authored book on Amazon is a public bibliographic record.
        _BIO_QUERY_WORDS = {
            'book', 'books', 'written', 'co-wrote', 'cowrite', 'co-author', 'coauthor',
            'author', 'authored', 'published', 'publication', 'bibliography', 'wrote',
        }
        is_bibliographic = bool(_BIO_QUERY_WORDS.intersection(set(re.findall(r'[a-z]+', query.lower()))))

        if settings.OPENAI_API_KEY:
            bio_instruction = (
                "- For book/publication/authorship queries: include queries targeting Amazon, Goodreads, "
                "publisher sites, and Wikipedia. Do NOT restrict to creator-owned URLs for bibliographic queries. "
                "Include a query like: \'\"creator name\" co-author book 2024 Amazon Goodreads\'.\n"
                "- Co-authored and multi-author books count as the creator\'s own work."
                if is_bibliographic else
                "- Prefer creator-owned or official public sources first."
            )
            planner_prompt = f"""
You are building a live search plan for creator research.
Creator: {creator_name}
Creator identity anchor: {creator_anchor}
Creator field/category: {creator_niche or "Unknown"}
User query: {query}
Recent context: {json.dumps((conversation_history or [])[-4:])}

Generate 2 to {max_queries} distinct web search queries that together maximize source fidelity.
Rules:
- Include the creator name in every query.
- Include at least one category/identity anchor term in every query when available.
- Make the queries specific, not vague.
- Do not include quotation marks around the whole query.
- Avoid same-name drift: the results must match the creator identity anchor, not just the name.
{bio_instruction}

Return JSON only:
{{"queries": ["query 1", "query 2"]}}
"""
            try:
                planned = rag.generate_chat_completion(
                    messages=[
                        {"role": "system", "content": "You generate search query plans."},
                        {"role": "user", "content": planner_prompt},
                    ],
                    model=settings.MODEL_VERIFY,
                    json_mode=True,
                    temperature=0.1,
                )
                data = self._parse_json(planned)
                for candidate in (data or {}).get("queries") or []:
                    cleaned = str(candidate or "").strip()
                    if cleaned:
                        queries.append(cleaned)
            except Exception as exc:
                logger.warning(f"GeminiResearch: grounding query plan failed: {exc}")

        if not queries:
            base_anchor = creator_anchor or creator_name
            queries = [f"{base_anchor} {query}".strip()]
            if len(query.split()) >= 4:
                queries.append(f"{base_anchor} official {query}".strip())
            if creator_profile.get("official_domains"):
                domain = str((creator_profile.get("official_domains") or [""])[0] or "").strip()
                if domain:
                    queries.append(f"site:{domain} {base_anchor} {query}".strip())

        deduped: List[str] = []
        seen = set()
        for candidate in queries:
            key = candidate.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(candidate.strip())
            if len(deduped) >= max_queries:
                break
        return deduped[:max_queries] or [query]

    def _call_gemini_rest(self, prompt: str, search_enabled: bool = True) -> Optional[Dict[str, Any]]:
        if not self.enabled: return None
        
        url = f"{self.base_url}?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}] if search_enabled else []
        }
        prompt_preview = re.sub(r"\s+", " ", prompt).strip()[:500]
        logger.info(f"[SEARCH_TRACE] provider_request: {prompt_preview}")
        
        import time
        for attempt in range(2): # Retry only for explicit rate limiting.
            try:
                response = requests.post(url, json=payload, timeout=settings.GEMINI_REST_TIMEOUT_SECONDS)
                if response.status_code == 429:
                    wait = 1
                    logger.warning(f"GeminiResearch: 429 Rate Limit. Waiting {wait}s... (Attempt {attempt+1}/2)")
                    time.sleep(wait)
                    continue
                    
                if response.status_code != 200:
                    logger.error(f"GeminiResearch REST Error {response.status_code}: {response.text}")
                    return None
                body = response.text[:500]
                logger.info(f"[SEARCH_TRACE] provider_response: {body}")
                return response.json()
            except requests.exceptions.Timeout as e:
                logger.warning(f"[SEARCH_TRACE] provider_timeout timeout={settings.GEMINI_REST_TIMEOUT_SECONDS}s error={e}")
                return None
            except Exception as e:
                logger.warning(f"[SEARCH_TRACE] provider_exception: {e}")
                return None
        return None

    def _extract_text_from_response(self, data: Optional[Dict[str, Any]]) -> str:
        if not data:
            return ""
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        full_text = ""
        for part in parts:
            if part.get("text"):
                full_text += part["text"]
        return full_text

    def _extract_grounded_results(self, data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not data:
            return []
        candidates = data.get("candidates") or []
        if not candidates:
            return []
        grounding = candidates[0].get("groundingMetadata") or {}
        chunks = grounding.get("groundingChunks") or []
        supports = grounding.get("groundingSupports") or []
        support_map: Dict[int, List[str]] = {}
        for support in supports:
            segment = support.get("segment") or {}
            text = (segment.get("text") or "").strip()
            for idx in support.get("groundingChunkIndices") or []:
                support_map.setdefault(int(idx), [])
                if text:
                    support_map[int(idx)].append(text)

        results: List[Dict[str, Any]] = []
        seen_urls = set()
        for idx, chunk in enumerate(chunks):
            web = chunk.get("web") or {}
            title = (web.get("title") or "Grounded Web Result").strip()
            url = _normalize_grounding_url(web.get("uri") or "", title=title)
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            snippets = support_map.get(idx) or []
            results.append({
                "title": title,
                "url": url,
                "snippet": " ".join(snippets[:2]).strip(),
                "resource_type": "video" if self._is_direct_video_url(url) else "web",
                "platform": self._infer_platform_from_url(url),
                "confidence": 0.82,
                "relation": "PUBLIC_FACTS",
                "is_public_info": True,
            })
        return results

    def _extract_grounding_package(self, data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        response_text = self._extract_text_from_response(data)
        candidates = (data or {}).get("candidates") or []
        grounding = (candidates[0].get("groundingMetadata") or {}) if candidates else {}
        chunks = grounding.get("groundingChunks") or []
        supports = grounding.get("groundingSupports") or []
        search_entry = grounding.get("searchEntryPoint") or {}

        citations: List[Dict[str, Any]] = []
        seen_keys = set()
        for support in supports:
            segment = support.get("segment") or {}
            segment_text = (segment.get("text") or "").strip()
            start_index = int(segment.get("startIndex") or 0)
            end_index = int(segment.get("endIndex") or (start_index + len(segment_text)))
            for chunk_index in support.get("groundingChunkIndices") or []:
                try:
                    chunk_index = int(chunk_index)
                except Exception:
                    continue
                if chunk_index < 0 or chunk_index >= len(chunks):
                    continue
                web = (chunks[chunk_index] or {}).get("web") or {}
                title = (web.get("title") or "Grounded Web Result").strip()
                url = _normalize_grounding_url(web.get("uri") or "", title=title)
                if not url:
                    continue
                key = (start_index, end_index, url)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                citations.append({
                    "start_index": start_index,
                    "end_index": end_index,
                    "startIndex": start_index,
                    "endIndex": end_index,
                    "text": segment_text,
                    "url": url,
                    "title": title,
                    "chunk_index": chunk_index,
                })

        return {
            "response_text": response_text,
            "citations": citations,
            "search_entry_point": {
                "rendered_content": search_entry.get("renderedContent") or "",
            },
            "grounded_results": self._extract_grounded_results(data),
        }

    def grounded_overview(
        self,
        query: str,
        creator_profile: Dict[str, Any],
        conversation_history: Optional[List[Dict[str, str]]] = None,
        max_queries: int = 4,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {
                "response_text": "",
                "citations": [],
                "search_entry_point": {"rendered_content": ""},
                "query_plan": [query],
                "results": [],
                "sources": [],
                "packages": [],
            }

        creator_name = self._resolve_creator_name(creator_profile)
        query_plan = self._build_grounding_query_plan(
            query,
            creator_profile,
            conversation_history=conversation_history,
            max_queries=max_queries,
        )
        logger.info(f"[SEARCH_TRACE] grounded_query_plan: {query_plan}")

        # Detect bibliographic queries once for the prompt adjustment below.
        _BIO_KEYWORDS = {
            'book', 'books', 'written', 'co-wrote', 'author', 'authored',
            'published', 'co-author', 'bibliography', 'wrote',
        }
        is_bibliographic_query = bool(_BIO_KEYWORDS.intersection(set(re.findall(r'[a-z]+', query.lower()))))

        def run_grounded_query(subquery: str) -> Dict[str, Any]:
            if is_bibliographic_query:
                search_instruction = (
                    "Search Amazon, Goodreads, Wikipedia, publisher/press sites, AND the creator's owned sources. "
                    "For book authorship and co-authorship records, include third-party bibliographic sources — "
                    "these are factual public records, not unrelated content. "
                    "Return all results where the creator is listed as author or co-author."
                )
            else:
                search_instruction = (
                    "Prefer official creator-owned sources and clearly relevant public records."
                )
            prompt = (
                f"Creator: {creator_name}\n"
                f"Original user question: {query}\n"
                f"Focused search objective: {subquery}\n\n"
                f"Use Google Search grounding to answer briefly and only from grounded public sources. "
                f"{search_instruction}"
            )
            raw = self._call_gemini_rest(prompt, search_enabled=True)
            package = self._extract_grounding_package(raw)
            package["subquery"] = subquery
            return package

        packages_by_query: Dict[str, Dict[str, Any]] = {}
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=min(len(query_plan), 4)) as executor:
                futures = {executor.submit(run_grounded_query, subquery): subquery for subquery in query_plan}
                for future in as_completed(futures):
                    try:
                        subquery = futures[future]
                        packages_by_query[subquery] = future.result()
                    except Exception as exc:
                        logger.warning(f"GeminiResearch: grounded query failed for '{futures[future]}': {exc}")
        except Exception as exc:
            logger.warning(f"GeminiResearch: grounded overview parallelism failed: {exc}")
            for subquery in query_plan:
                try:
                    packages_by_query[subquery] = run_grounded_query(subquery)
                except Exception as inner_exc:
                    logger.warning(f"GeminiResearch: grounded query failed for '{subquery}': {inner_exc}")

        merged_results: List[Dict[str, Any]] = []
        merged_citations: List[Dict[str, Any]] = []
        merged_text_parts: List[str] = []
        merged_sources: List[Dict[str, Any]] = []
        entry_point_html = ""
        seen_urls = set()
        seen_citations = set()
        ordered_packages: List[Dict[str, Any]] = []
        text_offsets: Dict[str, int] = {}

        for subquery in query_plan:
            package = packages_by_query.get(subquery) or {}
            if not package:
                continue
            ordered_packages.append(package)
            text = str(package.get("response_text") or "").strip()
            text_offset = 0
            if text:
                if text in text_offsets:
                    text_offset = text_offsets[text]
                else:
                    text_offset = sum(len(part) for part in merged_text_parts)
                    if merged_text_parts:
                        text_offset += 2
                    merged_text_parts.append(text)
                    text_offsets[text] = text_offset
            if not entry_point_html:
                entry_point_html = ((package.get("search_entry_point") or {}).get("rendered_content") or "").strip()
            for citation in package.get("citations") or []:
                adjusted = dict(citation)
                if text:
                    start_index = int(citation.get("start_index") or 0) + text_offset
                    end_index = int(citation.get("end_index") or 0) + text_offset
                else:
                    start_index = int(citation.get("start_index") or 0)
                    end_index = int(citation.get("end_index") or 0)
                adjusted["start_index"] = start_index
                adjusted["end_index"] = end_index
                adjusted["startIndex"] = start_index
                adjusted["endIndex"] = end_index
                adjusted["subquery"] = package.get("subquery")
                key = (adjusted.get("start_index"), adjusted.get("end_index"), adjusted.get("url"))
                if key in seen_citations:
                    continue
                seen_citations.add(key)
                merged_citations.append(adjusted)
                citation_url = str(adjusted.get("url") or "").strip()
                if citation_url and citation_url not in seen_urls:
                    seen_urls.add(citation_url)
                    merged_sources.append({
                        "url": citation_url,
                        "title": adjusted.get("title") or "Grounded Web Result",
                        "resource_type": "web",
                        "platform": self._infer_platform_from_url(citation_url),
                        "subquery": package.get("subquery"),
                    })
            for result in package.get("grounded_results") or []:
                url = (result.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                merged_results.append(result)
                merged_sources.append({
                    "url": url,
                    "title": result.get("title") or "Grounded Web Result",
                    "resource_type": result.get("resource_type") or "web",
                    "platform": result.get("platform") or self._infer_platform_from_url(url),
                    "subquery": package.get("subquery"),
                })

        merged_results = self._enforce_cog(merged_results, creator_profile)
        return {
            "response_text": "\n\n".join(merged_text_parts[:max_queries]).strip(),
            "citations": merged_citations,
            "search_entry_point": {"rendered_content": entry_point_html},
            "query_plan": query_plan,
            "results": merged_results,
            "sources": merged_sources,
            "packages": ordered_packages,
        }

    def lookup_public_fact(
        self,
        query: str,
        creator_profile: Dict[str, Any],
        *,
        fact_field: str = "",
        entity_subject: str = "",
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {}

        creator_name = self._resolve_creator_name(creator_profile)
        subject = (entity_subject or creator_name or "the creator").strip()
        policy = classify_creator_fact_query(
            query,
            query_goal="timeline_lookup" if (fact_field or "").strip().lower() in {"start_date", "publication_date", "launch_date"} else "",
        )
        fact_label_map = {
            "publication_date": "publication or release date",
            "launch_date": "launch or release date",
            "start_date": "when the creator first started that journey or activity",
            "price": "current price",
            "followers": "current follower count",
            "subscribers": "current subscriber count",
            "students": "current student count",
            "members": "current member count",
            "latest_episode": "latest episode or latest release",
            "valuation": "public valuation",
            "net_worth": "public net worth",
        }
        fact_label = fact_label_map.get((fact_field or "").strip().lower(), fact_field or "public fact")
        context_preview = json.dumps((conversation_history or [])[-4:])
        creator_start_rules = ""
        if (fact_field or "").strip().lower() == "start_date" or policy.kind == "creator_start_timeline":
            fact_label = "when the creator first started that journey or activity"
            creator_start_rules = """
    - This is a creator journey start-date question, not a book or product release question.
    - Ignore publication dates, ebook listings, Amazon pages, Audible pages, and retailer release dates unless they explicitly prove when the creator personally started.
    - Prefer interviews, creator-owned pages, biographies, or reputable profiles that directly say when the creator started, began, or got into the activity.
    """

        prompt = f"""
You are verifying one public fact about {creator_name}.

User question: {query}
Target subject: {subject}
Target fact to verify: {fact_label}
Recent context: {context_preview}

Use Google Search grounding and return JSON only.

Rules:
- Prefer official creator-owned sources, publisher/product listings, retailer listings, or high-authority public records.
- For books, prioritize publisher pages, Amazon, Audible, Goodreads, and official creator pages.
{creator_start_rules.rstrip()}
- If you can verify the fact, return:
  {{
    "found": true,
    "fact_field": "{(fact_field or 'public_fact').strip()}",
    "value": "the exact fact value",
        "answer_text": "one natural first-person sentence answering the question directly",
    "confidence": 0.0,
    "source_url": "https://...",
    "source_title": "source title",
    "source_snippet": "supporting source snippet"
  }}
- If you cannot verify it, return the same shape with found=false and empty strings.
- Do not hedge. Do not tell the user to check a listing if the fact is already found.
- Never mention search, evidence, sources, transcripts, or that you pulled anything up.
"""

        raw = self._call_gemini_rest(prompt, search_enabled=True)
        package = self._extract_grounding_package(raw)
        text = self._extract_text_from_response(raw).strip()
        parsed = self._parse_json(text) if text else None
        grounded_results = list(package.get("grounded_results") or [])
        primary = grounded_results[0] if grounded_results else {}
        source_url = str(primary.get("url") or "")
        source_title = str(primary.get("title") or "")
        source_snippet = str(primary.get("snippet") or "")

        result: Dict[str, Any] = {
            "found": False,
            "fact_field": (fact_field or "public_fact").strip(),
            "value": "",
            "answer_text": "",
            "confidence": 0.0,
            "source_url": source_url,
            "source_title": source_title,
            "source_snippet": source_snippet,
            "results": grounded_results,
            "sources": grounded_results,
            "citations": package.get("citations") or [],
            "response_text": "",
        }
        if isinstance(parsed, dict):
            result.update(
                {
                    "found": bool(parsed.get("found")),
                    "fact_field": str(parsed.get("fact_field") or result["fact_field"]).strip(),
                    "value": str(parsed.get("value") or "").strip(),
                    "answer_text": str(parsed.get("answer_text") or "").strip(),
                    "confidence": float(parsed.get("confidence") or 0.0),
                    "source_url": str(parsed.get("source_url") or source_url).strip(),
                    "source_title": str(parsed.get("source_title") or source_title).strip(),
                    "source_snippet": str(parsed.get("source_snippet") or source_snippet).strip(),
                }
            )

        if result["answer_text"]:
            result["response_text"] = result["answer_text"]
        else:
            result["response_text"] = str(package.get("response_text") or text or "").strip()

        logger.info(
            "[SEARCH_TRACE] fact_lookup: query=%r found=%s field=%s value=%r confidence=%s",
            query,
            result.get("found"),
            result.get("fact_field"),
            result.get("value"),
            result.get("confidence"),
        )
        return result

    def lookup_creator_entities(
        self,
        query: str,
        creator_profile: Dict[str, Any],
        *,
        entity_type: str = "",
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"entities": [], "response_text": "", "sources": []}

        creator_name = self._resolve_creator_name(creator_profile)
        requested_type = (entity_type or "entity").strip().lower() or "entity"
        context_preview = json.dumps((conversation_history or [])[-4:])
        plural_label = {
            "book": "books",
            "course": "courses or programs",
            "podcast": "podcasts or shows",
            "company": "companies or businesses",
        }.get(requested_type, f"{requested_type}s")
        prompt = f"""
You are verifying a creator-owned catalog for {creator_name}.

User question: {query}
Target entity type: {requested_type}
Recent context: {context_preview}

Use Google Search grounding and return JSON only.

Rules:
- Find the creator's public list of owned {plural_label}.
- Prefer official creator-owned sources, publisher/product pages, Amazon, Audible, Goodreads, and authoritative public sources.
- Return every clearly supported item you can verify, not just one.
- Do not include entities owned by someone else.
- CRITICAL: Only include items you can verify with a specific source URL. If you cannot find a direct link confirming the item exists (e.g. Amazon listing, publisher page, official website), do NOT include it. Never guess or infer entity names.

Return JSON:
{{
  "entities": [
    {{
      "name": "entity name",
      "entity_type": "{requested_type}",
      "official_url": "https://...",
      "source_title": "source title",
      "source_snippet": "supporting snippet"
    }}
  ]
}}
"""
        raw = self._call_gemini_rest(prompt, search_enabled=True)
        package = self._extract_grounding_package(raw)
        text = self._extract_text_from_response(raw).strip()
        parsed = self._parse_json(text) if text else None
        entities = []
        if isinstance(parsed, dict) and isinstance(parsed.get("entities"), list):
            for item in parsed.get("entities") or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                entities.append(
                    {
                        "name": name,
                        "type": str(item.get("entity_type") or requested_type or "entity").strip().lower(),
                        "creator_owned": True,
                        "official_urls": [str(item.get("official_url") or "").strip()] if str(item.get("official_url") or "").strip() else [],
                        "source_title": str(item.get("source_title") or "").strip(),
                        "source_snippet": str(item.get("source_snippet") or "").strip(),
                    }
                )

        if not entities:
            grounded_results = list(package.get("grounded_results") or [])
            seen_names = set()
            for result in grounded_results:
                title = str(result.get("title") or "").strip()
                if not title:
                    continue
                normalized = re.sub(r"\s+", " ", title).strip().lower()
                if normalized in seen_names:
                    continue
                seen_names.add(normalized)
                entities.append(
                    {
                        "name": title,
                        "type": requested_type or "entity",
                        "creator_owned": True,
                        "official_urls": [str(result.get("url") or "").strip()] if str(result.get("url") or "").strip() else [],
                        "source_title": str(result.get("title") or "").strip(),
                        "source_snippet": str(result.get("snippet") or "").strip(),
                    }
                )

        logger.info(
            "[SEARCH_TRACE] entity_lookup: query=%r type=%s count=%s",
            query,
            requested_type,
            len(entities),
        )
        return {
            "entities": entities,
            "response_text": str(package.get("response_text") or text or "").strip(),
            "sources": list(package.get("grounded_results") or []),
        }

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
        creator_anchor = self._creator_identity_anchor(creator_profile)
        creator_niche = self._creator_niche_text(creator_profile)
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
Creator identity anchor: {creator_anchor}
Creator field/category: {creator_niche or "Unknown"}

CRITICAL CONSTRAINTS:
1. ONLY return content OWNED by {creator_name} (from their YouTube, Site, or course).
2. DO NOT return random content from other creators.
3. EXCLUDE these already shown titles: {seen_titles_ultra}
4. Provide a helpful 'snippet' explaining why this specific result helps answer the user's intent.
5. Reject same-name results if the source does not match the identity anchor/category.

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
        
        raw = self._call_gemini_rest(prompt, search_enabled=True)
        text = self._extract_text_from_response(raw)
        grounded_candidates = self._extract_grounded_results(raw)
        if not text:
            candidates = grounded_candidates
        else:
            candidates = self._parse_json(text)
            if not candidates or not isinstance(candidates, list):
                candidates = []
            if grounded_candidates:
                existing_urls = {c.get("url") for c in candidates if isinstance(c, dict)}
                for grounded in grounded_candidates:
                    if grounded.get("url") not in existing_urls:
                        candidates.append(grounded)
            
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
        raw = self._call_gemini_rest(prompt, search_enabled=True)
        text = self._extract_text_from_response(raw)
        grounded_results = self._extract_grounded_results(raw)
        results = self._parse_json(text) if text else []
        if not results or not isinstance(results, list):
            results = []
        if grounded_results:
            existing_urls = {r.get("url") for r in results if isinstance(r, dict)}
            for grounded in grounded_results:
                if grounded.get("url") not in existing_urls:
                    results.append(grounded)
        if not results:
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
        
        raw = self._call_gemini_rest(prompt, search_enabled=True)
        text = self._extract_text_from_response(raw)
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
        
        raw = self._call_gemini_rest(prompt, search_enabled=True)
        text = self._extract_text_from_response(raw)
        if not text:
            logger.warning(f"GeminiResearch: Dossier returned NO TEXT for {creator_name}")
            return {}

        results = self._parse_json(text) or {}
        logger.info(f"GeminiResearch: Dossier synthesized for {creator_name}. Keys found: {list(results.keys())}")
        return results


class BraveSearchProvider(ResearchProvider):
    def __init__(self):
        self.api_key = settings.BRAVE_SEARCH_API_KEY
        self.enabled = bool(self.api_key)
        self.base_url = "https://api.search.brave.com/res/v1/web/search"

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
        creator_id = creator_profile.get("id")
        cached = self._get_cache(int(creator_id or 0), query, "brave") if creator_id else None
        if cached:
            return cached

        params = {
            "q": query,
            "count": 10,
            "safesearch": "moderate",
            "text_decorations": "false",
            "spellcheck": "true",
        }
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key or "",
        }
        try:
            response = requests.get(self.base_url, params=params, headers=headers, timeout=5)
            if response.status_code != 200:
                logger.warning("BraveSearchProvider error %s: %s", response.status_code, response.text[:300])
                return []
            data = response.json()
        except Exception as exc:
            logger.warning("BraveSearchProvider request failed: %s", exc)
            return []

        results: List[Dict[str, Any]] = []
        for item in ((data.get("web") or {}).get("results") or [])[:10]:
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            snippet = str(item.get("description") or item.get("snippet") or "").strip()
            if not url or not title or not _is_safe_public_url(url):
                continue
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet[:500],
                "resource_type": "video" if any(host in url.lower() for host in ("youtube.com", "youtu.be", "tiktok.com", "instagram.com")) else "article",
                "relation": "PUBLIC_FACTS",
                "confidence": 0.72,
                "platform": self._infer_platform_from_url(url),
                "provider": "brave",
            })

        verified = self._enforce_cog(results, creator_profile) if results else []
        if creator_id and verified:
            self._save_cache(int(creator_id), query, "brave", verified)
        return verified


class ExaSearchProvider(ResearchProvider):
    def __init__(self):
        self.api_key = settings.EXA_API_KEY
        self.enabled = bool(self.api_key)
        self.base_url = "https://api.exa.ai/search"

    def _coerce_platform_configs(self, creator_profile: Dict[str, Any]) -> Dict[str, Any]:
        configs = creator_profile.get("platform_configs") or {}
        if isinstance(configs, str):
            try:
                configs = json.loads(configs)
            except Exception:
                configs = {}
        return configs if isinstance(configs, dict) else {}

    def _query_topic_terms(self, query: str) -> List[str]:
        stop = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "can", "to", "of", "in", "for", "on", "with", "at", "by",
            "from", "as", "into", "about", "i", "me", "my", "you", "your", "u",
            "ur", "we", "our", "they", "them", "it", "its", "and", "or", "but",
            "what", "which", "who", "how", "when", "where", "why", "that", "this",
            "these", "those", "link", "links", "source", "sources", "video", "videos",
            "watch", "watching", "recommend", "reccomend", "show", "give", "tell",
            "please", "best", "good",
        }
        return [
            token
            for token in re.split(r"\W+", (query or "").lower())
            if len(token) > 2 and token not in stop
        ][:8]

    def _topic_score(self, result: Dict[str, Any], query: str) -> float:
        terms = self._query_topic_terms(query)
        if not terms:
            return 0.55
        text = " ".join([
            str(result.get("title") or ""),
            str(result.get("snippet") or ""),
            str(result.get("url") or ""),
        ]).lower()
        hits = sum(1 for term in terms if term in text)
        return max(0.0, min(1.0, hits / max(1, min(len(terms), 5))))

    def _infer_resource_type_from_url(self, url: str) -> str:
        host = self._normalize_netloc(urlparse(url or "").netloc)
        path = (urlparse(url or "").path or "").lower()
        if "youtube.com" in host or "youtu.be" in host:
            return "video"
        if "tiktok.com" in host:
            return "video"
        if "instagram.com" in host:
            return "video" if any(part in path for part in ("/reel/", "/reels/", "/tv/")) else "post"
        if "facebook.com" in host or host == "fb.watch":
            return "video" if any(part in path for part in ("/watch/", "/reel/", "/videos/")) else "post"
        if "x.com" in host or "twitter.com" in host:
            return "post"
        if "linkedin.com" in host:
            return "post"
        if "spotify.com" in host or "podcasts.apple.com" in host:
            return "podcast"
        return "article"

    def _platform_cache_salt(self, creator_profile: Dict[str, Any], resource_type: str) -> str:
        configs = self._coerce_platform_configs(creator_profile)
        bits = [f"resource:{resource_type}", "social-discovery-v1"]
        for platform, cfg in sorted(configs.items()):
            if not isinstance(cfg, dict) or not cfg.get("enabled"):
                continue
            identity = cfg.get("handle") or cfg.get("username") or cfg.get("channel_id") or cfg.get("url")
            if identity:
                bits.append(f"{platform}:{str(identity).strip().lower()}")
        return "|".join(bits)

    def _requested_platforms(self, query: str, conversation_history: Optional[List[Dict[str, str]]] = None) -> Set[str]:
        text = " ".join(
            [query or ""]
            + [
                str((msg or {}).get("content") or (msg or {}).get("text") or "")
                for msg in (conversation_history or [])[-2:]
                if isinstance(msg, dict) and (msg.get("role") or "").lower() == "user"
            ]
        ).lower()
        requested: Set[str] = set()
        patterns = {
            "youtube": (r"\byoutube\b", r"\byt\b", r"\bvideo\b", r"\bvideos\b", r"\bshorts?\b", r"\byoutube shorts?\b"),
            "instagram": (r"\binstagram\b", r"\binsta\b", r"\big\b", r"\breels?\b"),
            "tiktok": (r"\btiktok\b",),
            "facebook": (r"\bfacebook\b", r"\bfb\b"),
            "twitter": (r"\btwitter\b", r"\bx\.?com\b", r"\btweet\b"),
            "linkedin": (r"\blinkedin\b",),
            "podcast": (r"\bpodcast\b", r"\binterview\b", r"\bguest\b", r"\bspotify\b", r"\bapple podcast"),
        }
        for platform, platform_patterns in patterns.items():
            if any(re.search(pattern, text) for pattern in platform_patterns):
                requested.add(platform)
        return requested

    def _platform_query_specs(
        self,
        query: str,
        creator_profile: Dict[str, Any],
        conversation_history: Optional[List[Dict[str, str]]] = None,
        max_specs: int = 5,
    ) -> List[Dict[str, str]]:
        creator_name = str(creator_profile.get("name") or creator_profile.get("handle") or "creator").strip()
        creator_anchor = self._creator_identity_anchor(creator_profile)
        creator_niche = self._creator_niche_text(creator_profile)
        creator_handle = self._normalize_handle(creator_profile.get("handle"))
        topic = re.sub(r"\s+", " ", (query or "").strip())
        anchored_topic = re.sub(r"\s+", " ", f"{topic} {creator_niche}".strip()) if creator_niche and creator_niche.lower() not in topic.lower() else topic
        configs = self._coerce_platform_configs(creator_profile)
        requested = self._requested_platforms(query, conversation_history)
        specs: List[Dict[str, str]] = []

        def add(platform: str, search_query: str, priority: int) -> None:
            cleaned = re.sub(r"\s+", " ", search_query).strip()
            if not cleaned:
                return
            specs.append({"platform": platform, "query": cleaned, "priority": str(priority)})

        # Podcast/interview appearances do not usually live under a creator-owned
        # handle, but they are still valid when the result clearly features them.
        if not requested or "podcast" in requested:
            add("podcast", f'{creator_anchor} {anchored_topic} podcast interview guest Spotify Apple Podcasts', 70)

        platform_order = ["youtube", "tiktok", "instagram", "twitter", "linkedin", "facebook"]
        for platform in platform_order:
            cfg = configs.get(platform) or {}
            if not isinstance(cfg, dict) or not cfg.get("enabled"):
                continue
            if requested and platform not in requested:
                continue
            handle = self._normalize_handle(cfg.get("handle") or cfg.get("username") or creator_handle)
            url = str(cfg.get("verified_url") or cfg.get("url") or "").strip().rstrip("/")
            priority = 100 if platform in requested else 80
            if platform == "youtube":
                handle_part = f"@{handle}" if handle else creator_name
                add(platform, f'site:youtube.com {handle_part} {creator_anchor} {anchored_topic}', priority)
            elif platform == "tiktok":
                target = f"site:tiktok.com/@{handle}" if handle else "site:tiktok.com"
                add(platform, f"{target} {creator_anchor} {anchored_topic}", priority)
            elif platform == "instagram":
                target = f"site:instagram.com/{handle}" if handle else "site:instagram.com"
                add(platform, f"{target} {creator_anchor} {anchored_topic} reel post", priority)
            elif platform == "twitter":
                target = f"site:x.com/{handle}" if handle else "site:x.com"
                add(platform, f"{target} {creator_anchor} {anchored_topic}", priority)
            elif platform == "linkedin":
                if url and "linkedin.com" in url.lower():
                    add(platform, f"site:{self._normalize_netloc(urlparse(url).netloc)} {creator_anchor} {anchored_topic} LinkedIn post", priority)
                else:
                    add(platform, f"site:linkedin.com/posts {creator_anchor} {anchored_topic}", priority)
            elif platform == "facebook":
                target = f"site:facebook.com/{handle}" if handle else "site:facebook.com"
                add(platform, f"{target} {creator_anchor} {anchored_topic}", priority)

        if not specs:
            handle_part = f"@{creator_handle}" if creator_handle else ""
            add("web", f"{creator_anchor} {handle_part} {anchored_topic}", 50)

        deduped: List[Dict[str, str]] = []
        seen = set()
        for spec in sorted(specs, key=lambda item: int(item.get("priority") or 0), reverse=True):
            key = spec["query"].lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(spec)
            if len(deduped) >= max_specs:
                break
        return deduped

    def _search_exa_once(self, search_query: str, *, timeout: float = 3.8) -> List[Dict[str, Any]]:
        payload = {
            "query": search_query,
            "numResults": 8,
            "type": "auto",
            "contents": {"text": {"maxCharacters": 500}},
        }
        headers = {"x-api-key": self.api_key or "", "Content-Type": "application/json"}
        response = requests.post(self.base_url, json=payload, headers=headers, timeout=timeout)
        if response.status_code != 200:
            logger.warning("ExaSearchProvider error %s: %s", response.status_code, response.text[:300])
            return []
        data = response.json()
        normalized: List[Dict[str, Any]] = []
        for item in (data.get("results") or [])[:8]:
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            snippet = str(item.get("text") or item.get("summary") or "").strip()
            if not url or not title or not _is_safe_public_url(url):
                continue
            normalized.append({
                "title": title,
                "url": url,
                "snippet": snippet[:500],
                "resource_type": self._infer_resource_type_from_url(url),
                "relation": "PUBLIC_FACTS",
                "confidence": 0.76,
                "platform": self._infer_platform_from_url(url),
                "provider": "exa",
            })
        return normalized

    def _dedupe_and_rank(self, results: List[Dict[str, Any]], query: str, requested: Set[str]) -> List[Dict[str, Any]]:
        by_url: Dict[str, Dict[str, Any]] = {}
        for result in results:
            url = str(result.get("url") or "").strip()
            if not url:
                continue
            key = url.split("?", 1)[0].rstrip("/").lower()
            result["_topic_score"] = self._topic_score(result, query)
            platform = str(result.get("platform") or self._infer_platform_from_url(url) or "web").lower()
            result["platform"] = platform
            result["_platform_requested"] = platform in requested
            existing = by_url.get(key)
            if not existing or (
                float(result.get("confidence") or 0.0),
                float(result.get("_topic_score") or 0.0),
            ) > (
                float(existing.get("confidence") or 0.0),
                float(existing.get("_topic_score") or 0.0),
            ):
                by_url[key] = result

        ranked = list(by_url.values())
        ranked.sort(key=lambda item: (
            item.get("relation") == "SELF",
            item.get("_platform_requested", False),
            float(item.get("_topic_score") or 0.0),
            float(item.get("confidence") or 0.0),
            item.get("relation") == "AFFILIATED",
        ), reverse=True)

        diversified: List[Dict[str, Any]] = []
        per_platform: Dict[str, int] = {}
        for item in ranked:
            platform = str(item.get("platform") or "web")
            limit = 2 if len(ranked) > 3 else 3
            if per_platform.get(platform, 0) >= limit:
                continue
            diversified.append(item)
            per_platform[platform] = per_platform.get(platform, 0) + 1
            if len(diversified) >= 6:
                break
        return diversified

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
        creator_id = creator_profile.get("id")
        cache_salt = self._platform_cache_salt(creator_profile, resource_type)
        cached = self._get_cache(int(creator_id or 0), query, "exa", cache_salt=cache_salt) if creator_id else None
        if cached:
            return cached

        results: List[Dict[str, Any]] = []
        specs = [{"platform": "web", "query": query, "priority": "90"}] + self._platform_query_specs(
            query,
            creator_profile,
            conversation_history,
        )
        deduped_specs: List[Dict[str, str]] = []
        seen_queries = set()
        for spec in specs:
            key = spec["query"].lower().strip()
            if not key or key in seen_queries:
                continue
            seen_queries.add(key)
            deduped_specs.append(spec)
            if len(deduped_specs) >= 6:
                break

        if not deduped_specs:
            return []

        executor = None
        try:
            from concurrent.futures import ThreadPoolExecutor, wait

            executor = ThreadPoolExecutor(max_workers=min(4, len(deduped_specs)))
            futures = {
                executor.submit(self._search_exa_once, spec["query"]): spec
                for spec in deduped_specs
            }
            done, not_done = wait(futures.keys(), timeout=4.8)
            for future in done:
                spec = futures[future]
                try:
                    for item in future.result() or []:
                        item["_source_query_platform"] = spec.get("platform")
                        results.append(item)
                except Exception as exc:
                    logger.warning("ExaSearchProvider platform query failed for %s: %s", spec.get("platform"), exc)
            for future in not_done:
                future.cancel()
        except Exception as exc:
            logger.warning("ExaSearchProvider request failed: %s", exc)
            return []
        finally:
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)

        verified = self._enforce_cog(results, creator_profile) if results else []
        requested = self._requested_platforms(query, conversation_history)
        verified = self._dedupe_and_rank(verified, query, requested) if verified else []
        if creator_id and verified:
            self._save_cache(int(creator_id), query, "exa", verified, cache_salt=cache_salt)
        return verified


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
        if not _is_safe_public_url(url, {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}):
            return None
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

    def _validate_web_url(self, url: str) -> bool:
        """Validate a non-YouTube URL via HEAD request. Returns True if reachable (2xx/3xx)."""
        if not _is_safe_public_url(url):
            return False
        try:
            response = requests.head(url, allow_redirects=False, timeout=3, headers={'User-Agent': 'Mozilla/5.0'})
            status = response.status_code
            if status and status < 400:
                return True
            logger.warning(f"[URL-VALIDATE] Web URL returned {status}: {url}")
            return False
        except Exception as e:
            logger.warning(f"[URL-VALIDATE] Web URL unreachable: {url} — {e}")
            return False

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

    def _query_terms_for_relevance(self, topic_query: str, search_intent: str) -> List[str]:
        stop_words = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'shall', 'can', 'to', 'of', 'in', 'for',
            'on', 'with', 'at', 'by', 'from', 'as', 'into', 'about', 'i', 'me',
            'my', 'you', 'your', 'we', 'our', 'they', 'them', 'it', 'its', 'and',
            'or', 'but', 'not', 'no', 'what', 'which', 'who', 'how', 'when', 'where',
            'that', 'this', 'these', 'those', 'link', 'links', 'resource', 'resources',
        }
        if search_intent == 'VIDEO':
            stop_words.update({
                'video', 'videos', 'watch', 'watching', 'clip', 'clips', 'reel', 'reels',
                'post', 'posts', 'first', 'start', 'starting', 'begin', 'best', 'good',
                'recommend', 'recommended', 'reccomend', 'lesson', 'lessons', 'teach', 'learn',
            })
        return [
            token for token in re.split(r'\W+', (topic_query or '').lower())
            if token and token not in stop_words and len(token) > 1
        ]

    def _query_fidelity_score(self, result: Dict[str, Any], topic_keywords: List[str]) -> float:
        if not topic_keywords:
            return 0.55

        title_lower = (result.get('_real_title') or result.get('title') or '').lower()
        snippet_lower = (result.get('snippet') or '').lower()
        url_lower = (result.get('url') or '').lower()
        title_hits = sum(1 for kw in topic_keywords if kw in title_lower)
        snippet_hits = sum(1 for kw in topic_keywords if kw in snippet_lower)
        url_hits = sum(1 for kw in topic_keywords if kw in url_lower)

        coverage = (
            (title_hits * 0.45)
            + (snippet_hits * 0.22)
            + (url_hits * 0.18)
        ) / max(1.0, len(topic_keywords) * 0.45)

        joined_terms = " ".join(topic_keywords[:5]).strip()
        exact_phrase_bonus = 0.0
        if joined_terms and (joined_terms in title_lower or joined_terms in snippet_lower):
            exact_phrase_bonus = 0.2

        return max(0.0, min(1.0, coverage + exact_phrase_bonus))

    def _score_relevance(
        self, results: List[Dict[str, Any]], topic_query: str, search_intent: str
    ) -> List[Dict[str, Any]]:
        """
        Score and sort results by how well their title matches the topic query.
        """
        topic_keywords = self._query_terms_for_relevance(topic_query, search_intent)
        if not topic_keywords:
            baseline = 0.58 if search_intent == 'VIDEO' else 0.5
            for result in results:
                result['_relevance_score'] = 1.0 if search_intent == 'VIDEO' else baseline
                result['query_fidelity_score'] = baseline
                domain = result.get('_domain', '')
                trust = 1.0 if ('youtube.com' in domain or 'youtu.be' in domain) else 0.2
                ownership = float(result.get('ownership_score', 0.0) or 0.0)
                result['_evidence_score'] = (ownership * 2) + trust + baseline
            results.sort(key=lambda x: (
                x.get('relation') == 'SELF',
                x.get('query_fidelity_score', 0),
                x.get('_evidence_score', 0),
                x.get('confidence', 0)
            ), reverse=True)
            return results

        scored = []
        for result in results:
            title_lower = (result.get('_real_title') or result.get('title') or '').lower()
            snippet_lower = (result.get('snippet') or '').lower()
            url_lower = (result.get('url') or '').lower()
            title_hits = sum(1 for kw in topic_keywords if kw in title_lower)
            snippet_hits = sum(1 for kw in topic_keywords if kw in snippet_lower)
            url_hits = sum(1 for kw in topic_keywords if kw in url_lower)
            relevance_score = (title_hits * 2) + snippet_hits + url_hits
            query_fidelity = self._query_fidelity_score(result, topic_keywords)
            result['_relevance_score'] = relevance_score
            result['query_fidelity_score'] = query_fidelity
            domain = result.get('_domain', '')
            trust = 1.0 if ('youtube.com' in domain or 'youtu.be' in domain) else 0.2
            ownership = result.get('ownership_score', 0.0)
            result['_evidence_score'] = (ownership * 2) + trust + min(2.0, relevance_score / 2.0) + query_fidelity
            scored.append(result)

        scored.sort(key=lambda x: (
            x.get('relation') == 'SELF',
            x.get('query_fidelity_score', 0),
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
        creator_anchor = self._creator_identity_anchor(creator_profile)
        creator_niche = self._creator_niche_text(creator_profile)
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
                f"Find creator-owned or strongly affiliated videos for {creator_name} specifically about {sanitized_topic}.\n"
                f"Creator identity anchor: {creator_anchor}.\n"
                f"Creator field/category: {creator_niche or 'unknown'}.\n\n"
                "Allowed platforms: YouTube, Instagram Reels, TikTok, Facebook Reels/Watch, Twitter/X.\n"
                "Return only direct public post/video URLs.\n"
                "If a result is from a church, podcast, interview, or conference, include it only if the creator is clearly the speaker or guest.\n"
                "Reject same-name results that do not match the identity anchor or category.\n"
                "Do not guess URLs. Do not use placeholders like VIDEO_ID, REEL_ID, or POST_ID."
                f"{exclude_instruction}"
            )
        elif search_intent == 'EVENT':
            search_prompt = (
                f"Find current public event information for {creator_name} related to {sanitized_topic}.\n"
                f"Creator identity anchor: {creator_anchor}.\n"
                f"Creator field/category: {creator_niche or 'unknown'}.\n"
                "Prioritize official event pages, registration pages, venue pages, church or ministry pages, and recent announcements.\n"
                "Focus on next or upcoming dates, venue, registration, livestream details, and status.\n"
                "Prefer official sources, but include high confidence public event pages when they clearly refer to the same event.\n"
                "Reject same-name results that do not match the identity anchor or category.\n"
                "Return real URLs only.\n"
                f"{exclude_instruction}"
            )
        elif search_intent == 'PRODUCT':
            search_prompt = (
                f"Find official products, courses, or programs from {creator_name} related to {sanitized_topic}.\n"
                f"Creator identity anchor: {creator_anchor}.\n"
                f"Creator field/category: {creator_niche or 'unknown'}.\n"
                "Return only official pages or direct videos where the creator discusses the product."
                f"{exclude_instruction}"
            )
        elif search_intent == 'SOCIAL':
            search_prompt = (
                f"Find the official social profile or page for {creator_name} related to this request: {query}.\n"
                f"Creator identity anchor: {creator_anchor}.\n"
                f"Creator field/category: {creator_niche or 'unknown'}.\n"
                "Return only official public URLs if you can verify them."
                f"{exclude_instruction}"
            )
        else:
            search_prompt = (
                f"Find reliable public web results about {creator_name} related to {sanitized_topic}.\n"
                f"Creator identity anchor: {creator_anchor}.\n"
                f"Creator field/category: {creator_niche or 'unknown'}.\n"
                "Prefer official creator-owned pages and strongly related appearances.\n"
                "Reject same-name results that do not match the identity anchor or category.\n"
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
        non_youtube_items = [(i, r) for i, r in enumerate(pre_validated) if 'youtube.com' not in (r.get('url') or '') and 'youtu.be' not in (r.get('url') or '')]
        validation_results = {}
        if youtube_items:
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
        # Validate non-YouTube URLs via HEAD request to catch broken/hallucinated links
        web_validation_results = {}
        if non_youtube_items:
            def validate_web_item(args):
                idx, result = args
                return idx, self._validate_web_url(result['url'])
            with ThreadPoolExecutor(max_workers=min(len(non_youtube_items), 4)) as executor:
                futures = {executor.submit(validate_web_item, item): item[0] for item in non_youtube_items}
                for future in as_completed(futures):
                    try:
                        idx, is_valid = future.result(timeout=4)
                        web_validation_results[idx] = is_valid
                    except Exception:
                        web_validation_results[futures[future]] = False
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
            elif idx in web_validation_results:
                if not web_validation_results[idx]:
                    logger.info(f"[URL-VALIDATE] Dropping unreachable web URL: {result.get('url')}")
                    continue
            validated.append(result)

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
                query_fidelity = float(result.get('query_fidelity_score') or 0.0)
                if relation == 'SELF' and confidence >= 0.72 and (topic_score >= 1 or query_fidelity >= 0.5):
                    strict_results.append(result)
                elif relation == 'AFFILIATED' and confidence >= 0.8 and (topic_score >= 1 or query_fidelity >= 0.55):
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
    preferred = (_settings_value("LIVE_SEARCH_PROVIDER", "auto") or "auto").lower()

    if preferred == "gemini" and _gemini_api_key():
        return GeminiResearchProvider()
    if preferred == "openai" and _settings_value("OPENAI_API_KEY"):
        return OpenAIResearchProvider()
    if preferred == "brave" and _settings_value("BRAVE_SEARCH_API_KEY"):
        return BraveSearchProvider()
    if preferred == "exa" and _settings_value("EXA_API_KEY"):
        return ExaSearchProvider()
    if preferred in {"serpapi", "search_api"} and _settings_value("SEARCH_API_KEY"):
        return SerpApiResearchProvider()

    if _settings_value("BRAVE_SEARCH_API_KEY"):
        return BraveSearchProvider()
    if _settings_value("EXA_API_KEY"):
        return ExaSearchProvider()
    if _gemini_api_key():
        return GeminiResearchProvider()
    if _settings_value("OPENAI_API_KEY"):
        return OpenAIResearchProvider()
    if _settings_value("SEARCH_API_KEY"):
        return SerpApiResearchProvider()
    return GeminiResearchProvider()


def get_fallback_research_provider() -> Optional[ResearchProvider]:
    """Return a secondary search provider different from the primary one.

    Used when the primary provider returns no results so we can retry with
    an alternative backend before falling through to a dead-end fallback.
    Returns None if no secondary provider is available.
    """
    primary = get_research_provider()
    primary_type = type(primary).__name__

    # Try providers in order, skipping the one already in use
    if primary_type != "BraveSearchProvider" and _settings_value("BRAVE_SEARCH_API_KEY"):
        return BraveSearchProvider()
    if primary_type != "ExaSearchProvider" and _settings_value("EXA_API_KEY"):
        return ExaSearchProvider()
    if primary_type != "SerpApiResearchProvider" and _settings_value("SEARCH_API_KEY"):
        return SerpApiResearchProvider()
    if primary_type != "OpenAIResearchProvider" and _settings_value("OPENAI_API_KEY"):
        return OpenAIResearchProvider()
    if primary_type != "GeminiResearchProvider" and _gemini_api_key():
        return GeminiResearchProvider()
    return None

