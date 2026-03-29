"""
Creator entity graph extraction and resolution.

This gives Creator Bot a lightweight structured understanding of each
creator's public world: books, courses, podcasts, platforms, companies, and
official URLs. It is intentionally heuristic and cheap so it can run before
generation on every turn and improve follow-up resolution.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from backend.db import db


logger = logging.getLogger(__name__)


_ENTITY_SCHEMA_READY = False


_ENTITY_PATTERNS = [
    ("book", re.compile(r"\bbook (?:called|titled|named)\s*[\"']?([^\"'\n\.\!\?]{2,120})", re.IGNORECASE)),
    ("book", re.compile(r"\b(?:author of|wrote)\s*[\"']([^\"'\n]{2,120})[\"']", re.IGNORECASE)),
    ("course", re.compile(r"\b(?:course|program|membership|coaching)\s+(?:called|titled|named)\s*[\"']?([^\"'\n\.\!\?]{2,120})", re.IGNORECASE)),
    ("podcast", re.compile(r"\b(?:podcast|show|channel|newsletter)\s+(?:called|titled|named)\s*[\"']?([^\"'\n\.\!\?]{2,120})", re.IGNORECASE)),
    ("company", re.compile(r"\b(?:company|business|brand)\s+(?:called|named)\s*[\"']?([^\"'\n\.\!\?]{2,120})", re.IGNORECASE)),
]

_QUOTED_PATTERN = re.compile(r'"([^"\n]{3,120})"')
_BOOK_HINTS = ("book", "author", "published", "publication", "amazon", "audible", "goodreads")
_COURSE_HINTS = ("course", "program", "coaching", "membership", "curriculum")
_PODCAST_HINTS = ("podcast", "show", "episode", "newsletter", "channel")
_COMPANY_HINTS = ("company", "business", "brand", "startup", "software", "saas")
_SOCIAL_PLATFORMS = ("youtube", "instagram", "tiktok", "facebook", "twitter", "x", "linkedin")
_FOLLOWUP_REFERENTS = {"it", "that", "this", "one", "book", "course", "program", "podcast", "episode"}
_FACT_FIELDS_BY_TYPE = {
    "book": ["publication_date", "audiobook", "publisher", "availability"],
    "course": ["price", "launch_date", "availability", "official_url"],
    "podcast": ["latest_episode", "launch_date", "official_url"],
    "company": ["founding_date", "valuation", "official_url"],
    "profile": ["followers", "subscribers", "official_url"],
    "website": ["official_url"],
}


def _ensure_entity_schema() -> bool:
    global _ENTITY_SCHEMA_READY
    if _ENTITY_SCHEMA_READY:
        return True
    try:
        db.execute_update(
            """
            CREATE TABLE IF NOT EXISTS creator_entity_graph (
                creator_id TEXT PRIMARY KEY,
                graph JSONB NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        _ENTITY_SCHEMA_READY = True
        return True
    except Exception as exc:
        logger.warning("Creator entity graph schema bootstrap failed: %s", exc)
        return False


def _json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_alias(value: Any) -> str:
    cleaned = _normalize_space(value).strip(" .,:;!?\"'")
    return cleaned.lower()


def _safe_domain(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def _build_platform_url(platform: str, handle: str) -> str:
    clean = str(handle or "").strip().lstrip("@")
    if not clean:
        return ""
    if platform == "youtube":
        return f"https://www.youtube.com/@{clean}"
    if platform == "instagram":
        return f"https://www.instagram.com/{clean}/"
    if platform in {"twitter", "x"}:
        return f"https://x.com/{clean}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{clean}"
    if platform == "facebook":
        return f"https://www.facebook.com/{clean}"
    if platform == "linkedin":
        return f"https://www.linkedin.com/in/{clean}/"
    return ""


class CreatorEntityService:
    def _flatten_text_values(self, value: Any) -> List[str]:
        if isinstance(value, str):
            cleaned = _normalize_space(value)
            return [cleaned] if cleaned else []
        if isinstance(value, list):
            flattened: List[str] = []
            for item in value:
                flattened.extend(self._flatten_text_values(item))
            return flattened
        if isinstance(value, dict):
            flattened: List[str] = []
            for item in value.values():
                flattened.extend(self._flatten_text_values(item))
            return flattened
        cleaned = _normalize_space(value)
        return [cleaned] if cleaned else []

    def _load_creator_profile(self, creator_id: int) -> Dict[str, Any]:
        try:
            row = db.execute_one("SELECT * FROM creators WHERE id = %s", (creator_id,))
            return row or {}
        except Exception as exc:
            logger.warning("CreatorEntityService profile load failed for %s: %s", creator_id, exc)
            return {}

    def _cache_graph(self, creator_id: int, graph: Dict[str, Any]) -> None:
        try:
            if not _ensure_entity_schema():
                return
            db.execute_update(
                """
                INSERT INTO creator_entity_graph (creator_id, graph, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (creator_id)
                DO UPDATE SET graph = EXCLUDED.graph, updated_at = NOW()
                """,
                (str(creator_id), json.dumps(graph)),
            )
        except Exception as exc:
            logger.warning("CreatorEntityService graph cache write failed: %s", exc)

    def _get_cached_graph(self, creator_id: int) -> Optional[Dict[str, Any]]:
        try:
            if not _ensure_entity_schema():
                return None
            row = db.execute_one(
                "SELECT graph FROM creator_entity_graph WHERE creator_id = %s",
                (str(creator_id),),
            )
            graph = (row or {}).get("graph")
            return graph if isinstance(graph, dict) else None
        except Exception as exc:
            logger.warning("CreatorEntityService graph cache read failed: %s", exc)
            return None

    def _candidate_type(self, source_text: str, value: str) -> str:
        haystack = f"{source_text} {value}".lower()
        if any(token in haystack for token in _BOOK_HINTS):
            return "book"
        if any(token in haystack for token in _COURSE_HINTS):
            return "course"
        if any(token in haystack for token in _PODCAST_HINTS):
            return "podcast"
        if any(token in haystack for token in _COMPANY_HINTS):
            return "company"
        return "entity"

    def _collect_text_sources(self, creator_profile: Dict[str, Any]) -> List[str]:
        texts: List[str] = []
        for field in (
            "identity_fingerprint",
            "research_summary",
            "style_fingerprint",
            "soul_md",
            "bio",
            "description",
        ):
            raw = creator_profile.get(field)
            texts.extend(self._flatten_text_values(raw))
        return texts

    def _entity_aliases(self, name: str, entity_type: str, single_of_type: bool) -> List[str]:
        aliases = []
        base = _normalize_alias(name)
        if base:
            aliases.append(base)
        stripped = re.sub(r"[^a-z0-9 ]+", "", base).strip()
        if stripped and stripped not in aliases:
            aliases.append(stripped)
        if single_of_type:
            if entity_type == "book":
                aliases.extend(["your book", "the book", "my book"])
            elif entity_type == "course":
                aliases.extend(["your course", "the course", "your program", "the program"])
            elif entity_type == "podcast":
                aliases.extend(["your podcast", "the podcast", "your show", "the show"])
            elif entity_type == "company":
                aliases.extend(["your company", "the company", "your business", "the business"])

        deduped: List[str] = []
        seen = set()
        for alias in aliases:
            key = _normalize_alias(alias)
            if key and key not in seen:
                deduped.append(alias.strip())
                seen.add(key)
        return deduped

    def _extract_entities(self, creator_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        entities: List[Dict[str, Any]] = []
        texts = self._collect_text_sources(creator_profile)

        def add_entity(entity_type: str, name: str, source_text: str = "") -> None:
            cleaned = _normalize_space(name).strip(" .,:;!?\"'")
            if not cleaned or len(cleaned) < 2:
                return
            if cleaned.lower() in {"the creator", "creator", "book", "course", "program", "podcast"}:
                return
            for existing in entities:
                if _normalize_alias(existing.get("name")) == _normalize_alias(cleaned):
                    if entity_type != "entity" and existing.get("type") == "entity":
                        existing["type"] = entity_type
                    return
            entities.append(
                {
                    "type": entity_type,
                    "name": cleaned,
                    "creator_owned": True,
                    "official_urls": [],
                    "fact_fields": list(_FACT_FIELDS_BY_TYPE.get(entity_type, [])),
                    "aliases": [],
                    "source_text": source_text[:240],
                }
            )

        for text in texts:
            for entity_type, pattern in _ENTITY_PATTERNS:
                for match in pattern.finditer(text):
                    add_entity(entity_type, match.group(1), text)

            for match in _QUOTED_PATTERN.finditer(text):
                quoted = _normalize_space(match.group(1))
                if len(quoted.split()) < 2:
                    continue
                start = max(0, match.start() - 80)
                end = min(len(text), match.end() + 80)
                surrounding = text[start:end]
                add_entity(self._candidate_type(surrounding, quoted), quoted, surrounding)

        # Titles from explicitly verified facts often show up in lists/dicts.
        identity = _json_dict(creator_profile.get("identity_fingerprint"))
        for value in (identity.get("verified_facts") or []):
            value_text = _normalize_space(value)
            if not value_text:
                continue
            if ":" in value_text:
                left, right = value_text.split(":", 1)
                add_entity(self._candidate_type(left, right), right, value_text)

        type_counts: Dict[str, int] = {}
        for entity in entities:
            type_counts[entity["type"]] = type_counts.get(entity["type"], 0) + 1

        for entity in entities:
            entity["aliases"] = self._entity_aliases(
                entity["name"],
                entity["type"],
                type_counts.get(entity["type"], 0) == 1,
            )
        return entities

    def _collect_official_urls(self, creator_profile: Dict[str, Any]) -> List[str]:
        urls: List[str] = []

        for raw_url in creator_profile.get("course_base_urls") or []:
            cleaned = str(raw_url or "").strip()
            if cleaned:
                urls.append(cleaned)

        for domain in creator_profile.get("official_domains") or []:
            cleaned = str(domain or "").strip()
            if cleaned and not cleaned.startswith(("http://", "https://")):
                cleaned = f"https://{cleaned}"
            if cleaned:
                urls.append(cleaned)

        platform_configs = _json_dict(creator_profile.get("platform_configs"))
        for platform, config in platform_configs.items():
            if not isinstance(config, dict):
                continue
            for key in ("verified_url", "url"):
                cleaned = str(config.get(key) or "").strip()
                if cleaned:
                    urls.append(cleaned)
            if not any(config.get(key) for key in ("verified_url", "url")):
                handle = config.get("handle") or config.get("username")
                guessed = _build_platform_url(platform.lower(), str(handle or ""))
                if guessed:
                    urls.append(guessed)

        youtube_handle = str(creator_profile.get("youtube_handle") or "").strip()
        if youtube_handle:
            urls.append(_build_platform_url("youtube", youtube_handle))

        creator_handle = str(creator_profile.get("handle") or "").strip()
        if creator_handle and not urls:
            guessed = _build_platform_url("instagram", creator_handle)
            if guessed:
                urls.append(guessed)

        deduped: List[str] = []
        seen = set()
        for url in urls:
            cleaned = url.rstrip("/")
            if cleaned and cleaned.lower() not in seen:
                deduped.append(cleaned)
                seen.add(cleaned.lower())
        return deduped

    def _attach_entity_urls(self, creator_profile: Dict[str, Any], entities: List[Dict[str, Any]]) -> None:
        official_urls = self._collect_official_urls(creator_profile)
        for entity in entities:
            entity_urls: List[str] = []
            lowered_name = entity.get("name", "").lower()
            for url in official_urls:
                lowered_url = url.lower()
                if entity["type"] == "profile":
                    if any(platform in lowered_url for platform in _SOCIAL_PLATFORMS):
                        entity_urls.append(url)
                elif any(token in lowered_url for token in re.findall(r"[a-z0-9]+", lowered_name)):
                    entity_urls.append(url)
            if entity["type"] in {"book", "course", "podcast", "company"} and not entity_urls:
                entity_urls = list(official_urls[:3])
            entity["official_urls"] = entity_urls[:5]

    def _add_profile_entities(self, creator_profile: Dict[str, Any], entities: List[Dict[str, Any]]) -> None:
        platform_configs = _json_dict(creator_profile.get("platform_configs"))
        for platform, config in platform_configs.items():
            if not isinstance(config, dict):
                continue
            url = str(config.get("verified_url") or config.get("url") or "").strip()
            handle = str(config.get("handle") or config.get("username") or "").strip()
            if not url and handle:
                url = _build_platform_url(platform.lower(), handle)
            if not url:
                continue
            name = platform.title()
            entities.append(
                {
                    "type": "profile",
                    "name": name,
                    "creator_owned": True,
                    "official_urls": [url],
                    "fact_fields": list(_FACT_FIELDS_BY_TYPE["profile"]),
                    "aliases": [name.lower(), f"your {platform.lower()}", f"my {platform.lower()}"],
                    "source_text": f"Official {platform} profile",
                }
            )

        official_urls = self._collect_official_urls(creator_profile)
        if official_urls:
            entities.append(
                {
                    "type": "website",
                    "name": "Official Website",
                    "creator_owned": True,
                    "official_urls": official_urls[:3],
                    "fact_fields": list(_FACT_FIELDS_BY_TYPE["website"]),
                    "aliases": ["your website", "my website", "official website", "site", "website"],
                    "source_text": "Official creator website(s)",
                }
            )

    def build_entity_graph(
        self,
        creator_id: Optional[int] = None,
        creator_profile: Optional[Dict[str, Any]] = None,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        profile = dict(creator_profile or {})
        if creator_id is None:
            raw_id = profile.get("id")
            try:
                creator_id = int(raw_id) if raw_id is not None else None
            except Exception:
                creator_id = None

        if creator_id is not None and not refresh:
            cached = self._get_cached_graph(creator_id)
            if cached:
                return cached

        if not profile and creator_id is not None:
            profile = self._load_creator_profile(creator_id)

        entities = self._extract_entities(profile)
        self._add_profile_entities(profile, entities)
        self._attach_entity_urls(profile, entities)

        deduped_entities: List[Dict[str, Any]] = []
        seen = set()
        for entity in entities:
            key = (_normalize_alias(entity.get("name")), entity.get("type"))
            if key in seen:
                continue
            seen.add(key)
            deduped_entities.append(entity)

        graph = {
            "creator_id": creator_id,
            "creator_name": profile.get("name") or profile.get("handle") or "",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "official_urls": self._collect_official_urls(profile),
            "entities": deduped_entities,
        }
        if creator_id is not None:
            self._cache_graph(creator_id, graph)
        return graph

    def resolve_entity(
        self,
        query: str,
        creator_id: Optional[int] = None,
        creator_profile: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Optional[Dict[str, Any]]:
        graph = self.build_entity_graph(creator_id=creator_id, creator_profile=creator_profile)
        entities = list(graph.get("entities") or [])
        if not entities:
            return None

        normalized_query = _normalize_alias(query)
        combined_history = " ".join(
            _normalize_space(message.get("content") or message.get("text") or "")
            for message in (conversation_history or [])[-8:]
        ).lower()

        def score_entity(entity: Dict[str, Any]) -> float:
            score = 0.0
            name = _normalize_alias(entity.get("name"))
            if name and name in normalized_query:
                score += 10.0
            for alias in entity.get("aliases") or []:
                alias_key = _normalize_alias(alias)
                if alias_key and alias_key in normalized_query:
                    score += 6.0
            name_tokens = [token for token in re.findall(r"[a-z0-9]+", name) if len(token) > 2]
            token_hits = sum(1 for token in name_tokens if token in normalized_query)
            score += token_hits * 1.25

            entity_type = entity.get("type")
            if entity_type == "book" and any(token in normalized_query for token in ("book", "published", "publication", "wrote", "write", "audible", "amazon")):
                score += 2.0
            elif entity_type == "course" and any(token in normalized_query for token in ("course", "program", "coaching", "membership")):
                score += 2.0
            elif entity_type == "podcast" and any(token in normalized_query for token in ("podcast", "episode", "show", "newsletter", "channel")):
                score += 2.0
            elif entity_type == "company" and any(token in normalized_query for token in ("company", "business", "valuation", "founded", "founded", "employees")):
                score += 2.0
            elif entity_type == "profile" and any(token in normalized_query for token in ("followers", "subscribers", "instagram", "youtube", "linkedin", "tiktok", "twitter", "x")):
                score += 2.0

            if score == 0.0 and combined_history:
                if name and name in combined_history:
                    score += 3.0
                for alias in entity.get("aliases") or []:
                    alias_key = _normalize_alias(alias)
                    if alias_key and alias_key in combined_history:
                        score += 1.5

            return score

        ranked = sorted(
            ((score_entity(entity), entity) for entity in entities),
            key=lambda item: item[0],
            reverse=True,
        )
        if ranked and ranked[0][0] >= 3.0:
            return ranked[0][1]

        if combined_history and set(re.findall(r"[a-z0-9']+", normalized_query)) & _FOLLOWUP_REFERENTS:
            history_ranked = []
            for entity in entities:
                name = _normalize_alias(entity.get("name"))
                score = 0.0
                if name and name in combined_history:
                    score += 5.0
                for alias in entity.get("aliases") or []:
                    alias_key = _normalize_alias(alias)
                    if alias_key and alias_key in combined_history:
                        score += 2.0
                if score:
                    history_ranked.append((score, entity))
            if history_ranked:
                history_ranked.sort(key=lambda item: item[0], reverse=True)
                return history_ranked[0][1]

        # Generic typed fallback: if user says "your book" and there is only one book, use it.
        type_groups: Dict[str, List[Dict[str, Any]]] = {}
        for entity in entities:
            type_groups.setdefault(str(entity.get("type") or "entity"), []).append(entity)

        generic_map = {
            "book": ("book",),
            "course": ("course", "program", "membership", "coaching"),
            "podcast": ("podcast", "show", "newsletter", "channel", "episode"),
            "company": ("company", "business"),
            "profile": ("followers", "subscribers", "instagram", "youtube", "linkedin", "tiktok", "twitter", "x"),
            "website": ("website", "site", "official"),
        }
        for entity_type, hints in generic_map.items():
            if len(type_groups.get(entity_type, [])) == 1 and any(hint in normalized_query for hint in hints):
                return type_groups[entity_type][0]
        return None


creator_entity_service = CreatorEntityService()
