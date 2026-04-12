"""
Grounded-RAG Loop (GRL) Algorithm
Forces the assistant to stay close to retrieved DB chunks with evidence mapping and validation.
"""

from __future__ import annotations

import re
import json
import logging
import math
from typing import List, Dict, Any, Optional, Tuple, Set
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qs, urlparse
from backend.db import db
from backend.settings import settings
import backend.rag as rag
from backend.prompts.creator_base_prompt import CREATOR_BASE_SYSTEM_PROMPT
from backend.services.style_distiller import StyleDistiller
from backend.services.style_scorer import StyleScorer
from backend.services.content_finder import ContentFinder
from backend.services.research_provider import GeminiResearchProvider
from backend.services.memory_service import memory_service
from backend.services.greeting_service import greeting_service, is_greeting
from backend.services.personal_bio_service import personal_bio_service
from backend.services.persona_filter import apply_persona_surface_filter
from backend.services.curiosity_service import curiosity_service
from backend.services.rhythm_shaper import rhythm_shaper
from backend.services.user_priority_service import user_priority_service
from backend.services.decision_service import decision_service
from backend.services.greeting_service import greeting_service, is_greeting
from backend.services.memory_loop_service import memory_loop_service
from backend.services.steering_service import steering_service
from backend.services.classifiers import classifiers
from backend.services.stronghold_guard import stronghold_guard
from backend.core.interaction_engine import interaction_engine, InteractionPlan, strip_all_markdown
from backend.services.web_verify import web_verify
from backend.services.formatting import clean_response, should_strip_hyphens
from backend.services.assumption_blocker import assumption_blocker
from backend.services.image_identity_service import image_identity_service
from backend.services.voice_dna import build_voice_echo_block
from backend.utils.url_health import check_url_alive_sync
from backend.services.live_search_rules import (
    build_live_search_query,
    extract_requested_platforms,
    needs_fresh_public_web_search,
)
from backend.services.creator_entity_service import creator_entity_service
from backend.services.evidence_router import (
    EvidencePlan,
    EvidenceRouter,
    detect_evidence_contradiction,
    log_evidence_plan,
)
from backend.services.search_decision_engine import (
    SearchDecision,
    SearchDecisionEngine,
    log_search_decision,
)
from backend.services.rag_text_matcher import (
    extract_named_resource_fragments,
    merge_support_sets,
    retrieve_sparse_text_matches,
    retrieve_exact_text_matches,
)
from backend.services.recommendation_asset_service import recommendation_asset_service
from backend.services.recommendation_feedback_service import recommendation_feedback_service
from backend.services.out_of_domain_rules import (
    default_bridge_question,
    detect_external_live_fact_topic,
    recent_bridge_topic,
    should_redirect_general_knowledge,
    should_soft_decline_external_live_fact,
)
from backend.services.conversation_closure import get_bridge_question
from backend.services.regurgitation_guard import (
    build_anti_regurgitation_block,
    check_for_regurgitation,
    score_response_quality,
    shape_support_set,
)


logger = logging.getLogger(__name__)


_CREATOR_COLUMN_CACHE: dict[str, bool] = {}


def _coerce_json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _quality_markers_for_creator(creator_profile: Dict[str, Any]) -> List[str]:
    style_fp = _coerce_json_dict((creator_profile or {}).get("style_fingerprint"))
    voice_profile = _coerce_json_dict((creator_profile or {}).get("voice_profile"))
    lexical_rules = _coerce_json_dict(style_fp.get("lexical_rules"))
    value_model = _coerce_json_dict(style_fp.get("value_model"))
    markers = []
    for value in (
        list(style_fp.get("evidence_snippets") or [])
        + list(style_fp.get("signature_moves") or [])
        + list(style_fp.get("signature_response_moves") or [])
        + list((value_model.get("decision_heuristics") or []))
        + list((lexical_rules.get("signature_phrases") or []))
        + list((voice_profile.get("signature_phrases") or []))
    ):
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in markers:
            markers.append(cleaned)
        if len(markers) >= 12:
            break
    return markers


def _build_evidence_plan(
    question: str,
    creator_profile: Dict[str, Any],
    conversation_history: Optional[List[Dict[str, str]]] = None,
    *,
    top_score: Optional[float] = None,
    support_set: Optional[List[Dict[str, Any]]] = None,
    web_results: Optional[List[Dict[str, Any]]] = None,
) -> Optional[EvidencePlan]:
    try:
        return EvidenceRouter(creator_profile or {}).build_plan(
            question,
            conversation_history=conversation_history,
            top_score=top_score,
            retrieved_chunks=support_set,
            web_results=web_results,
        )
    except Exception as exc:
        logger.warning("Evidence plan build failed: %s", exc)
        return None


def _creator_column_exists(column_name: str) -> bool:
    cached = _CREATOR_COLUMN_CACHE.get(column_name)
    if cached is not None:
        return cached
    row = db.execute_one(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        ("creators", column_name),
    )
    exists = bool(row)
    _CREATOR_COLUMN_CACHE[column_name] = exists
    return exists


def _creator_select_expr(column_name: str) -> str:
    return column_name if _creator_column_exists(column_name) else f"NULL AS {column_name}"


def _get_creator_profile_row(creator_id: int, extra_columns: list[str]) -> Optional[Dict[str, Any]]:
    base_columns = ["id", "name", "handle"]
    select_parts = base_columns + [_creator_select_expr(col) for col in extra_columns]
    query = f"SELECT {', '.join(select_parts)} FROM creators WHERE id = %s"
    return db.execute_one(query, (creator_id,))


def _platform_from_url(url: str) -> str:
    """Derive platform key from URL. Ensures platform always matches canonical_url domain."""
    if not url or not isinstance(url, str):
        return "unknown"
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "instagram.com" in u:
        return "instagram"
    if "linkedin.com" in u:
        return "linkedin"
    if "twitter.com" in u or "x.com" in u:
        return "twitter"
    if "tiktok.com" in u:
        return "tiktok"
    if "reddit.com" in u:
        return "reddit"
    if "facebook.com" in u or "fb.com" in u:
        return "facebook"
    return "unknown"


_GENERIC_RESOURCE_TITLES = {
    "",
    "youtube",
    "youtube video",
    "video",
    "watch this",
    "watch this one",
    "this one",
    "link",
    "resource",
    "external resource",
}


def _resource_title_quality(title: str, url: str = "") -> float:
    cleaned = re.sub(r"\s+", " ", (title or "").strip())
    lowered = cleaned.lower()
    if lowered in _GENERIC_RESOURCE_TITLES:
        return 0.0
    if not cleaned:
        return 0.0

    words = re.findall(r"[a-z0-9']+", lowered)
    score = 1.0

    if re.search(r"https?://|www\.", cleaned, re.IGNORECASE):
        score -= 0.55

    if "." in cleaned and len(words) <= 4:
        score -= 0.35

    if len(words) <= 2:
        score -= 0.15

    compact = re.sub(r"[^a-z0-9]+", "", lowered)
    if (
        8 <= len(compact) <= 16
        and re.fullmatch(r"[a-z0-9]+", compact)
        and re.search(r"\d", compact)
        and any(host in (url or "").lower() for host in ("youtube.com", "youtu.be", "tiktok.com"))
    ):
        score -= 0.7

    if url:
        host = re.sub(r"^www\.", "", (re.sub(r"^https?://", "", url.lower())).split("/", 1)[0])
        if host and host in lowered and len(words) <= 4:
            score -= 0.3

    return max(0.0, min(1.0, score))


def _candidate_platform(candidate: Optional[Dict[str, Any]]) -> str:
    if not candidate:
        return ""
    platform = (candidate.get("platform") or "").lower().strip()
    if platform:
        return platform
    source_ref = candidate.get("source_ref") or {}
    platform = (source_ref.get("platform") or "").lower().strip()
    if platform:
        return platform
    return _platform_from_url(candidate.get("url") or source_ref.get("canonical_url") or "")


def _candidate_url(candidate: Optional[Dict[str, Any]]) -> str:
    if not candidate:
        return ""
    return (
        candidate.get("url")
        or ((candidate.get("source_ref") or {}).get("canonical_url"))
        or ""
    )


def _candidate_title(candidate: Optional[Dict[str, Any]]) -> str:
    if not candidate:
        return ""
    return (
        candidate.get("title")
        or ((candidate.get("source_ref") or {}).get("title"))
        or ""
    )


def _candidate_resource_kind(candidate: Optional[Dict[str, Any]]) -> str:
    if not candidate:
        return "resource"
    source_ref = candidate.get("source_ref") or {}
    raw_kind = str(
        candidate.get("resource_type")
        or source_ref.get("content_type")
        or candidate.get("type")
        or ""
    ).strip().lower()
    platform = _candidate_platform(candidate)
    url = _candidate_url(candidate).lower()

    if raw_kind in {"video", "podcast", "clip", "short", "shorts", "tutorial"}:
        return "video"
    if raw_kind == "reel":
        return "reel"
    if raw_kind in {"tweet", "status"}:
        return "status"
    if raw_kind == "post" and platform == "twitter":
        return "status"
    if raw_kind == "post" and platform == "tiktok":
        return "video"
    if raw_kind in {"post", "article", "course_lesson", "lesson", "website", "web"}:
        return raw_kind

    if "youtube.com" in url or "youtu.be" in url or "/watch" in url or "/shorts/" in url:
        return "video"
    if "instagram.com/reel/" in url:
        return "reel"
    if "instagram.com/p/" in url:
        return "post"
    if "tiktok.com/" in url and "/video/" in url:
        return "video"
    if "x.com/" in url or "twitter.com/" in url:
        return "status"
    if "linkedin.com/" in url or "facebook.com/" in url:
        return "post"

    if platform == "youtube":
        return "video"
    if platform == "instagram":
        return "post"
    if platform == "tiktok":
        return "video"
    if platform == "twitter":
        return "status"
    return "resource"


def _is_video_like_kind(kind: str) -> bool:
    return str(kind or "").strip().lower() in {"video", "reel"}


def _resource_label(kind: str, platform: str = "") -> str:
    normalized = str(kind or "").strip().lower()
    normalized_platform = str(platform or "").strip().lower()
    if normalized == "video":
        if normalized_platform == "tiktok":
            return "TikTok video"
        return "video"
    if normalized == "reel":
        return "reel" if normalized_platform != "instagram" else "Instagram reel"
    if normalized == "status":
        return "post on X" if normalized_platform in {"twitter", "x"} else "status post"
    if normalized == "post":
        if normalized_platform == "instagram":
            return "Instagram post"
        if normalized_platform == "linkedin":
            return "LinkedIn post"
        if normalized_platform == "facebook":
            return "Facebook post"
        return "post"
    if normalized == "article":
        return "article"
    if normalized == "course_lesson":
        return "lesson"
    if normalized == "lesson":
        return "lesson"
    return "resource"


def _resource_prompt_context(
    support_set: Optional[List[Dict[str, Any]]],
    user_msg: str,
) -> Dict[str, Any]:
    chunks = list(support_set or [])
    primary = chunks[0] if chunks else {}
    primary_kind = _candidate_resource_kind(primary)
    primary_platform = _candidate_platform(primary)
    primary_title = _candidate_title(primary)
    wants_video = any(token in (user_msg or "").lower() for token in ["video", "watch", "clip", "tutorial"])

    closest_video_title = ""
    closest_video_label = ""
    if wants_video and not _is_video_like_kind(primary_kind):
        for candidate in chunks[1:]:
            candidate_kind = _candidate_resource_kind(candidate)
            if _is_video_like_kind(candidate_kind):
                closest_video_title = _candidate_title(candidate)
                closest_video_label = _resource_label(candidate_kind, _candidate_platform(candidate))
                break

    return {
        "primary_kind": primary_kind,
        "primary_label": _resource_label(primary_kind, primary_platform),
        "primary_title": primary_title,
        "primary_is_video": _is_video_like_kind(primary_kind),
        "video_requested": wants_video,
        "closest_video_title": closest_video_title,
        "closest_video_label": closest_video_label,
    }


def _normalize_resource_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower()).strip()


def _is_live_web_chunk(chunk: Optional[Dict[str, Any]]) -> bool:
    if not chunk:
        return False
    return str(chunk.get("content") or "").startswith("[LIVE WEB SEARCH RESULT]")


def _is_direct_video_resource_url(url: str) -> bool:
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
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


def _is_viable_resource_url(url: str, require_video: bool = False) -> bool:
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").strip("/")
    if not host:
        return False

    if require_video:
        return _is_direct_video_resource_url(url)

    platform = _platform_from_url(url)
    if platform in {"youtube", "instagram", "tiktok", "facebook", "twitter"}:
        return _is_direct_video_resource_url(url)

    lowered_path = path.lower()
    if lowered_path in {"", "search", "explore", "results", "home", "login", "signup", "accounts"}:
        return False
    if any(token in lowered_path for token in ["accounts/login", "checkpoint", "authwall", "share", "redirect", "search"]):
        return False
    return True


def _support_resource_card_candidates(
    support_set: Optional[List[Dict[str, Any]]],
    *,
    preferred_platforms: Optional[List[str]] = None,
    require_video: bool = False,
    include_live_web: bool = False,
    question: str = "",
    answer_text: str = "",
    recommended_urls: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    preferred = {platform.lower() for platform in (preferred_platforms or []) if platform}
    selected: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()

    for chunk_index, chunk in enumerate(support_set or []):
        is_live_web = _is_live_web_chunk(chunk)
        if not include_live_web and is_live_web:
            continue
        if include_live_web and not is_live_web:
            continue

        url = (
            chunk.get("url")
            or (chunk.get("source_ref") or {}).get("canonical_url")
            or ""
        ).strip()
        title = (
            chunk.get("title")
            or (chunk.get("source_ref") or {}).get("title")
            or ""
        ).strip()
        if not url or url.lower() in seen_urls:
            continue
        if preferred:
            platform = _platform_from_url(url)
            if platform not in preferred:
                continue
        if not _is_viable_resource_url(url, require_video=require_video):
            continue
        if _resource_title_quality(title, url) < 0.45:
            continue

        content = str(chunk.get("content") or "")
        snippet = str(chunk.get("snippet") or "")
        selected.append({
            "title": title,
            "url": url,
            "platform": _platform_from_url(url),
            "content": content,
            "snippet": snippet,
            "chunk_index": chunk_index,
            "is_live_web": is_live_web,
            "score": _score_support_resource_candidate(
                title,
                url,
                content,
                snippet=snippet,
                question=question,
                answer_text=answer_text,
                recommended=bool(recommended_urls and url.lower() in recommended_urls),
                is_live_web=is_live_web,
                chunk_index=chunk_index,
            ),
        })
        seen_urls.add(url.lower())

    selected.sort(
        key=lambda candidate: (
            float(candidate.get("score", 0.0) or 0.0),
            0 if candidate.get("is_live_web") else 1,
            -int(candidate.get("chunk_index", 0) or 0),
        ),
        reverse=True,
    )
    # Only surface sources with meaningful relevance to this specific answer.
    # Prevents unrelated retrieved chunks from showing up as cards.
    _MIN_CARD_SCORE = 0.40
    return [c for c in selected if float(c.get("score", 0.0) or 0.0) >= _MIN_CARD_SCORE]


def _support_set_has_linkable_ingested_resource(
    support_set: Optional[List[Dict[str, Any]]],
    *,
    preferred_platforms: Optional[List[str]] = None,
    require_video: bool = False,
) -> bool:
    return bool(
        _support_resource_card_candidates(
            support_set,
            preferred_platforms=preferred_platforms,
            require_video=require_video,
            include_live_web=False,
        )
    )


def _is_recent_duplicate_candidate(
    candidate: Optional[Dict[str, Any]],
    seen_resources: Optional[Dict[str, Set[str]]] = None,
) -> bool:
    if not candidate or not seen_resources:
        return False

    url = _candidate_url(candidate)
    if url:
        youtube_match = re.search(r"(?:v=|youtu\.be/|/shorts/)([\w-]+)", url, re.IGNORECASE)
        if youtube_match and youtube_match.group(1) in (seen_resources.get("ids") or set()):
            return True

    title_key = _normalize_resource_title(_candidate_title(candidate))
    if title_key and title_key in (seen_resources.get("titles") or set()):
        return True

    return False


def _filter_candidates_for_requested_platforms(
    candidates: List[Dict[str, Any]],
    preferred_platforms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    if not preferred_platforms:
        return candidates
    preferred = {platform.lower() for platform in preferred_platforms if platform}
    matching = [candidate for candidate in candidates if _candidate_platform(candidate) in preferred]
    return matching or candidates


def _has_recommendable_resource(
    rec_result: Optional[Dict[str, Any]],
    preferred_platforms: Optional[List[str]] = None,
) -> bool:
    if not rec_result:
        return False
    best = rec_result.get("best_candidate") or {}
    url = _candidate_url(best)
    title = _candidate_title(best)
    if not url or not title:
        return False
    if preferred_platforms:
        preferred = {platform.lower() for platform in preferred_platforms if platform}
        if preferred and _candidate_platform(best) not in preferred:
            return False
    title_quality = float(best.get("title_quality", _resource_title_quality(title, url)))
    rerank_score = float(best.get("rerank_score", 0.0) or 0.0)
    return title_quality >= 0.45 and rerank_score >= 0.25


def _make_live_web_chunk(result: Dict[str, Any], index: int) -> Dict[str, Any]:
    title = (result.get("title") or "").strip()
    url = (result.get("url") or "").strip()
    snippet = re.sub(r"\s+", " ", (result.get("snippet") or "").strip())
    summary = snippet or title or "Verified external result."
    platform = _platform_from_url(url)
    if not platform or platform == "unknown":
        platform = "web"
    return {
        "chunk_id": f"web_{index}",
        "chunk_index": index,
        "distance": 0.05,
        "content": f"[LIVE WEB SEARCH RESULT]\n{summary}",
        "snippet": snippet,
        "url": url,
        "title": title,
        "source_ref": {
            "platform": platform,
            "canonical_url": url,
            "title": title,
        },
    }


def _thumbnail_for_url(url: str) -> str:
    youtube_match = re.search(r"(?:v=|youtu\.be/|/shorts/)([\w-]+)", url or "", re.IGNORECASE)
    if youtube_match:
        return f"https://img.youtube.com/vi/{youtube_match.group(1)}/mqdefault.jpg"
    return ""


def _preview_card_from_resource(title: str, url: str) -> Optional[Dict[str, str]]:
    title = (title or "").strip()
    url = (url or "").strip()
    if not url:
        return None
    # ── URL health-check: skip dead links ──
    if not check_url_alive_sync(url):
        logger.info("Dead link filtered from card: %s", url)
        return None
    if _resource_title_quality(title, url) < 0.45:
        title = ""
    return {
        "type": "preview_card",
        "title": title,
        "url": url,
        "thumbnail_url": _thumbnail_for_url(url),
    }


def _build_response_cards(
    rec_result: Optional[Dict[str, Any]],
    support_set: Optional[List[Dict[str, Any]]] = None,
    preferred_platforms: Optional[List[str]] = None,
    question: str = "",
    answer_text: str = "",
) -> List[Dict[str, str]]:
    cards: List[Dict[str, str]] = []
    desired_count = max(1, int((rec_result or {}).get("card_limit") or 1))
    resource_type = (((rec_result or {}).get("resource_intent") or {}).get("resource_type") or "video").lower()
    require_direct_video = resource_type not in {"article", "course_lesson", "web", "website"}
    recommended_urls = {
        (_candidate_url(candidate) or "").strip().lower()
        for candidate in [((rec_result or {}).get("best_candidate"))] + list((rec_result or {}).get("alternate_candidates") or [])
        if _candidate_url(candidate)
    }

    ingested_candidates = _support_resource_card_candidates(
        support_set,
        preferred_platforms=preferred_platforms,
        require_video=require_direct_video,
        include_live_web=False,
        question=question,
        answer_text=answer_text,
        recommended_urls=recommended_urls,
    )
    for candidate in ingested_candidates[:desired_count]:
        card = _preview_card_from_resource(candidate.get("title") or "", candidate.get("url") or "")
        if not card:
            continue
        cards.append(card)
    if cards:
        return cards

    if _has_recommendable_resource(rec_result, preferred_platforms=preferred_platforms):
        recommended_candidates = [
            (rec_result or {}).get("best_candidate")
        ] + list((rec_result or {}).get("alternate_candidates") or [])
        seen_urls: Set[str] = set()
        for candidate in recommended_candidates:
            if not candidate:
                continue
            if preferred_platforms:
                preferred = {p.lower() for p in preferred_platforms if p}
                if preferred and _candidate_platform(candidate) not in preferred:
                    continue
            card = _preview_card_from_resource(_candidate_title(candidate), _candidate_url(candidate))
            if not card or card["url"] in seen_urls:
                continue
            cards.append(card)
            seen_urls.add(card["url"])
            if len(cards) >= desired_count:
                return cards
        if cards:
            return cards

    live_candidates = _support_resource_card_candidates(
        support_set,
        preferred_platforms=preferred_platforms,
        require_video=require_direct_video,
        include_live_web=True,
        question=question,
        answer_text=answer_text,
        recommended_urls=recommended_urls,
    )
    for candidate in live_candidates[:desired_count]:
        card = _preview_card_from_resource(
            candidate.get("title") or "",
            candidate.get("url") or "",
        )
        if not card:
            continue
        cards.append(card)

    return cards


def _rescue_cards_from_answer(
    answer_text: str,
    support_set: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, str]]:
    """If the LLM's answer mentions a video title present in the support set,
    build a card for the best-matching resource so the user gets a clickable link
    even when the planner didn't activate video_policy."""
    if not answer_text or not support_set:
        return []

    answer_lower = answer_text.lower()
    best_card: Optional[Dict[str, str]] = None
    best_overlap = 0

    for chunk in support_set:
        title = (
            chunk.get("title")
            or (chunk.get("source_ref") or {}).get("title")
            or ""
        ).strip()
        url = (
            chunk.get("url")
            or (chunk.get("source_ref") or {}).get("canonical_url")
            or ""
        ).strip()
        if not title or not url:
            continue
        if not _is_viable_resource_url(url):
            continue
        # Check if a meaningful portion of the title appears in the answer.
        title_words = [w for w in re.findall(r"[a-z0-9']+", title.lower()) if len(w) > 2]
        if not title_words:
            continue
        hits = sum(1 for w in title_words if w in answer_lower)
        overlap = hits / len(title_words)
        # Require at least 35% word overlap to avoid false positives.
        if overlap >= 0.35 and hits >= 2 and overlap > best_overlap:
            card = _preview_card_from_resource(title, url)
            if card:
                best_card = card
                best_overlap = overlap

    return [best_card] if best_card else []


def _selected_recommendation_chunks(
    rec_result: Optional[Dict[str, Any]],
    preferred_platforms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    if not rec_result:
        return []

    desired_count = max(1, int((rec_result or {}).get("card_limit") or 1))
    selected_candidates = [
        (rec_result or {}).get("best_candidate")
    ] + list((rec_result or {}).get("alternate_candidates") or [])

    filtered_candidates = []
    seen_urls: Set[str] = set()
    preferred = {p.lower() for p in (preferred_platforms or []) if p}
    for candidate in selected_candidates:
        if not candidate:
            continue
        if preferred and _candidate_platform(candidate) not in preferred:
            continue
        url = (_candidate_url(candidate) or "").strip().lower()
        title = (_candidate_title(candidate) or "").strip()
        if not url or not title or url in seen_urls:
            continue
        seen_urls.add(url)
        filtered_candidates.append(candidate)
        if len(filtered_candidates) >= desired_count:
            break

    if not filtered_candidates:
        return []

    if len(filtered_candidates) == 1:
        return list((filtered_candidates[0].get("chunks") or [])[:3])

    selected_chunks: List[Dict[str, Any]] = []
    seen_chunk_keys: Set[str] = set()
    for candidate in filtered_candidates:
        candidate_chunks = candidate.get("chunks") or []
        if not candidate_chunks:
            continue
        for chunk in candidate_chunks:
            chunk_url = (
                chunk.get("url")
                or (chunk.get("source_ref") or {}).get("canonical_url")
                or _candidate_url(candidate)
            )
            chunk_title = (
                chunk.get("title")
                or (chunk.get("source_ref") or {}).get("title")
                or _candidate_title(candidate)
            )
            chunk_key = f"{(chunk_url or '').strip().lower()}::{_normalize_resource_title(chunk_title or '')}"
            if chunk_key in seen_chunk_keys:
                continue
            seen_chunk_keys.add(chunk_key)
            selected_chunks.append(chunk)
            break

    return selected_chunks[:desired_count]


def _unwrap_structured_answer(answer: str) -> str:
    text = (answer or "").strip()
    if not text:
        return text
    if text.startswith("{") and text.endswith("}"):
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            for key in ("content", "answer", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    text = value.strip()
                    break

    text = re.sub(r"(?im)^\[LIVE WEB SEARCH RESULT\]\s*", "", text)
    text = re.sub(r"(?im)^(Title|Summary):\s*", "", text)
    text = re.sub(r"(?im)^URL:\s*", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# Algorithm settings
K_RETRIEVE = 25  # Broad retrieval
K_FINAL = 12  # Final support set after re-ranking
MIN_SUPPORT = 1  # Minimum chunks supporting key claims
MAX_REPAIR = 1  # Max repair attempts

# Source quality weights (higher = better)
SOURCE_QUALITY_MAP = {
    "video": 1.0,  # Full videos/podcasts
    "reel": 0.9,  # Short videos
    "post": 0.8,  # Social posts
    "tweet": 0.7,  # Tweets
    "comment": 0.5,  # Comments
    "caption": 0.6,  # Captions only
}


def get_enabled_platforms_for_creator(creator_id: int) -> Optional[List[str]]:
    """
    Return enabled platform keys from creator.platform_configs, or None if not restricted.
    Used to filter retrieval to only chunks from enabled platforms.
    """
    try:
        row = db.execute_one(
            "SELECT platform_configs FROM creators WHERE id = %s LIMIT 1",
            (creator_id,),
        )
    except Exception:
        return None
    pc = row.get("platform_configs") if row else None
    if not pc:
        return None
    if isinstance(pc, str):
        try:
            pc = json.loads(pc)
        except Exception:
            return None
    if not isinstance(pc, dict):
        return None
    enabled = [
        k for k, cfg in pc.items()
        if isinstance(cfg, dict) and cfg.get("enabled") is True
    ]
    return enabled if enabled else None


def build_search_query(question: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    """
    Step 1: Build a search query from question + minimal recent context keywords.
    When user asks for "links for both/those", augment with last assistant message
    so retrieval favors the same items we just recommended.
    """
    # 1. Detect if this is a "request for more" follow-up
    more_triggers = ["another", "other", "more", "else", "different", "next"]
    is_request_more = any(t in question.lower() for t in more_triggers)
    
    query_parts = [question]

    # Follow-up "links for both/those": bias retrieval toward same items as last reply
    if history and is_follow_up_requesting_links(question, history):
        last_assistant = None
        for m in reversed(history):
            if (m.get("role") or "").lower() == "assistant":
                last_assistant = (m.get("content") or m.get("text") or "").strip()
                break
        if last_assistant:
            # Strip markdown, truncate; add to query so we retrieve same videos/posts
            clean = re.sub(r"\*\*", "", last_assistant)
            clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)  # keep link text, drop URL
            query_parts.append(clean[:500])

    elif history and is_request_more:
        # PIVOT: Find the last few mentioned videos and EXPLICITLY ask for something else
        seen_recent = []
        for m in reversed(history[-10:]):
            if (m.get("role") or "").lower() == "assistant":
                text = m.get("content") or m.get("text") or ""
                quoted = re.findall(r'"([^"]+)"', text)
                seen_recent.extend(quoted)
                for card in m.get("cards") or []:
                    title = (card.get("title") or "").strip()
                    if title:
                        seen_recent.append(title)
        
        if seen_recent:
            prior_topic = ""
            for msg in reversed(history[-10:]):
                if (msg.get("role") or "").lower() != "user":
                    continue
                user_text = (msg.get("content") or msg.get("text") or "").strip()
                if not user_text:
                    continue
                if any(trigger in user_text.lower() for trigger in more_triggers):
                    continue
                prior_topic = user_text
                break
            topic_seed = prior_topic or "related creator content"
            query_parts.append(f"{topic_seed} excluding {', '.join(seen_recent[:2])}")
        else:
            recent_text = " ".join([msg.get("content", "") for msg in history[-3:] if msg.get("role") == "user"])
            query_parts.append(recent_text)

    elif history:
        # Extract 3-6 keywords from last few user turns
        recent_text = " ".join([
            msg.get("content", "") for msg in history[-3:]
            if msg.get("role") == "user"
        ])
        words = re.findall(r"\b[a-z]{4,}\b", recent_text.lower())
        stop_words = {"that", "this", "what", "when", "where", "which", "with", "from", "have", "been", "will", "would"}
        keywords = [w for w in words if w not in stop_words][:6]
        if keywords:
            query_parts.extend(keywords)

    return " ".join(query_parts)


def rewrite_queries(intent_plan: Dict[str, Any], creator_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Stage 1: Multi-stage Query Rewriting.
    Turns structured intent into 3-8 query variants + negative keywords.
    """
    import backend.rag as rag
    import json
    
    creator_name = creator_profile.get("name") if creator_profile else "the creator"
    topic_str = ", ".join(intent_plan.get("topic_entities", []))
    
    system_prompt = f"""
You are a Search Query Architect. Your goal is to rewrite a user's intent into 5-8 highly effective search queries for YouTube and internal DBs.

Output ONLY a JSON object:
{{
  "queries": [
    "exact variant",
    "expanded variant",
    "semantic variant",
    "format-targeted variant"
  ],
  "negatives": ["-shorts", "-reaction"]
}}

GUIDELINES:
- Include at least 2 queries with the creator name: "{creator_name}".
- Create "Exact" queries for the specific topic: "{topic_str}".
- Create "Expanded" queries with educational intent (e.g., 'how to', 'tutorial').
- Create "Semantic" queries focusing on the underlying concepts.
- Create "Format-targeted" queries based on the user's help criteria: {intent_plan.get('help_criteria', [])}.
- Generate negative keywords from constraints: {intent_plan.get('constraints', [])}.
"""

    user_prompt = f"Intent Plan: {json.dumps(intent_plan)}"
    
    try:
        response_text = rag.generate_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model=settings.REWRITE_MODEL,
            temperature=0.0,
            json_mode=True
        )
        data = json.loads(response_text)
        # Ensure creator filter is in queries
        if creator_name and creator_name != "the creator":
            for i in range(len(data.get("queries", []))):
                if creator_name.lower() not in data["queries"][i].lower():
                    if i % 2 == 0: # Add to every other query if missing
                         data["queries"][i] = f"{data['queries'][i]} {creator_name}"
        return data
    except Exception as e:
        logger.error(f"Query rewrite failed: {e}")
        return {"queries": [intent_plan.get("query", "")], "negatives": []}


def _build_recommendation_context_features(
    user_message: str,
    history: Optional[List[Dict[str, str]]] = None,
    resource_intent: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    lowered = (user_message or "").lower()
    request_type = str((resource_intent or {}).get("request_type") or "").lower()
    specificity = str((resource_intent or {}).get("specificity") or "").lower()
    intent_type = str((resource_intent or {}).get("intent_type") or "").lower()
    learning_phase = str((resource_intent or {}).get("learning_phase") or "").lower()

    already_shown_titles = list((_get_suggested_resources(history) or {}).get("titles") or [])
    return {
        "wants_video": any(token in lowered for token in ["video", "watch", "clip", "youtube", "tiktok", "reel"]),
        "wants_post": any(token in lowered for token in ["post", "tweet", "tweets", "x", "twitter", "instagram post", "status"]),
        "wants_proof": any(token in lowered for token in ["proof", "source", "show me", "where did you", "where do you", "evidence"]),
        "wants_tactical": any(token in lowered for token in ["how", "steps", "script", "template", "process", "tactic", "tactical", "execute", "fix"]),
        "wants_mindset": any(token in lowered for token in ["mindset", "belief", "discipline", "confidence", "motivation", "focus"]),
        "wants_story": any(token in lowered for token in ["story", "journey", "lesson", "lessons", "learned", "mistake", "experience"]),
        "wants_beginner": any(token in lowered for token in ["start", "starting", "beginner", "beginning", "first", "basics", "foundation"]) or learning_phase == "overview",
        "wants_advanced": any(token in lowered for token in ["advanced", "scale", "optimize", "refine", "deep dive"]),
        "needs_exact_match": specificity == "specific" or request_type == "explicit" or bool(extract_named_resource_fragments(user_message)),
        "is_ambiguous": request_type == "implicit" or intent_type in {"how_to", "recommend_content"} or _is_elliptical_followup(user_message),
        "learning_phase": learning_phase,
        "request_type": request_type,
        "intent_type": intent_type,
        "already_shown_titles": already_shown_titles,
    }


def _dedupe_queries(queries: List[str], limit: int = 3) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for query in queries or []:
        clean = re.sub(r"\s+", " ", str(query or "")).strip()
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        ordered.append(clean)
        if len(ordered) >= limit:
            break
    return ordered


def _build_recommendation_query_variants(
    base_query: str,
    resource_intent: Dict[str, Any],
    creator_profile: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, str]]] = None,
    *,
    allow_llm: bool = False,
    limit: int = 3,
) -> List[str]:
    queries = [base_query]
    creator_name = str((creator_profile or {}).get("name") or "").strip()
    named_fragments = extract_named_resource_fragments(base_query)
    must_terms = [str(item).strip() for item in (resource_intent.get("must_terms") or []) if str(item or "").strip()]
    resource_type = str(resource_intent.get("resource_type") or "").strip().lower()
    implicit_goal = str(resource_intent.get("implicit_goal") or "").strip()
    task_axis = str(resource_intent.get("task_axis") or "").strip()

    if named_fragments:
        fragment = named_fragments[0]
        queries.append(fragment if not creator_name else f"{fragment} {creator_name}")
    elif must_terms:
        query = " ".join(must_terms[:4])
        queries.append(query if not creator_name else f"{query} {creator_name}")

    if resource_type in {"video", "article", "course_lesson"}:
        medium_hint = "video" if resource_type == "video" else resource_type.replace("_", " ")
        queries.append(f"{base_query} {medium_hint}".strip())
    elif task_axis:
        queries.append(f"{base_query} {task_axis}".strip())

    if allow_llm:
        rewritten = rewrite_queries(
            {
                "query": base_query,
                "intent_type": resource_intent.get("intent_type"),
                "implicit_goal": implicit_goal,
                "task_axis": task_axis,
                "help_criteria": resource_intent.get("help_criteria") or [],
                "resource_type": resource_type or "any",
                "topic_entities": named_fragments or must_terms,
                "learning_phase": resource_intent.get("learning_phase"),
            },
            creator_profile=creator_profile or {},
        )
        queries.extend(rewritten.get("queries") or [])

    contextualized = []
    for query in queries:
        contextualized.append(build_search_query(query, history))
    return _dedupe_queries(contextualized + queries, limit=limit)


def _annotate_retrieval_chunks(
    chunks: List[Dict[str, Any]],
    *,
    retrieval_mode: str,
    query_variant: str,
) -> List[Dict[str, Any]]:
    annotated: List[Dict[str, Any]] = []
    for rank, chunk in enumerate(chunks or [], start=1):
        item = dict(chunk)
        item["retrieval_mode"] = retrieval_mode
        item["query_variant"] = query_variant
        item["retrieval_rank"] = rank
        item["retrieval_rrf"] = 1.0 / (60.0 + rank)
        annotated.append(item)
    return annotated


def _hybrid_retrieve_recommendation_chunks(
    creator_id: int,
    query_variants: List[str],
    *,
    enabled_platforms: Optional[List[str]] = None,
    k_dense: int = 18,
    k_sparse: int = 10,
    base_query: str = "",
    base_embedding: Optional[List[float]] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from backend.rag import get_client

    all_chunks: List[Dict[str, Any]] = []
    debug_payload: Dict[str, Any] = {"queries": []}
    for query in _dedupe_queries(query_variants, limit=3):
        dense_chunks: List[Dict[str, Any]] = []
        sparse_chunks: List[Dict[str, Any]] = []
        try:
            if base_embedding and base_query and query.strip().lower() == base_query.strip().lower():
                q_emb = base_embedding
            else:
                emb_resp = get_client().embeddings.create(model=settings.EMBEDDING_MODEL, input=query)
                q_emb = emb_resp.data[0].embedding
            dense_chunks = retrieve_candidates(
                creator_id,
                q_emb,
                k_retrieve=k_dense,
                enabled_platforms=enabled_platforms,
            )
            dense_chunks = _annotate_retrieval_chunks(dense_chunks, retrieval_mode="dense", query_variant=query)
        except Exception as exc:
            logger.warning("Recommendation dense retrieval failed for '%s': %s", query, exc)

        try:
            sparse_chunks = retrieve_sparse_text_matches(
                creator_id,
                query,
                limit=k_sparse,
                enabled_platforms=enabled_platforms,
            )
            sparse_chunks = _annotate_retrieval_chunks(sparse_chunks, retrieval_mode="sparse", query_variant=query)
        except Exception as exc:
            logger.warning("Recommendation sparse retrieval failed for '%s': %s", query, exc)

        debug_payload["queries"].append(
            {
                "query": query,
                "dense_count": len(dense_chunks),
                "sparse_count": len(sparse_chunks),
            }
        )
        all_chunks.extend(dense_chunks)
        all_chunks.extend(sparse_chunks)
    return all_chunks, debug_payload

def retrieve_candidates(
    creator_id: int,
    query_embedding: List[float],
    k_retrieve: int = K_RETRIEVE,
    max_distance: float = 1.15,
    enabled_platforms: Optional[List[str]] = None,
    debug: bool = False,
) -> List[Dict[str, Any]]:
    """
    Step 2: Retrieve broadly (high recall).
    Always filtered by creator_id. Optionally filtered by enabled_platforms from
    creator.platform_configs. canonical_url is always from content item URL
    (source_url), never from author/profile URL.
    """
    embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"
    
    base = """
        SELECT 
            c.id as chunk_id,
            c.chunk_index,
            c.chunk_text,
            c.metadata as chunk_metadata,
            (e.embedding <=> %s::vector) as distance,
            d.id as document_id,
            d.title as document_title,
            d.source as document_source,
            d.source_id as document_source_id,
            d.metadata as document_metadata
        FROM chunks c
        JOIN embeddings e ON c.id = e.chunk_id
        JOIN documents d ON c.document_id = d.id
        WHERE d.creator_id = %s
        AND (d.metadata->>'type' IS NULL OR d.metadata->>'type' != 'persona')
        AND e.model = %s
        AND (e.embedding <=> %s::vector) <= %s
    """
    order_limit = """
        ORDER BY e.embedding <=> %s::vector
        LIMIT %s
    """
    params: List[Any] = [
        embedding_str, creator_id, settings.EMBEDDING_MODEL,
        embedding_str, max_distance,
    ]
    if enabled_platforms:
        # Restrict to chunks whose document platform is in enabled set (case-insensitive)
        low = [str(p).lower() for p in enabled_platforms]
        base += " AND (LOWER(COALESCE(d.metadata->>'platform','')) = ANY(%s) OR LOWER(COALESCE(d.source,'')) = ANY(%s))"
        params.extend([low, low])
    base += order_limit
    params.extend([embedding_str, k_retrieve])

    results = db.execute_query(base, tuple(params))

    candidates = []
    for r in results:
        chunk_meta = r.get("chunk_metadata") or {}
        if isinstance(chunk_meta, str):
            try:
                chunk_meta = json.loads(chunk_meta)
            except Exception:
                chunk_meta = {}
        doc_meta = r.get("document_metadata") or {}
        if isinstance(doc_meta, str):
            try:
                doc_meta = json.loads(doc_meta)
            except Exception:
                doc_meta = {}

        # canonical_url: content item URL only (source_url). Never author/profile URL.
        source_url = chunk_meta.get("source_url") or doc_meta.get("source_url") or ""
        stored = chunk_meta.get("platform") or doc_meta.get("platform") or r.get("document_source") or ""
        # Always derive platform from URL so we never show "instagram: https://youtube.com/..."
        platform = _platform_from_url(source_url) if source_url else (stored or "unknown")
        platform = (platform or "unknown").lower()
        content_id = chunk_meta.get("content_id") or doc_meta.get("content_id") or r.get("document_source_id") or ""
        content_type = chunk_meta.get("type") or chunk_meta.get("content_type") or "unknown"
        published_at = chunk_meta.get("published_at") or doc_meta.get("published_at")

        # ── Video timestamps from chunk metadata (if preserved during ingestion) ──
        start_time_sec = chunk_meta.get("start_time_sec")
        end_time_sec = chunk_meta.get("end_time_sec")

        source_ref_dict = {
            "platform": platform,
            "content_id": content_id,
            "canonical_url": source_url,
            "title": r.get("document_title") or "",
            "published_at": published_at,
            "content_type": content_type,
        }
        if start_time_sec is not None:
            source_ref_dict["start_time_sec"] = start_time_sec
        if end_time_sec is not None:
            source_ref_dict["end_time_sec"] = end_time_sec

        cand = {
            "chunk_id": r["chunk_id"],
            "chunk_index": r["chunk_index"],
            "distance": float(r["distance"]),
            "content": r["chunk_text"],
            "source_ref": source_ref_dict,
            "document_id": r["document_id"],
        }
        candidates.append(cand)

    # Platform purity: when enabled_platforms set, keep only chunks whose URL-derived platform is in that set
    if enabled_platforms:
        allowed = { str(p).lower() for p in enabled_platforms }
        filtered = [ c for c in candidates if (c["source_ref"].get("platform") or "").lower() in allowed ]
        if len(filtered) < len(candidates) and debug:
            logger.info("retrieval_debug platform_filter kept=%d dropped=%d enabled=%s", len(filtered), len(candidates) - len(filtered), list(allowed))
        candidates = filtered

    if debug and candidates:
        for i, c in enumerate(candidates[:10]):
            ref = c["source_ref"]
            logger.info(
                "retrieval_debug chunk_id=%s creator_id=%s platform=%s canonical_url=%s",
                c["chunk_id"], creator_id, ref.get("platform"), ref.get("canonical_url"),
            )

    # ── Enrich with total_chunks per document (for positional estimation) ──
    doc_ids = list({c["document_id"] for c in candidates if c.get("document_id")})
    doc_chunk_counts: Dict[Any, int] = {}
    if doc_ids:
        try:
            placeholders = ",".join(["%s"] * len(doc_ids))
            rows = db.execute_query(
                f"SELECT document_id, COUNT(*) AS cnt FROM chunks WHERE document_id IN ({placeholders}) GROUP BY document_id",
                tuple(doc_ids),
            )
            for row in (rows or []):
                doc_chunk_counts[row["document_id"]] = int(row["cnt"])
        except Exception as e:
            logger.debug("total_chunks enrichment failed: %s", e)
    for c in candidates:
        c["total_chunks"] = doc_chunk_counts.get(c.get("document_id"), 0)

    return candidates


def _normalize_search_terms(text: str) -> List[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "what", "which", "where", "when",
        "your", "about", "into", "then", "them", "they", "have", "been", "would", "could",
        "should", "video", "videos", "link", "links", "watch", "show", "send", "online",
        "first", "start", "starting", "begin", "best", "good", "recommend", "recommended",
        "reccomend", "watching", "learn",
    }
    return [w for w in words if len(w) > 2 and w not in stop]


def _term_overlap_score(left: str, right: str, limit: int = 5) -> float:
    left_terms = _normalize_search_terms(left)
    right_terms = set(_normalize_search_terms(right))
    if not left_terms or not right_terms:
        return 0.0
    hits = sum(1 for term in left_terms[:limit] if term in right_terms)
    return min(1.0, hits / max(1, min(len(left_terms[:limit]), limit)))


def _title_verbatim_match_bonus(title: str, question: str) -> float:
    """Returns a bonus [0.0, 0.50] when the source title's words appear in the
    user's question. Handles explicit references like: 'in your video Give me
    25 minutes...' — the matching source should dominate all others."""
    title_words = [w for w in re.findall(r"[a-z0-9]+", (title or "").lower()) if len(w) > 2]
    q_words = set(re.findall(r"[a-z0-9]+", (question or "").lower()))
    if len(title_words) < 3 or not q_words:
        return 0.0
    hits = sum(1 for w in title_words if w in q_words)
    ratio = hits / len(title_words)
    if ratio >= 0.80:
        return 0.50
    if ratio >= 0.60:
        return 0.28
    if ratio >= 0.40:
        return 0.10
    return 0.0


def _score_support_resource_candidate(
    title: str,
    url: str,
    content: str,
    *,
    snippet: str = "",
    question: str = "",
    answer_text: str = "",
    recommended: bool = False,
    is_live_web: bool = False,
    chunk_index: int = 0,
) -> float:
    haystack = " ".join(
        part for part in [
            title,
            snippet,
            re.sub(r"\s+", " ", (content or "").replace("[LIVE WEB SEARCH RESULT]", "").strip())[:280],
        ]
        if part
    )
    title_quality = _resource_title_quality(title, url)
    question_overlap = _term_overlap_score(question, haystack)
    answer_overlap = _term_overlap_score(answer_text, haystack)
    support_rank_bonus = max(0.0, 0.16 - (chunk_index * 0.02))
    title_verbatim_bonus = _title_verbatim_match_bonus(title, question)

    score = (
        (0.28 if not is_live_web else 0.12)
        + (0.24 if recommended else 0.0)
        + (0.12 if (not recommended and not is_live_web) else 0.0)  # ingested content bonus
        + (title_quality * 0.22)
        + (question_overlap * 0.28)      # primary: does this source match what the user asked?
        + (answer_overlap * 0.20)         # secondary: was it referenced in the response?
        + support_rank_bonus
        + title_verbatim_bonus            # 0.0–0.50 when user quoted the video title
    )

    # If the source has zero overlap with what the user actually asked,
    # hard-cap its score below the citation threshold so it never surfaces.
    # This prevents irrelevant RAG chunks (e.g. AI-tools videos for a books
    # question) from appearing as citations just because of high base/rank score
    # or incidental answer-text overlap on shared business vocabulary.
    if question_overlap < 0.05 and title_verbatim_bonus < 0.10:
        score = min(score, 0.30)

    return round(score, 4)


def _topic_match_score(result: Dict[str, Any], question: str) -> float:
    q_terms = set(_normalize_search_terms(question))
    if not q_terms:
        return 0.5
    hay = " ".join([str(result.get("title") or ""), str(result.get("snippet") or "")]).lower()
    hits = sum(1 for term in q_terms if term in hay)
    return min(1.0, hits / max(1, min(len(q_terms), 4)))


def _filter_live_web_results(results: List[Dict[str, Any]], question: str, require_video: bool = False) -> List[Dict[str, Any]]:
    filtered = []
    for result in results or []:
        if not isinstance(result, dict) or not result.get("url"):
            continue
        url = str(result.get("url") or "").strip()
        title = str(result.get("title") or "").strip()
        confidence = float(result.get("confidence", 0.0) or 0.0)
        relation = (result.get("relation") or "").upper()
        platform = (result.get("platform") or "").lower()
        topic_score = _topic_match_score(result, question)
        query_fidelity = float(result.get("query_fidelity_score", topic_score) or topic_score or 0.0)
        title_quality = _resource_title_quality(title, url)
        result["topic_score"] = topic_score
        result["query_fidelity_score"] = query_fidelity
        result["title_quality"] = title_quality

        if not _is_viable_resource_url(url, require_video=require_video):
            continue

        if require_video:
            if platform and platform not in {"youtube", "instagram", "tiktok", "facebook", "twitter"}:
                continue
            if relation not in {"SELF", "AFFILIATED"}:
                continue
            if title_quality < 0.45:
                continue
            if relation == "SELF":
                if confidence < 0.72 or query_fidelity < 0.35:
                    continue
            else:
                if confidence < 0.80 or query_fidelity < 0.45:
                    continue
        else:
            if title_quality < 0.35 and _platform_from_url(url) != "web":
                continue
            if confidence < 0.58 and query_fidelity < 0.45:
                continue

        filtered.append(result)

    filtered.sort(
        key=lambda r: (
            float(r.get("confidence", 0.0) or 0.0),
            float(r.get("query_fidelity_score", 0.0) or 0.0),
            float(r.get("topic_score", 0.0) or 0.0),
        ),
        reverse=True,
    )
    return filtered


def _is_elliptical_followup(question: str) -> bool:
    words = re.findall(r"[a-z0-9']+", (question or "").lower())
    if not words or len(words) > 7:
        return False
    follow_terms = {"that", "those", "it", "this", "one", "ones", "another", "other", "else", "more", "same", "again"}
    media_terms = {"video", "videos", "reel", "reels", "post", "posts", "link", "links", "source", "sources", "clip", "clips", "watch"}
    return any(word in follow_terms for word in words) or any(word in media_terms for word in words)


def _recent_discussion_topic(question: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    candidate_text = question or ""
    if history and _is_elliptical_followup(question):
        recent_user = [m.get("content") or m.get("text") or "" for m in history[-4:] if (m.get("role") or "") == "user"]
        if recent_user:
            candidate_text = recent_user[-1]
    terms = _normalize_search_terms(candidate_text)
    if not terms:
        return "this"
    return " ".join(terms[:4])


def _extract_previously_cited_titles(history: Optional[List[Dict[str, str]]]) -> List[str]:
    """Scan recent assistant messages for video/content titles that were cited."""
    titles: List[str] = []
    seen: set = set()
    for msg in reversed(list(history or [])[-10:]):
        if (msg.get("role") or "").lower() != "assistant":
            continue
        # Check card metadata
        for card in (msg.get("cards") or []):
            if isinstance(card, dict):
                t = (card.get("title") or "").strip()
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    titles.append(t)
        # Check citation metadata
        for cit in (msg.get("citations") or []):
            if isinstance(cit, dict):
                t = (cit.get("title") or "").strip()
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    titles.append(t)
    return titles


def _build_not_online_fallback(
    question: str,
    creator_name: str,
    history: Optional[List[Dict[str, str]]] = None,
    kind: str = "source",
    style_fingerprint: Optional[Dict[str, Any]] = None,
) -> str:
    # ── Self-contradiction guard ──────────────────────────────
    # If we already cited videos in this conversation, do NOT say
    # "I don't have a video".  Return empty so the system continues
    # to the LLM-rendered answer path instead.
    previously_cited = _extract_previously_cited_titles(history)
    if previously_cited:
        logger.debug(
            "Skipping not-online fallback — %d source(s) already cited in history",
            len(previously_cited),
        )
        return ""

    topic = _recent_discussion_topic(question, history)
    sfp = style_fingerprint or {}
    lexical = sfp.get("lexical_rules") or {}
    sig_phrases = list(lexical.get("signature_phrases") or [])[:3]
    mode_matrix = sfp.get("mode_matrix") or {}
    boundary_rules = mode_matrix.get("boundary") or {}

    # Try LLM micro-call if we have voice data
    if sig_phrases or boundary_rules:
        try:
            resource_type = "video" if kind == "video" else "public source"
            voice_hint = ""
            if sig_phrases:
                voice_hint = f"Use phrases like: {', '.join(sig_phrases[:2])}. "
            if boundary_rules:
                bt = boundary_rules.get("tone") or boundary_rules.get("approach")
                if bt:
                    voice_hint += f"Boundary tone: {bt}. "
            mini_prompt = (
                f"You are {creator_name}. A fan asked for a {resource_type} about \"{topic}\". "
                f"You don't have one you'd feel good sending right now. "
                f"Write 1-2 sentences saying that honestly and offering to answer directly or narrow what they want. "
                f"VOICE RULES: {voice_hint}"
                f"Max 35 words. No URLs. No markdown. Sound like yourself."
            )
            result = rag.generate_chat_completion(
                messages=[{"role": "user", "content": mini_prompt}],
                model=settings.ROUTER_MODEL,
                temperature=0.5,
                max_tokens=60,
            )
            if result and len(result.strip()) > 10:
                return result.strip().strip('"')
        except Exception as e:
            logger.debug("Persona-flavored not-online fallback failed: %s", e)

    # Template fallback
    if kind == "video":
        if topic != "this":
            return f"I don't have a specific video on {topic} I'd feel good sending you right now. I can still answer it directly here, or help narrow what kind of clip you want."
        return "I don't have a specific video I'd feel good sending you right now. I can still answer it directly here, or help narrow what kind of clip you want."
    return "I don't have a public source for that I'd trust enough to send you right now. I can still answer it directly here, or help narrow exactly what you want sourced."


def _contains_placeholder_link_artifacts(text: str) -> bool:
    cleaned = str(text or "")
    if not cleaned:
        return False
    return bool(
        re.search(r'(?<!\\w)""(?:/[^\s"]*)?', cleaned)
        or re.search(r"(?<!\\w)''(?:/[^\\s']*)?", cleaned)
    )


def _should_force_resource_fallback(
    no_online_fallback: Optional[str],
    *,
    wants_link: bool,
    has_linkable_ingested_resource: bool,
    web_results: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    return bool(
        no_online_fallback
        and wants_link
        and not has_linkable_ingested_resource
        and not (web_results or [])
    )


def needs_links(user_msg: str) -> bool:
    """
    True if the user is asking for links/sources/proof, or asking for a video recommendation.
    Only include links in the final answer when this is True.
    """
    t = (user_msg or "").lower()
    
    # Robust intent matching for video requests (e.g., "what video", "whats a video", "video youd recommend")
    if any(media in t for media in ["video", "reel", "post"]):
        if any(action in t for action in ["what", "which", "any", "recommend", "reccomend", "send", "show", "link", "url", "best", "good", "watch"]):
            return True
            
    # Direct explicit triggers
    triggers = [
        "link", "source", "url", "proof", "prove it", "are you sure",
        "reference", "references", "cite", "citation", "give me the links",
        "links for", "links to those", "links to both",
        "where do you talk about", "where do you cover", "where in your videos",
        "where in your content", "have you talked about", "do you have a video on",
    ]
    return any(x in t for x in triggers)


def _assistant_recently_offered_resource(last_bot_msg: str) -> bool:
    msg = (last_bot_msg or "").lower()
    if not msg:
        return False
    return any(token in msg for token in [
        "video", "videos", "watch this", "watch that", "check out", "link",
        "links", "source", "sources", "send you", "point you to"
    ])


def _is_followup_resource_request(question: str, last_bot_msg: str) -> bool:
    q = (question or "").lower().strip()
    if not q or not _assistant_recently_offered_resource(last_bot_msg):
        return False
    if needs_links(question):
        return True

    followup_phrases = [
        "another one", "another video", "another reel", "another post", "other videos",
        "other video", "more videos", "more like that", "more on that", "that one",
        "those videos", "send it", "show it", "which one", "what else can i watch"
    ]
    if any(phrase in q for phrase in followup_phrases):
        return True

    words = re.findall(r"[a-z0-9']+", q)
    if len(words) > 6:
        return False
    referential = {"that", "those", "it", "another", "other", "more", "same", "one", "ones"}
    media = {"video", "videos", "reel", "reels", "post", "posts", "link", "links", "source", "sources", "clip", "clips", "watch"}
    return any(word in referential for word in words) and any(word in media for word in words)


def _should_run_resource_recommender(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    last_bot_msg: str = "",
) -> bool:
    q = (question or "").lower().strip()
    if not q:
        return False
    if needs_links(question) or _is_followup_resource_request(question, last_bot_msg):
        return True
    if classify_intent(question) == "request_sources":
        return True

    explicit_phrases = [
        "what should i watch",
        "what should i read",
        "where do i start",
        "where should i start",
        "where do you talk about",
        "where do you cover",
        "where in your videos",
        "where in your content",
        "have you talked about",
        "do you have a video on",
        "do you have a post on",
        "watch first",
        "read first",
        "send me",
        "show me",
        "recommend a video",
        "recommend me a video",
        "recommend a post",
        "recommend a reel",
        "best video",
        "best post",
        "best reel",
        "any resources",
        "good resources",
        "course lesson",
        "course module",
        "which lesson",
        "which module",
        "which video",
        "which post",
        "which reel",
        "where did you talk about",
        "did you talk about",
        "where did you say",
    ]
    if any(phrase in q for phrase in explicit_phrases):
        return True

    words = set(re.findall(r"[a-z0-9']+", q))
    media_words = {
        "video", "videos", "watch", "reel", "reels", "clip", "clips", "post", "posts",
        "link", "links", "source", "sources", "resource", "resources", "article",
        "articles", "lesson", "lessons", "module", "modules", "course", "courses",
        "episode", "episodes", "read",
    }
    action_words = {"show", "send", "recommend", "reccomend", "which", "what", "where", "best", "good", "watch", "read", "find"}
    return bool(words & media_words and words & action_words)


def _should_run_exact_text_match(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    *,
    wants_resource: bool = False,
) -> bool:
    q = (question or "").lower().strip()
    if not q:
        return False
    if wants_resource or needs_links(question):
        return True
    if extract_named_resource_fragments(question):
        return True
    if any(token in q for token in ['"', "“", "”"]):
        return True
    if any(phrase in q for phrase in [
        "where did you say",
        "where did you talk",
        "where do you talk",
        "where do you cover",
        "what did you say",
        "did you mention",
        "exact words",
        "quote",
        "title",
        "called",
        "proof",
        "source",
    ]):
        return True
    return len(q.split()) <= 6


def _should_speculate_live_search(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    *,
    explicit_link_request: bool = False,
    context_needs_video: bool = False,
    should_run_recommender: bool = False,
) -> bool:
    if needs_fresh_public_web_search(question, history):
        return True
    if (explicit_link_request or context_needs_video) and not should_run_recommender:
        return True
    # Proactively speculate on any factual / creator-world question so web
    # results are ready by the time the pipeline needs them.  This runs in
    # parallel with RAG and adds zero latency if the results aren't needed.
    lowered = (question or "").lower()
    _factual_signal = bool(
        re.search(r"\bwhen\s+(did|was|were|is)\b", lowered)
        or re.search(r"\bwhat\s+(year|date|month|day)\b", lowered)
        or re.search(r"\bhow\s+(much|many|old|long|tall)\b", lowered)
        or re.search(r"\bwhere\s+(is|are|was|were|did|does|do)\b", lowered)
        or re.search(r"\bwho\s+(is|are|was|were|did|does)\b", lowered)
        or re.search(r"\b(net worth|followers|subscribers|valuation|founded|revenue)\b", lowered)
        or re.search(r"\b(married|wife|husband|spouse|children|kids|family|born|age|hometown)\b", lowered)
    )
    if _factual_signal:
        return True
    return False


def _wants_multiple_resources(question: str) -> bool:
    q = (question or "").lower()
    return any(token in q for token in [
        "videos", "links", "resources", "posts", "reels", "clips", "sources",
        "both", "few", "some", "couple", "list", "best ones", "top ",
    ])


def _should_lock_single_resource(
    question: str,
    rec_result: Optional[Dict[str, Any]],
    preferred_platforms: Optional[List[str]] = None,
) -> bool:
    return (
        not _wants_multiple_resources(question)
        and _has_recommendable_resource(rec_result, preferred_platforms=preferred_platforms)
    )


def _should_block_on_web_fallback(
    question: str,
    history: Optional[List[Dict[str, str]]],
    *,
    wants_link: bool,
    is_video_request: bool,
    support_set: List[Dict[str, Any]],
    has_recommendable_ingested_resource: bool,
    has_linkable_ingested_resource: bool,
    search_mode: str,
    images: bool = False,
) -> bool:
    """
    Keep live web search off the critical chat path unless the user explicitly
    needs fresh/public info or a source/link. This preserves quality where the
    web matters, while avoiding multi-second speculative searches for normal chat.
    """
    if search_mode != "hybrid":
        return False

    if images and not wants_link:
        return False

    needs_fresh_info = needs_fresh_public_web_search(question, history)
    if not wants_link and not needs_fresh_info:
        return False

    if is_video_request and (has_recommendable_ingested_resource or has_linkable_ingested_resource):
        return False

    if wants_link and has_linkable_ingested_resource and not needs_fresh_info:
        return False

    return True


def _support_set_top_score(
    support_set: Optional[List[Dict[str, Any]]],
    *,
    max_distance: float = 1.15,
) -> Optional[float]:
    scores: List[float] = []
    for chunk in support_set or []:
        if chunk.get("distance") is not None:
            try:
                distance = float(chunk.get("distance") or 0.0)
                scores.append(max(0.0, min(1.0, 1.0 - (distance / max_distance))))
                continue
            except Exception:
                pass
        for key in ("rerank_score", "score", "confidence"):
            if chunk.get(key) is not None:
                try:
                    scores.append(max(0.0, min(1.0, float(chunk.get(key) or 0.0))))
                    break
                except Exception:
                    continue
    return max(scores) if scores else None


def _decision_is_creator_public_fact(decision: Optional[SearchDecision]) -> bool:
    if not decision:
        return False
    return decision.reason in {
        "creator_own_world",
        "creator_named_fact",
        "factual_query",
        "medium_confidence_factual",
    }


def _should_use_gemini_grounded_search(
    provider: Any,
    question: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    *,
    is_video_request: bool = False,
    intent_metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    if is_video_request:
        return False
    if not callable(getattr(provider, "grounded_overview", None)):
        return False

    intent = str((intent_metadata or {}).get("intent") or "").upper()
    if intent in {"PUBLIC_CREATOR_FACT", "EVENT_PUBLIC_FACTS"}:
        return True
    return needs_fresh_public_web_search(question, conversation_history)


def _run_gemini_grounded_search(
    provider: Any,
    question: str,
    creator_row: Dict[str, Any],
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, Any]]:
    overview = provider.grounded_overview(
        question,
        creator_row,
        conversation_history=conversation_history,
    ) or {}
    results = list(overview.get("results") or [])
    response_text = str(overview.get("response_text") or "").strip()

    if response_text:
        for result in results:
            if not str(result.get("snippet") or "").strip():
                result["snippet"] = response_text[:280]

    logger.info(
        "[SEARCH] Gemini grounded overview returned %s results across %s subqueries",
        len(results),
        len(overview.get("query_plan") or []),
    )
    return results


def _run_live_web_search(
    question: str,
    creator_row: Dict[str, Any],
    conversation_history: Optional[List[Dict[str, str]]] = None,
    preferred_platforms: Optional[List[str]] = None,
    is_video_request: bool = False,
    intent_metadata: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    from backend.services.research_provider import get_research_provider

    rp = get_research_provider()
    web_results: List[Dict[str, Any]] = []

    # --- Primary provider ---
    try:
        if _should_use_gemini_grounded_search(
            rp,
            question,
            conversation_history,
            is_video_request=is_video_request,
            intent_metadata=intent_metadata,
        ):
            web_results = _run_gemini_grounded_search(
                rp,
                question,
                creator_row,
                conversation_history=conversation_history,
            )
        else:
            web_query = build_live_search_query(
                question,
                conversation_history,
                creator_name=creator_row.get("name") or creator_row.get("handle"),
                preferred_platforms=preferred_platforms,
                require_video=is_video_request,
            )
            web_results = rp.search(
                web_query,
                creator_row,
                conversation_history=conversation_history,
                intent_metadata=intent_metadata,
            )
        web_results = _filter_live_web_results(
            web_results,
            question,
            require_video=is_video_request,
        )
    except Exception as primary_exc:
        logger.warning("[SEARCH_TRACE] Primary search provider failed: %s", primary_exc)
        web_results = []

    # --- Fallback provider if primary returned nothing ---
    if not web_results:
        try:
            from backend.services.research_provider import get_fallback_research_provider
            fallback_rp = get_fallback_research_provider()
            if fallback_rp and fallback_rp is not rp:
                creator_name = creator_row.get("name") or creator_row.get("handle") or ""
                fallback_query = f"{creator_name} {question}".strip()
                logger.info("[SEARCH_TRACE] Trying fallback search provider for: %s", fallback_query[:80])
                web_results = fallback_rp.search(
                    fallback_query,
                    creator_row,
                    conversation_history=conversation_history,
                    intent_metadata=intent_metadata,
                )
                web_results = _filter_live_web_results(
                    web_results,
                    question,
                    require_video=is_video_request,
                )
        except Exception as fallback_exc:
            logger.warning("[SEARCH_TRACE] Fallback search provider failed: %s", fallback_exc)

    # --- Dedup & platform filter ---
    seen_resources = _get_suggested_resources(conversation_history)
    if seen_resources:
        deduped_web_results = []
        for result in web_results:
            pseudo_candidate = {
                "title": result.get("title"),
                "url": result.get("url"),
                "platform": result.get("platform"),
            }
            if not _is_recent_duplicate_candidate(pseudo_candidate, seen_resources):
                deduped_web_results.append(result)
        if deduped_web_results:
            web_results = deduped_web_results
    if preferred_platforms:
        lowered = {platform.lower() for platform in preferred_platforms}
        platform_filtered = [
            result
            for result in web_results
            if (result.get("platform") or "").lower() in lowered
        ]
        if platform_filtered:
            web_results = platform_filtered
    return web_results


def _inject_live_web_results(
    support_set: List[Dict[str, Any]],
    web_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged = list(support_set or [])
    if web_results:
        logger.info(f"[SEARCH_TRACE] prompt_injection: INCLUDED count={len(web_results[:6])}")
        for i, result in enumerate(web_results[:6]):
            faux_chunk = _make_live_web_chunk(result, i)
            logger.info(
                f"[SEARCH_TRACE] prompt_injection_item[{i}]: {(faux_chunk.get('title') or '')[:60]} -> {(faux_chunk.get('url') or '')[:80]}"
            )
            merged.insert(i, faux_chunk)
    else:
        logger.info("[SEARCH_TRACE] prompt_injection: SKIPPED count=0")
    return merged


def _inject_entity_graph_support(
    support_set: List[Dict[str, Any]],
    question: str,
    creator_row: Dict[str, Any],
    conversation_history: Optional[List[Dict[str, str]]] = None,
    *,
    evidence_plan: Optional[EvidencePlan] = None,
) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    plan = evidence_plan or _build_evidence_plan(question, creator_row, conversation_history)
    if not plan or plan.query_goal not in {"entity_confirmation", "entity_overview", "availability_lookup", "resource_lookup"}:
        return list(support_set or []), None

    entity = creator_entity_service.resolve_entity(
        plan.resolved_query or question,
        creator_id=creator_row.get("id"),
        creator_profile=creator_row,
        conversation_history=conversation_history,
    )
    if not entity:
        return list(support_set or []), None

    entity_chunks = creator_entity_service.build_entity_support_chunks(
        entity=entity,
        query=plan.resolved_query or question,
        creator_id=creator_row.get("id"),
        creator_profile=creator_row,
        conversation_history=conversation_history,
    )
    if not entity_chunks:
        return list(support_set or []), entity

    logger.info(
        "[EVIDENCE] Injecting %s creator-entity support chunk(s) for '%s' (%s)",
        len(entity_chunks),
        entity.get("name") or plan.entity_subject or "entity",
        plan.query_goal,
    )
    return merge_support_sets(list(support_set or []), entity_chunks, limit=4), entity


def _build_public_fact_fallback(
    question: str,
    creator_name: str,
    style_fingerprint: Optional[Dict[str, Any]] = None,
) -> str:
    lowered = (question or "").lower()
    sfp = style_fingerprint or {}

    # Extract creator-specific vocabulary for flavoring
    lexical = sfp.get("lexical_rules") or {}
    sig_phrases = list(lexical.get("signature_phrases") or [])[:4]
    high_words = list(lexical.get("high_signal_words") or [])[:4]
    mode_matrix = sfp.get("mode_matrix") or {}
    uncertainty_rules = mode_matrix.get("uncertainty") or {}
    boundary_rules = mode_matrix.get("boundary") or {}

    # Build a short voice hint so the template sounds like the creator
    voice_cues: List[str] = []
    if sig_phrases:
        voice_cues.append(f"Use phrases like: {', '.join(sig_phrases[:2])}")
    if uncertainty_rules:
        unc_tone = uncertainty_rules.get("tone") or uncertainty_rules.get("approach")
        if unc_tone:
            voice_cues.append(f"When uncertain, your tone is: {unc_tone}")
    voice_hint = ". ".join(voice_cues) if voice_cues else ""

    is_catalog_question = bool(
        re.search(r"\bhow many\s+(books|courses|programs|podcasts|shows)\b", lowered)
        or re.search(r"\bwhat\s+(books|courses|programs|podcasts|shows)\b", lowered)
        or re.search(r"\bwhich\s+(books|courses|programs|podcasts|shows)\b", lowered)
        or re.search(r"\bhave\s+(?:you|u)\s+(?:written|published|made|created)\b", lowered)
        or re.search(r"\b(?:books|courses|programs|podcasts|shows)\s+(?:have\s+)?(?:you|u)\s+(?:written|published|made|created)\b", lowered)
    )
    is_timeline_question = bool(
        any(token in lowered for token in ["publish", "published", "publication", "release", "released", "launch", "launched", "come out", "what year", "what date", "which month"])
        or re.search(r"\bwhen\s+did\b", lowered)
        or re.search(r"\bhow\s+long\s+(?:ago|have)\b", lowered)
        or re.search(r"\bwhen\s+(?:was|were|did)\s+.+\s*(?:start|begin|first)\b", lowered)
        or (
            any(token in lowered for token in ["write", "wrote", "written"])
            and any(token in lowered for token in ["when", "what year", "what date", "which month"])
        )
    )

    # If we have voice cues, use an LLM micro-call to rewrite the fallback in
    # the creator's voice.  Falls back to templates if no style data present.
    if voice_hint or sig_phrases:
        # Determine the factual category for the prompt
        if is_catalog_question:
            fact_category = "a catalog/list question (how many books, courses, etc.)"
        elif is_timeline_question:
            fact_category = "a timeline/date question (when something happened)"
        elif any(t in lowered for t in ["followers", "subscribers", "members"]):
            fact_category = "a live stats question (follower count, subscribers)"
        elif any(t in lowered for t in ["price", "cost", "pricing", "purchase", "course", "program"]):
            fact_category = "a pricing/availability question"
        else:
            fact_category = "a factual question where the exact answer could be outdated"

        try:
            mini_prompt = (
                f"You are {creator_name}. A fan asked you {fact_category}: \"{question}\"\n"
                f"You don't have the exact current answer. Write 1-2 sentences admitting that honestly "
                f"and directing them to your official website or profiles for the latest info.\n"
                f"VOICE RULES: {voice_hint}\n"
                f"Sound like yourself, not like a search engine. Max 40 words. No URLs. No markdown."
            )
            result = rag.generate_chat_completion(
                messages=[{"role": "user", "content": mini_prompt}],
                model=settings.ROUTER_MODEL,
                temperature=0.5,
                max_tokens=80,
            )
            if result and len(result.strip()) > 10:
                return result.strip().strip('"')
        except Exception as e:
            logger.debug("Persona-flavored fallback failed, using template: %s", e)

    # Template fallback (no style data or LLM failed)
    if is_catalog_question:
        if "book" in lowered:
            return (
                "That's a great question. I don't want to give you an outdated number, so "
                "the best place to see the full current list is my Amazon author page or my official website."
            )
        return (
            "That's a great question. I don't want to give you an outdated list, so "
            "check my official website for the latest lineup."
        )
    if is_timeline_question:
        return (
            "Good question. I want to make sure I give you the exact date rather than guess wrong. "
            "You can find that on my official profiles or website."
        )
    if any(token in lowered for token in ["followers", "subscribers", "members"]):
        return (
            "Those numbers change all the time, so I don't want to give you a stale count. "
            "Check my profiles directly for the live number."
        )
    if any(
        phrase in lowered
        for phrase in [
            "price",
            "cost",
            "pricing",
            "where can i buy",
            "where do i buy",
            "where can i get",
            "where can i find",
            "purchase",
            "course",
            "program",
        ]
    ):
        return (
            "I want to make sure you get the current pricing. "
            "Head to my website or official checkout page for the latest details."
        )
    return (
        "I want to give you the right answer on that rather than guess. "
        "My website or official profiles will have the most accurate info."
    )

def evaluate_context_sufficiency(
    question: str,
    support_set: List[Dict[str, Any]],
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    Uses a fast LLM call to classify if the retrieved RAG chunks are sufficient to answer the question.
    Returns: "SUFFICIENT", "PARTIAL", or "INSUFFICIENT"
    """
    knowledge_text = ""
    for i, c in enumerate(support_set[:5]):
        knowledge_text += f"[{i}] {c.get('content', '')}\n"
    
    if not knowledge_text.strip():
        return "INSUFFICIENT"

    has_live_web_result = any("[LIVE WEB SEARCH RESULT]" in (c.get("content") or "") for c in support_set)
    if needs_fresh_public_web_search(question, history) and not has_live_web_result:
        logger.info("Context Sufficiency: forcing PARTIAL because the question needs fresh public info.")
        return "PARTIAL"

    # Creator timeline / biographical questions need web verification 
    # e.g. "when did you start", "how long ago", "how old are you"
    lowered_q = (question or "").lower()
    _is_creator_timeline = bool(
        re.search(r"\bwhen\s+did\s+(?:you|u|he|she|they)\b", lowered_q)
        or re.search(r"\bhow\s+long\s+(?:ago|have)\b", lowered_q)
        or re.search(r"\bhow\s+old\s+(?:are|is|r)\b", lowered_q)
        or re.search(r"\bwhen\s+(?:did|was|were)\s+.+\s*(?:start|begin|launch|found|creat|born|marr|mov|first)\b", lowered_q)
    )
    if _is_creator_timeline and not has_live_web_result:
        logger.info("Context Sufficiency: forcing PARTIAL because timeline/biographical question needs web verification.")
        return "PARTIAL"

    # Biographical / personal fact questions that the RAG corpus rarely covers
    # well — always verify against the web before trusting stale chunks.
    _is_biographical = bool(
        re.search(r"\b(married|wife|husband|spouse|partner|girlfriend|boyfriend|engaged|divorced)\b", lowered_q)
        or re.search(r"\b(children|kids|son|daughter|baby|family)\b", lowered_q)
        or re.search(r"\b(born|birthday|birth\s?place|hometown|grew up|raised)\b", lowered_q)
        or re.search(r"\b(net worth|salary|income|revenue|earn|making)\b", lowered_q)
        or re.search(r"\bwhere\s+(?:are|is|r)\s+(?:you|u|he|she|they)\s+from\b", lowered_q)
        or re.search(r"\bwho\s+(?:is|are|was)\s+(?:your|his|her|their)\b", lowered_q)
    )
    if _is_biographical and not has_live_web_result:
        logger.info("Context Sufficiency: forcing PARTIAL because biographical question needs web verification.")
        return "PARTIAL"

    # Catalog/count queries ("how many books", "what books have you written") should
    # always verify against the web — RAG chunks may mention only a subset of items.
    lowered_q = (question or "").lower()
    is_catalog_count_query = bool(
        re.search(r"\bhow many\s+(books|courses|programs|podcasts|shows|companies|businesses)\b", lowered_q)
        or re.search(r"\bwhat\s+(books|courses|programs|podcasts|shows)\b", lowered_q)
        or re.search(r"\bwhich\s+(books|courses|programs|podcasts|shows)\b", lowered_q)
        or re.search(r"\bhave\s+(?:you|u)\s+(?:written|published|made|created|authored)\b", lowered_q)
        or re.search(r"\b(?:books|courses|programs|podcasts|shows)\s+(?:have\s+)?(?:you|u)\s+(?:written|published|made|created)\b", lowered_q)
        or re.search(r"\ball\s+(?:your|his|her)\s+books\b", lowered_q)
    )
    if is_catalog_count_query and not has_live_web_result:
        logger.info("Context Sufficiency: forcing PARTIAL because catalog/count query needs web verification.")
        return "PARTIAL"

    prompt = f"""
Evaluate if the following KNOWLEDGE is sufficient to answer the USER QUESTION accurately.

USER QUESTION: "{question}"

KNOWLEDGE:
{knowledge_text}

Classify the sufficiency:
- SUFFICIENT: The knowledge provides a complete, accurate, and current answer.
- PARTIAL: The knowledge has some info but is missing critical updated facts, real-time data (like current prices/news), or specific details asked.
- INSUFFICIENT: The knowledge is irrelevant, outdated, or completely missing the answer.

Respond with JUST the classification in JSON format.
JSON: {{"classification": "SUFFICIENT" | "PARTIAL" | "INSUFFICIENT"}}
"""
    
    try:
        messages = [{"role": "system", "content": "You are a helpful knowledge assessment assistant."}, {"role": "user", "content": prompt}]
        # Use MODEL_CLASSIFICATION as requested (e.g. GPT-4o-mini or GPT-4o)
        response_text = rag.generate_chat_completion(messages, model=settings.MODEL_CLASSIFICATION, json_mode=True)
        
        if not response_text:
            return "PARTIAL"
            
        # Use GeminiResearchProvider's parser for convenience if it's accessible, 
        # or just a simple json.loads here
        import json
        data = json.loads(response_text)
        if data and "classification" in data:
            result = data["classification"].upper()
            if result in ["SUFFICIENT", "PARTIAL", "INSUFFICIENT"]:
                return result
    except Exception as e:
        logger.error(f"Error in context evaluation: {e}")
        
    return "PARTIAL"


def is_follow_up_requesting_links(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> bool:
    """
    True if the user is asking for links to items we just recommended (e.g. "links for both", "those").
    When True, we must provide links only for those same items, not new recommendations.
    """
    t = (question or "").lower().strip()
    if not t:
        return False
    follow_up_patterns = [
        "links for both", "links for those", "links for them", "give me the links for both",
        "give me the links for those", "links for the two", "links for the videos you",
        "links for the ones you", "links for the two you", "can you give me the links",
        "send me those", "send me the links", "link for both", "link for those",
        "the links for both", "the links for those", "links for the ones",
        "links to both", "links to those", "both links", "those links",
    ]
    if not any(p in t for p in follow_up_patterns):
        return False
    # Must have a prior assistant message (user is referring to what we just said)
    if not history:
        return False
    last_assistant = None
    for m in reversed(history):
        if (m.get("role") or "").lower() == "assistant":
            last_assistant = m.get("content") or m.get("text") or ""
            break
    return bool(last_assistant and len(last_assistant.strip()) > 0)


_PLATFORM_DISPLAY_NAMES = {
    "instagram": "Instagram",
    "youtube": "YouTube",
    "linkedin": "LinkedIn",
    "twitter": "X",
    "tiktok": "TikTok",
    "reddit": "Reddit",
    "facebook": "Facebook",
}


def _platform_display_name(key: str) -> str:
    return _PLATFORM_DISPLAY_NAMES.get((key or "").lower()) or (key or "Instagram").strip().title()


def _cta_platform_instruction(enabled_platforms: Optional[List[str]] = None) -> str:
    """
    Instruction for making CTAs (message me COACH / Elite etc.) platform-specific.
    Use the platform of the source when the CTA comes from retrieved content;
    otherwise prefer Instagram when ingested, or the first enabled platform.
    """
    base = "When you mention 'message me X' (e.g. COACH, Elite), always add the platform: e.g. 'message me COACH on Instagram' or 'message me Elite on Instagram'. "
    if not enabled_platforms:
        return base + "Use the platform(s) where the creator's content was ingested; prefer 'on Instagram' when that is one of them. If the CTA appears in a specific retrieved source, use that source's platform (see [Source N - platform] in context)."
    low = [str(p).lower() for p in enabled_platforms]
    if "instagram" in low:
        default = "Instagram"
    else:
        default = _platform_display_name(enabled_platforms[0] or "instagram")
    return (
        base
        + f"If the CTA comes from a specific retrieved source, use that source's platform (see [Source N - platform] in context). Otherwise use only ingested platforms ({', '.join(enabled_platforms)}) and prefer 'on {default}'."
    )


def needs_cta(user_msg: str) -> bool:
    """
    True if the user is asking about coaching, programs, or working together.
    Only mention coaching/group/DM/COACH when this is True.
    """
    t = (user_msg or "").lower()
    triggers = [
        "coaching", "coach", "program", "programme", "work with you", "work together",
        "mentor", "mentorship", "join your", "your group", "your community", "your course",
        "hire you", "book you", "consulting", "offer",
    ]
    return any(x in t for x in triggers)


_INTENT_PATTERNS = [
    ([
        "how old are you", "your age", "when were you born", "where do you live", "where are you from", "where did you grow up",
        "are you married", "do you have a wife", "do you have a husband", "do you have kids", "your family",
        "your background", "your education", "where did you go to school", "your degree", "your story",
        "who are you really", "tell me about yourself", "personal question", "are you religious", "why are you not religious",
        "are you atheist", "are you agnostic", "are you a nihilist", "are you nihilist", "what do you believe",
        "what are your beliefs", "your worldview", "are you pagan", "what religion are you",
        "when did you write your book", "when was your book published", "when did you publish your book"
    ], "personal_bio_question"),
    ([
        "what's your name", "what is your name", "who are you", "what do you do", "your name",
        "what's my name", "what is my name", "do you know my name", "my name"
    ], "identity"),
    (["how are you", "what's up", "hey", "hello", "hi there", "good morning", "good afternoon", "hi", "hey,"], "small_talk"),
    (["start a business", "start business", "starting a business", "want to start", "want to start a business", "i want to start"], "start_business"),
    (["how do i", "how to", "how can i", "steps to", "guide to", "tutorial"], "how_to"),
    (["strategy", "strategies", "framework", "breakdown", "explain ", "deep dive"], "deep_strategy"),
    (["link", "source", "which post", "which video", "show me", "send me", "url", "proof", "best video", "best reel", "best post", "video link", "post link", "whats the video", "that video", "that reel", "that post", "any other videos", "more videos", "other videos", "any more videos", "what else can i watch", "what else to watch", "any other video", "give me the links", "links for", "tools", "recommend", "where do you talk about", "where do you cover", "where in your videos", "where in your content", "have you talked about", "do you have a video on"], "request_sources"),
]


def analyze_user_style(question: str) -> Dict[str, Any]:
    """Analyze user style: tone, length, question type."""
    q = (question or "").lower().strip()
    words = q.split()
    word_count = len(words)
    
    style = {
        "tone": "neutral",
        "length": word_count,
        "length_category": "short", # short (<10), medium (10-40), long (>40)
        "question_type": "none"
    }

    if word_count > 40: style["length_category"] = "long"
    elif word_count > 10: style["length_category"] = "medium"
    
    # Hyped signals
    if "!" in q or any(w in q for w in ["best", "insane", "literally", "excited", "wow", "pumped", "yo "]):
        style["tone"] = "hyped"
    # Serious/Technical signals
    elif any(w in q for w in ["technical", "specifically", "explain in detail", "scientific", "data"]):
        style["tone"] = "serious"
        
    # Question type
    if "?" in q or any(q.startswith(w) for w in ["what", "how", "why", "when", "can you"]):
        if word_count < 8 and "what" in q:
            style["question_type"] = "vague"
        else:
            style["question_type"] = "specific"
            
    return style


def classify_intent(question: str) -> str:
    """Rule-based intent: greeting_only | small_talk | identity | request | followup."""
    q = (question or "").lower().strip()
    if not q:
        return "greeting_only"
    if is_greeting(q):
        return "greeting_only"
    
    # 1. Check explicit patterns
    for patterns, type_label in _INTENT_PATTERNS:
        # Check strict matches first for short phrases
        if any(p == q for p in patterns):
            return type_label
        # Then check substring matches
        if any(p in q for p in patterns):
            if type_label == "small_talk":
                # Distinguish greeting_only ("yo") vs small_talk ("how are you")
                greetings = ["hey", "hello", "hi", "yo", "hi there", "hey there"]
                if q in greetings or len(q.split()) == 1:
                    return "greeting_only"
            return type_label
            
    # 2. Heuristic for low-intent
    words = q.split()
    if len(words) <= 2:
        return "greeting_only"
        
    # 3. Vague request detection
    if len(words) < 5:
        if "?" in q or any(q.startswith(w) for w in ["how", "what", "can", "why", "help"]):
            return "vague_request"
            
    return "request"  # default


def classify_resource_intent(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    creator_profile: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Semantic Intent Router to detect when user needs a resource (video, article, course).
    """
    import backend.rag as rag
    
    # Prepare profile context
    profile_info = "Available platforms: YouTube, Instagram, Website."
    if creator_profile:
        pc = creator_profile.get("platform_configs") or {}
        active_plats = [k for k, v in pc.items() if isinstance(v, dict) and v.get("enabled")]
        if active_plats:
            profile_info = f"Available platforms: {', '.join(active_plats)}."
        if creator_profile.get("has_course"):
            profile_info += " This creator HAS a paid course/modules."

    # Prepare history context
    history_context = ""
    if history:
        history_context = "\nConversation History:\n"
        for m in history[-10:]:
            role = m.get("role", "user").upper()
            content = m.get("content") or m.get("text") or ""
            history_context += f"{role}: {content}\n"

    system_prompt = f"""
You are an Intent Router for a Creator AI. Your goal is to detect when the user is requesting or would benefit from a specific piece of creator content (video, article, or course lesson).

Output ONLY a JSON object:
{{
  "needs_resource": true/false,
  "request_type": "explicit" | "implicit" | "none",
  "intent_type": "recommend_content" | "answer_question" | "how_to" | "opinion",
  "task_axis": "training" | "nutrition" | "mindset" | "business" | "other",
  "explicit_constraints": ["under 15 min", "from creator only", "recent"],
  "implicit_goal": "what the user is really trying to achieve",
  "must_terms": ["exact keyword"],
  "avoid_terms": ["undesired concept"],
  "help_criteria": ["step-by-step", "practical", "science-based", "quick summary"],
  "resource_type": "video" | "article" | "course_lesson" | "any",
  "specificity": "specific" | "recommendation" | "evidence",
  "user_level": "beginner" | "intermediate" | "advanced" | "unknown",
  "learning_phase": "overview" | "execution" | "refinement" | "troubleshooting",
  "query": "a clean 2-3 word search query",
  "topic_depth": "deep technical keywords",
  "reason": "short explanation",
  "confidence": 0.0-1.0
}}

Set request_type="explicit" when the user asks for links, videos, or where to watch.
Set request_type="implicit" when the user asks a technical or how-to question where a video would be helpful but they didn't ask for one.
Set request_type="none" for casual chat, meta questions, emotional support, moral/spiritual opinion questions, or relationship pressure unless the user explicitly asks for a resource.

Set needs_resource=true when the user intent implies:
- Identifying as a beginner or asking for a roadmap (e.g., "I'm new, where do I start?").
- Finding where something exists (e.g., "where did you talk about X?").
- Requesting what to watch/read/do next (learning path/recommendations).
- Requesting proof, source, clip, episode, or lesson.
- Asking a specific technical question that is best answered by a foundational video (e.g., "How does BOS work?").
- Any situation where a 10-minute video explanation from the creator would be 10x more valuable than a text summary.

SMART REASONING:
- Act like an AI Research Agent. Your goal is AUTHENTICITY. 
- Only set needs_resource=true if a specific video provides significantly more depth than a text answer.
- Detect "Value Gaps": If a user asks a technical question like "How to use order blocks?", a text summary is risky; a video is AUTHENTIC.
- Explicit Requests: If the user says "link", "video", "resource", "show me", "send me", set request_type="explicit".
- High-Value Opportunities: If the user describes a struggle or mistake inside the creator's core teaching domain, a resource may help. If the user is asking for counsel, conviction, emotional support, or a moral take, answer directly unless they explicitly ask for a video/link/source.
- AVOID REPEATS: If the conversation history shows similar topics were already addressed with links, be more conservative.
- AUTHENTICITY CHECK: Is this a "core" topic for the creator? If yes, find the MASTERCLASS or most-viewed foundational video.

{profile_info}
"""

    user_prompt = f"User Message: {question}\n{history_context}"

    try:
        response_text = rag.generate_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model=settings.ROUTER_MODEL,
            temperature=0.0,
            json_mode=True
        )
        return json.loads(response_text)
    except Exception as e:
        logger.error(f"Intent Router failed: {e}")
        return {
            "needs_resource": False,
            "request_type": "none",
            "resource_type": "any",
            "specificity": "recommendation",
            "query": question,
            "reason": "error",
            "confidence": 0.0
        }


def get_energy_constraints(energy_bucket: str, intent: str = "how_to") -> Dict[str, Any]:
    """Return hard constraints based on energy bucket (LOW, MID, HIGH)."""
    # Defaults
    constraints = {
        "max_words": 150,
        "emoji_rate": "low",
        "punctuation_intensity": "medium",
        "punchiness": "balanced"
    }
    
    if energy_bucket == "LOW":
        constraints["max_words"] = 60 if intent in ["greeting_only", "small_talk"] else 140
        constraints["min_words"] = 40 if intent in ["greeting_only", "small_talk"] else 80
        constraints["emoji_rate"] = "none/rare"
        constraints["punctuation_intensity"] = "low"
        constraints["punchiness"] = "low (calm, gentle)"
    elif energy_bucket == "MID":
        constraints["max_words"] = 70 if intent in ["greeting_only", "small_talk"] else 220
        constraints["min_words"] = 50 if intent in ["greeting_only", "small_talk"] else 100
        constraints["emoji_rate"] = "low"
        constraints["punctuation_intensity"] = "medium"
        constraints["punchiness"] = "balanced"
    elif energy_bucket == "HIGH":
        # High energy = short but fast and punchy
        constraints["max_words"] = 50 if intent in ["greeting_only", "small_talk"] else 180
        constraints["min_words"] = 30 if intent in ["greeting_only", "small_talk"] else 80
        constraints["emoji_rate"] = "low-medium"
        constraints["punctuation_intensity"] = "high"
        constraints["punchiness"] = "high (direct, confident, rhetorical)"

    return constraints


def response_length_instruction(
    intent: str, 
    mode: str = "ANSWER_NOW", 
    energy_bucket: str = "MID", 
    tone_mirror_limit: int = 0,
    user_priority_constraints: Optional[Dict[str, Any]] = None,
    resource_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Instruction for model response length based on intent, policy mode, energy, and USER PRIORITY."""
    # Compute energy constraints
    constraints = get_energy_constraints(energy_bucket, intent)
    budget = constraints["max_words"]
    if tone_mirror_limit > 0:
        budget = min(budget, tone_mirror_limit)
        
    if mode == "ASK_ONE_QUESTION":
        budget = 80 if energy_bucket == "HIGH" else 100

    upc = user_priority_constraints or {}
    max_sent = upc.get("max_sentences", 6)
    complexity = upc.get("complexity", "moderate")

    base_dm_rule = f"""
    DM STYLE RULES:
    - Reveal Budget: Max {budget} words.
    - Max Sentences: {max_sent}.
    - Complexity: {complexity}.
    - Jargon: {'Allowed but clear' if upc.get('jargon_allowed', True) else 'STRICTLY FORBIDDEN'}.
    - NO headings (###), NO bold intros, NO 'Hope this helps'.
    - Keep paragraphs short (1-2 sentences).
    - Use a human, 1-to-1 conversational tone.
    - Punchiness: {constraints['punchiness']}
    - Punctuation Intensity: {constraints['punctuation_intensity']}
    - Emoji usage: {constraints['emoji_rate']}
    """

    if mode == "ASK_ONE_QUESTION":
        return base_dm_rule + f" GOAL: Ask exactly ONE short, high-signal question. Max {budget} words. DO NOT explain why you are asking."

    if intent in ["greeting", "greeting_only"]:
        return base_dm_rule + f"""
        GOAL: Just greet them back in character. 
        - Max {min(3, max_sent)} sentences total.
        - Max {budget} words.
        - DO NOT give advice yet.
        - BANNED PHRASES: "I don't have enough information", "To better assist you", "Based on what you said".
        - STRUCTURE: Warm social acknowledgment -> one broad open question (optional).
        - NEVER describe your own style out loud.
        - NEVER ask a hyper-specific business or diagnostic question.
        """

    if intent == "identity":
        return base_dm_rule + " Respond naturally in 1–2 sentences. Then ask a question in the creator's style to learn about the USER."
    if intent == "small_talk":
        return base_dm_rule + f" Greet them briefly. Ask a unique question to open the floor. Max {budget} words."
    if intent == "start_business" or intent == "start_goal":
        return base_dm_rule + " Give a high-level response + one key piece of mindset advice. Use the 'Reveal Budget' strictly."
    if intent == "how_to":
        return base_dm_rule + " Provide actionable steps. Be thorough but concise. No lists longer than 4 points."
    if intent == "deep_strategy":
        return base_dm_rule + f" Provide a detailed, structured strategy session. Even if deep, stay under {min(budget + 50, 250)} words."
    if intent == "introduce_content":
        primary_label = str((resource_context or {}).get("primary_label") or "resource")
        if resource_context and resource_context.get("video_requested") and not resource_context.get("primary_is_video"):
            if resource_context.get("closest_video_title"):
                return base_dm_rule + (
                    f" Answer the core question briefly. Be explicit that you did not find an exact video for this. "
                    f"Recommend the {primary_label} as the best direct match, then offer the closest video as a secondary option and explain why it is the best watchable fit. "
                    f"Max {budget} words."
                )
            return base_dm_rule + (
                f" Answer the core question briefly. Be explicit that you did not find an exact video for this. "
                f"Recommend the {primary_label} as the best direct match. If they still want something to watch, invite them to ask for the closest video. "
                f"Max {budget} words."
            )
        if resource_context and not resource_context.get("primary_is_video"):
            return base_dm_rule + f" Answer core question briefly, then introduce the {primary_label} as the essential next step. Max {budget} words."
        return base_dm_rule + f" Answer core question briefly, then introduce the video as the essential next step. Max {budget} words."
    
    return base_dm_rule + f" Match the creator's natural DM style. Max {budget} words."


# Match URLs for stripping or collecting
_URL_RE = re.compile(
    r"https?://[^\s\]>\)\"']+",
    re.IGNORECASE,
)


def strip_urls_from_text(text: str) -> str:
    """Remove URLs from text. Used when needs_links is False."""
    if not text:
        return text
    out = _URL_RE.sub("", text)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _filter_sources_by_platform(
    sources: List[Dict[str, Any]],
    enabled_platforms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Keep only sources whose platform (URL-derived) is in enabled_platforms when set."""
    if not enabled_platforms:
        return sources
    allowed = { str(p).lower() for p in enabled_platforms }
    out = []
    for s in sources:
        ref = s.get("source_ref") if isinstance(s.get("source_ref"), dict) else {}
        plat = (ref.get("platform") or "").lower()
        if plat in allowed:
            out.append(s)
    return out


def _urls_in_text(text: str) -> Set[str]:
    """Extract URLs present in text (for deduping when appending Sources)."""
    if not text:
        return set()
    return set(_URL_RE.findall(text))


def sources_section(
    sources: List[Dict[str, Any]],
    max_links: int = 3,
    enabled_platforms: Optional[List[str]] = None,
    exclude_urls: Optional[Set[str]] = None,
) -> str:
    """Build 'Sources:' section with max_links URLs, deduped by content_id. Skip URLs in exclude_urls (e.g. already inline)."""
    sources = _filter_sources_by_platform(sources, enabled_platforms)
    exclude = exclude_urls or set()
    seen: Set[str] = set()
    out: List[str] = []
    for s in sources:
        ref = s.get("source_ref") if isinstance(s.get("source_ref"), dict) else {}
        cid = ref.get("content_id") or ref.get("canonical_url") or ""
        url = ref.get("canonical_url") or ""
        if not url or cid in seen or url in exclude:
            continue
        seen.add(cid)
        title = ref.get("title") or ""
        platform = ref.get("platform") or ""
        part = f"{platform}: {url}" + (f" ({title})" if title else "")
        out.append(part)
        if len(out) >= max_links:
            break
    if not out:
        return ""
    return "Sources:\n" + "\n".join(out)


def recency_boost(published_at: Optional[str], days_old_threshold: int = 90) -> float:
    """Calculate recency boost: newer content gets slight boost."""
    if not published_at:
        return 0.5  # Neutral if no date
    
    try:
        if isinstance(published_at, str):
            if published_at.endswith("Z"):
                published_at = published_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(published_at)
        else:
            dt = published_at
        
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        days_old = (datetime.now(timezone.utc) - dt).days
        if days_old < 0:
            return 1.0  # Future dates (shouldn't happen)
        if days_old <= 30:
            return 1.0  # Very recent
        if days_old <= days_old_threshold:
            return 0.8  # Recent
        return 0.6  # Older
    except Exception:
        return 0.5


def source_quality_score(content_type: str) -> float:
    """Calculate source quality score based on content type."""
    return SOURCE_QUALITY_MAP.get(content_type.lower(), 0.7)


def _aggregate_document_evidence(chunks: List[Dict[str, Any]], sim_threshold: float = 0.55) -> List[Dict[str, Any]]:
    """
    Stage 2: Evidence-first chunk scoring.
    Groups chunks by document and computes:
    - max_chunk_sim
    - mean_top3_chunk_sim
    - evidence_density (count of chunks > threshold)
    """
    docs = {}
    for c in chunks:
        doc_id = c.get("document_id")
        if not doc_id: continue
        
        if doc_id not in docs:
            docs[doc_id] = {
                "id": doc_id,
                "title": c["source_ref"].get("title"),
                "url": c["source_ref"].get("canonical_url"),
                "thumbnail": c["source_ref"].get("thumbnail"), # assuming thumbnail might be there
                "platform": c["source_ref"].get("platform"),
                "chunks": [],
                "retrieval_signals": {
                    "query_variants": set(),
                    "retrieval_modes": set(),
                    "rrf_total": 0.0,
                    "sparse_hits": 0,
                    "dense_hits": 0,
                },
            }
        
        # Distance to Similarity conversion for metrics
        lexical_score = float(c.get("lexical_score") or 0.0)
        sim = max(0.0, 1.0 - (float(c.get("distance") or 1.15) / 1.15))
        if lexical_score > 0.0:
            sim = max(sim, min(0.95, 0.35 + min(lexical_score, 12.0) * 0.05))
        c["sim"] = sim
        docs[doc_id]["chunks"].append(c)
        retrieval_signals = docs[doc_id]["retrieval_signals"]
        query_variant = str(c.get("query_variant") or "").strip().lower()
        if query_variant:
            retrieval_signals["query_variants"].add(query_variant)
        retrieval_mode = str(c.get("retrieval_mode") or "").strip().lower()
        if retrieval_mode:
            retrieval_signals["retrieval_modes"].add(retrieval_mode)
            if retrieval_mode == "sparse":
                retrieval_signals["sparse_hits"] += 1
            elif retrieval_mode == "dense":
                retrieval_signals["dense_hits"] += 1
        retrieval_signals["rrf_total"] += float(c.get("retrieval_rrf") or 0.0)
        
    aggregated = []
    for d_id, d in docs.items():
        sorted_chunks = sorted(
            d["chunks"],
            key=lambda x: (
                float(x.get("sim") or 0.0),
                float(x.get("lexical_score") or 0.0),
                float(x.get("retrieval_rrf") or 0.0),
            ),
            reverse=True,
        )
        top5 = sorted_chunks[:5]
        
        max_sim = top5[0]["sim"] if top5 else 0.0
        mean_top3 = sum(c["sim"] for c in top5[:3]) / min(len(top5), 3) if top5 else 0.0
        density = len([c for c in sorted_chunks if c["sim"] >= sim_threshold])
        retrieval_signals = d["retrieval_signals"]
        query_coverage = len(retrieval_signals["query_variants"])
        
        d["max_chunk_sim"] = max_sim
        d["mean_top3_chunk_sim"] = mean_top3
        d["evidence_density"] = density
        d["evidence_metrics"] = {
            "max_sim": max_sim,
            "mean_top3": mean_top3,
            "density": density,
            "query_coverage": query_coverage,
            "rrf_total": retrieval_signals["rrf_total"],
            "sparse_hits": retrieval_signals["sparse_hits"],
            "dense_hits": retrieval_signals["dense_hits"],
        }
        representative_chunks = []
        for chunk in top5[:2]:
            snippet = re.sub(r"\s+", " ", str(chunk.get("content") or "").strip())
            if snippet:
                representative_chunks.append(snippet[:240])
        d["content"] = " ".join(representative_chunks) if representative_chunks else ""
        d["snippet"] = representative_chunks[0] if representative_chunks else ""
        d["chunk_id"] = top5[0]["chunk_id"] if top5 else ""
        d["distance"] = top5[0]["distance"] if top5 else 1.15
        d["source_ref"] = {
            "title": d["title"],
            "canonical_url": d["url"],
            "platform": d["platform"],
            "content_type": top5[0]["source_ref"].get("content_type") if top5 else "unknown"
        }
        d["retrieval_signals"] = {
            "query_coverage": query_coverage,
            "retrieval_modes": sorted(retrieval_signals["retrieval_modes"]),
            "rrf_total": retrieval_signals["rrf_total"],
            "sparse_hits": retrieval_signals["sparse_hits"],
            "dense_hits": retrieval_signals["dense_hits"],
        }
        
        aggregated.append(d)
        
    return aggregated


def query_term_overlap(query: str, text: str) -> float:
    """Calculate overlap between query terms and chunk text."""
    query_words = set(re.findall(r'\b[a-z]{3,}\b', query.lower()))
    text_words = set(re.findall(r'\b[a-z]{3,}\b', text.lower()))
    
    if not query_words:
        return 0.0
    
    overlap = len(query_words & text_words) / len(query_words)
    return min(overlap, 1.0)


def rerank_candidates(
    candidates: List[Dict[str, Any]],
    query: str,
    intent_plan: Dict[str, Any],
    preferred_platforms: Optional[List[str]] = None,
    creator_id: Optional[int] = None,
    context_features: Optional[Dict[str, Any]] = None,
    k_final: int = K_FINAL
) -> List[Dict[str, Any]]:
    """
    Step 3: Re-rank tightly using composite score.
    Enhanced with Stage 2 metrics and Stage 3 compatibility.
    """
    scored = []
    user_goal = intent_plan.get("intent_type", "how_to")
    preferred = {platform.lower() for platform in (preferred_platforms or []) if platform}
    context_features = context_features or {}
    ranker_mode = "default"
    specificity = str(intent_plan.get("specificity") or "").lower()
    if specificity == "specific" or context_features.get("needs_exact_match"):
        ranker_mode = "exact_lookup"
    elif context_features.get("wants_proof") or str(intent_plan.get("specificity") or "").lower() == "evidence":
        ranker_mode = "proof_source"
    elif context_features.get("wants_video") and not context_features.get("needs_exact_match"):
        ranker_mode = "best_video"
    elif context_features.get("wants_post") and not context_features.get("needs_exact_match"):
        ranker_mode = "best_post"
    elif context_features.get("wants_beginner"):
        ranker_mode = "beginner_start"
    weight_map = {
        "default": {"similarity": 0.28, "density": 0.08, "recency": 0.08, "quality": 0.07, "overlap": 0.06, "compat": 0.08, "platform": 0.06, "title": 0.1, "asset_fit": 0.13, "query_cov": 0.07, "rrf": 0.09},
        "exact_lookup": {"similarity": 0.18, "density": 0.06, "recency": 0.04, "quality": 0.05, "overlap": 0.1, "compat": 0.08, "platform": 0.04, "title": 0.15, "asset_fit": 0.12, "query_cov": 0.08, "rrf": 0.1},
        "proof_source": {"similarity": 0.18, "density": 0.06, "recency": 0.06, "quality": 0.08, "overlap": 0.08, "compat": 0.08, "platform": 0.05, "title": 0.08, "asset_fit": 0.14, "query_cov": 0.08, "rrf": 0.11},
        "best_video": {"similarity": 0.24, "density": 0.08, "recency": 0.08, "quality": 0.07, "overlap": 0.06, "compat": 0.1, "platform": 0.08, "title": 0.08, "asset_fit": 0.13, "query_cov": 0.07, "rrf": 0.11},
        "best_post": {"similarity": 0.22, "density": 0.07, "recency": 0.08, "quality": 0.06, "overlap": 0.06, "compat": 0.1, "platform": 0.08, "title": 0.09, "asset_fit": 0.14, "query_cov": 0.08, "rrf": 0.12},
        "beginner_start": {"similarity": 0.2, "density": 0.06, "recency": 0.06, "quality": 0.06, "overlap": 0.05, "compat": 0.08, "platform": 0.06, "title": 0.08, "asset_fit": 0.2, "query_cov": 0.07, "rrf": 0.08},
    }
    weights = weight_map[ranker_mode]
    
    for cand in candidates:
        # 1. Base Evidence Score (from Stage 2 Metrics)
        # Using a blend of peak similarity and density
        metrics = cand.get("evidence_metrics", {})
        similarity = metrics.get("max_sim", 0.0)
        density_bonus = min(0.15, metrics.get("density", 0) * 0.02)
        
        # 2. Recency boost
        recency = recency_boost(cand["source_ref"].get("published_at"))
        
        # 3. Source quality
        quality = source_quality_score(cand["source_ref"].get("content_type", ""))
        
        # 4. Term overlap
        overlap = query_term_overlap(query, cand["content"])
        
        # 5. Stage 3: Compatibility Score
        # Simple rule-based compatibility for now
        comp_boost = 0.0
        c_type = cand["source_ref"].get("content_type", "").lower()
        if user_goal == "how_to" and ("tutorial" in c_type or "guide" in c_type):
            comp_boost = 0.15
        elif user_goal == "recommend_content" and "video" in c_type:
            comp_boost = 0.1

        platform_boost = 0.12 if preferred and _candidate_platform(cand) in preferred else 0.0
        title_quality = _resource_title_quality(_candidate_title(cand), _candidate_url(cand))
        cand["title_quality"] = title_quality
        if creator_id:
            cand["asset_profile"] = recommendation_asset_service.get_profile(creator_id, cand)
        asset_fit = recommendation_asset_service.score_fit(
            cand.get("asset_profile") or {},
            query,
            resource_intent=intent_plan,
            context_features=context_features,
        )
        retrieval_signals = cand.get("retrieval_signals") or {}
        query_cov = min(1.0, float(retrieval_signals.get("query_coverage") or 0.0) / 3.0)
        rrf_total = min(1.0, float(retrieval_signals.get("rrf_total") or 0.0) * 12.0)
            
        # Composite score
        score = (
            weights["similarity"] * similarity +
            weights["density"] * density_bonus +
            weights["recency"] * recency +
            weights["quality"] * quality +
            weights["overlap"] * overlap +
            weights["compat"] * comp_boost +
            weights["platform"] * platform_boost +
            weights["title"] * title_quality +
            weights["asset_fit"] * asset_fit +
            weights["query_cov"] * query_cov +
            weights["rrf"] * rrf_total
        )
        
        cand["rerank_score"] = score
        cand["asset_fit_score"] = asset_fit
        cand["ranker_mode"] = ranker_mode
        scored.append(cand)
    
    scored.sort(key=lambda x: x["rerank_score"], reverse=True)
    return scored[:k_final]


def build_answer_contract(
    support_set: List[Dict[str, Any]],
    question: str
) -> Dict[str, Any]:
    """
    Step 4: Build an Answer Contract with facts[] and evidence mapping.
    """
    facts = []
    gaps = []
    
    # Group chunks by document/source for deduplication
    sources_by_id = {}
    for chunk in support_set:
        source_ref = chunk["source_ref"]
        source_id = source_ref.get("content_id") or source_ref.get("canonical_url", "")
        if source_id not in sources_by_id:
            sources_by_id[source_id] = {
                "source_ref": source_ref,
                "chunks": [],
            }
        sources_by_id[source_id]["chunks"].append(chunk["chunk_id"])
    
    # Extract key facts from chunks (simplified - in production, use LLM to extract)
    # For now, we'll mark all chunks as supporting facts
    fact_id = 1
    for source_id, source_data in sources_by_id.items():
        chunk_ids = source_data["chunks"]
        if len(chunk_ids) >= MIN_SUPPORT:
            facts.append({
                "id": f"F{fact_id}",
                "text": f"Content from {source_data['source_ref'].get('platform', 'unknown')}",
                "support": chunk_ids[:MIN_SUPPORT],  # Use first MIN_SUPPORT chunks
                "source_ref": source_data["source_ref"],
            })
            fact_id += 1
    
    # Identify gaps (areas not well supported)
    if len(support_set) < MIN_SUPPORT:
        gaps.append("Limited supporting content - may need to provide general advice")
    
    return {
        "facts": facts,
        "gaps": gaps,
        "sources": list(sources_by_id.values()),
        "total_chunks": len(support_set),
    }


def generate_meaning_draft(
    question: str,
    context: str,
    verified_facts: str,
    intent: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    target_title: Optional[str] = None,
    backup_titles: Optional[List[str]] = None,
    mode: str = "ANSWER_NOW",
    memory_context: str = "",
    user_state: Optional[Dict[str, Any]] = None,
    steering_guidance: str = "",
    steering_move: str = "",
    support_set: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Step 3: Generate a Neutral Meaning Draft (Content Plan).
    This step focuses purely on WHAT to say, ignoring HOW to say it.
    """
    
    intent_guidance = ""
    resource_context = _resource_prompt_context(support_set, question)
    if mode == "ASK_ONE_QUESTION":
        intent_guidance = """
        IMPORTANT: Your goal is NOT to answer fully. 
        1. Give ONE tiny quick win or high-level value line (max 1 sentence).
        2. Identify the single most important piece of missing information (a slot/gap).
        3. PLAN to ask for that missing information in a natural way.
        DO NOT provide a full list of steps.
        """
    elif intent == "introduce_content":
        # Note: Specific title enforcement is now handled via explicit instructions in the prompt.
        # We rely on Source 1 being the 'Currently Recommended' video as defined in the context.
        if resource_context.get("video_requested") and not resource_context.get("primary_is_video"):
            primary_label = resource_context.get("primary_label") or "resource"
            if resource_context.get("closest_video_title"):
                intent_guidance = f"""
        IMPORTANT: The best direct match in Source 1 is a {primary_label}, not a video.
        Your goal is to PLAN a mentorship response that:
        1. Answers the core question briefly from the Source 1 snippet.
        2. Says you did not find an exact video for this.
        3. Recommends the {primary_label} in Source 1 by its FULL TITLE as the best direct match.
        4. Mentions the closest video alternative, "{resource_context.get("closest_video_title")}", and gives one short reason it is the best watchable fit.

        CRITICAL: Do NOT call Source 1 a video and do NOT tell the user to watch it.
        Set uncertainty_handling = 'exact_required'.
        """
            else:
                intent_guidance = f"""
        IMPORTANT: The best direct match in Source 1 is a {primary_label}, not a video.
        Your goal is to PLAN a mentorship response that:
        1. Answers the core question briefly from the Source 1 snippet.
        2. Says you did not find an exact video for this.
        3. Recommends the {primary_label} in Source 1 by its FULL TITLE as the best direct match.

        CRITICAL: Do NOT call Source 1 a video and do NOT tell the user to watch it.
        Set uncertainty_handling = 'exact_required'.
        """
        elif not resource_context.get("primary_is_video"):
            primary_label = resource_context.get("primary_label") or "resource"
            intent_guidance = f"""
        IMPORTANT: A high-confidence creator resource HAS been found and is provided in Source 1.
        Your goal is to PLAN a mentorship response that:
        1. Gives a specific piece of advice or technical answer based on Source 1's snippet.
        2. Explicitly RECOMMENDS the {primary_label} in Source 1 by its FULL TITLE.

        CRITICAL: Treat Source 1 as a {primary_label}, not a video.
        Use language that fits the medium.
        Set uncertainty_handling = 'exact_required'.
        """
        else:
            intent_guidance = """
        IMPORTANT: A high-confidence video/resource HAS been found and is provided in Source 1. 
        Your goal is to PLAN a mentorship response that:
        1. Gives a specific piece of advice or technical answer based on Source 1's snippet.
        2. Explicitly RECOMMENDS the resource in Source 1 by its FULL TITLE.
        
        CRITICAL: Ignore recommendations from previous conversation history. 
        You MUST use the title from Source 1. 
        Source 1 is the ONLY 'Currently Recommended' video for THIS response.
        
        DO NOT be generic. Use the specific title provided in Source 1.
        Set uncertainty_handling = 'exact_required'.
        """
        if backup_titles:
            intent_guidance += f"\n\nBACKUP OPPORTUNITY: You have {len(backup_titles)} other relevant resources: {', '.join(backup_titles)}. Include them in 'backup_resources' with brief reasons why they might be good alternates (e.g. 'shorter', 'more technical', 'for beginners')."
    elif intent == "introduce_fallback":
         intent_guidance = """
         IMPORTANT: No exact video was found, but a channel search card is provided in Source 1.
         Your goal is to PLAN a response that:
         1. Acknowledges you don't have a specific video for the exact query.
         2. Suggests the user search your channel for the specific topic mentioned in Source 1 (the 'Title' of Source 1).
         
         CRITICAL: If you previously recommended a specific video, acknowledge this is a request for a NEW or DIFFERENT one.
         """
    elif intent == "request_sources":
        intent_guidance = """
        IMPORTANT: The user is asking for links or proof. Plan to provide only the best 1-2 sources from the context, and briefly explain why each one helps with the exact question.
        """
    elif intent in ["greeting", "greeting_only"]:
        intent_guidance = "Plan a short, authentic creator greeting. Do NOT provide advice. Focus on being welcoming and open. IGNORE any retrieved content as it is not relevant to a simple greeting."
    elif intent == "small_talk":
        intent_guidance = "Plan a friendly, brief greeting. Do NOT use retrieved context information."

    system_prompt = f"""
You are a Neutral Content Planner (User Outcome Engine). 
Your goal is to figure out EXACTLY what the user needs and pick the single best next step.
Do NOT write the final answer. Do NOT use any persona.

OFPO STEP 0 - USER SIGNAL LOCK:
- Goal Guess: {user_state.get('goal_guess', 'extract from last message') if user_state else 'unknown'}
- User Stage: {user_state.get('user_stage', 'unknown') if user_state else 'unknown'}
- Missing Info: {user_state.get('missing_info', []) if user_state else []}

OFPO STEP 1 - NEXT ACTION SELECTION:
Pick exactly ONE action from this menu based on the user's need:
- Clarify (ask 1 laser question if info is missing)
- Plan (give 3-5 steps, no extra)
- Execute (write the thing / produce the output)
- Diagnose (find what's broken + fix path)
- Compare (pick between options)
- Coach (mindset/behavior change with 1 exercise)
- Entertain (only if user explicitly asked or is idle-chatting)

OFPO STEP 4 - HELPING STYLE MAP:
(SKIP THIS IF IT IS A GREETING)
Adapt the help format based on the creator category:
- Business/Money → Identify constraint → Smallest revenue action → Numbers/Offer.
- Fitness/Health → Metric/Goal → One actionable exercise/habit → Next check-in.
- Relationship/Life → Emotion → Belief reframe → Reflection question.
- Comedian/Entertainer → Quick joke/setup (plan this) → Real helpful move → Playful question.
- Trader/Technical → Risk first → Scenarios → Specific rule check.

{intent_guidance}

OFPO STEP 5 - USER-CENTEREDNESS CHECK:
1. Did I reference what the user actually said? (If they only said hello, ONLY greet them back).
2. Did I give exactly one next step? (For greetings, the step is asking what's on their mind).
3. If info is missing, did I stop at the question?
4. Is the plan concise and outcome-first?

Output a JSON object with the following structure:
{{
    "goal_guess": "1 sentence summary",
    "user_stage": "exploring|deciding|executing|stuck",
    "next_action": "Clarify|Plan|Execute|Diagnose|Compare|Coach|Entertain",
    "missing_info_request": "Specific question if info is missing, else null",
    "help_format": "business|fitness|relationship|comedian|technical",
    "target_resource_title": "THE EXACT TITLE FROM CURRENT CONTEXT",
    "answer_points": ["Point 1", "Point 2"],
    "concrete_action_step": "One specific step the user can take today",
    "uncertainty_handling": "exact_required" | "admit_unknown",
    "tone_guidance": "neutral"
}}
"""
    if target_title:
        system_prompt += f"\nCRITICAL: The current video we are recommending is LITERALLY titled: '{target_title}'. REJECT any other titles mentioned earlier in the chat history."
    history_text = ""
    if conversation_history:
        history_text = "\nRecent History:\n" + "\n".join([f"{m.get('role', 'unknown')}: {m.get('content', '')}" for m in conversation_history[-3:]])

    anti_regurgitation_block = ""
    if intent not in ["greeting", "greeting_only", "small_talk"] and support_set:
        anti_regurgitation_block = build_anti_regurgitation_block(question, support_set)

    user_prompt = f"""
{anti_regurgitation_block}

Context:
{context if intent not in ["greeting", "greeting_only", "small_talk"] else "None (Greeting/Small talk mode - do not use RAG context)"}

Verified Facts:
{verified_facts if intent not in ["greeting", "greeting_only", "small_talk"] else "None"}

{memory_context if intent not in ["greeting", "greeting_only", "small_talk"] else ""}

Question: {question}
{history_text}

Draft the content plan.
"""

    response = rag.generate_chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        model=settings.MODEL_SYNTHESIS,
        temperature=0.0, # Strict facts
        json_mode=True
    )
    
    try:
        # Simple heuristic to extract JSON if specific block not returned
        json_str = response
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0]
        elif "{" in response:
            start = response.find("{")
            end = response.rfind("}") + 1
            json_str = response[start:end]
            
        return json.loads(json_str)
    except Exception as e:
        logger.error(f"Failed to parse Meaning Draft JSON: {e}")
        return {
            "goal_guess": "Unknown",
            "user_stage": "exploring",
            "next_action": "Explain",
            "missing_info_request": None,
            "help_format": "business",
            "answer_points": ["Could not parse plan"], 
            "concrete_action_step": "Try asking a more specific question.",
            "uncertainty_handling": "admit_unknown",
            "tone_guidance": "neutral"
        }



def generate_grounded_answer(
    question: str,
    support_set: List[Dict[str, Any]],
    answer_contract: Dict[str, Any],
    persona: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    intent: str = "how_to",
    include_links_in_output: bool = False,
    allow_cta: bool = False,
    enabled_platforms: Optional[List[str]] = None,
    follow_up_requesting_links: bool = False,
    user_preferences: Optional[Dict[str, Any]] = None,
    user_name: Optional[str] = None,
    creator_name: Optional[str] = None,
    style_fingerprint: Optional[Dict[str, Any]] = None,
    images: Optional[List[Dict[str, Any]]] = None,
    creator_id: Optional[int] = None,
    target_title: Optional[str] = None,
    backup_titles: Optional[List[str]] = None,
    mode: str = "ANSWER_NOW",
    voice_profile: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = 1,
    decision_policy: Optional[Dict[str, Any]] = None,
    memory_guidance: str = "",
    current_memory: Optional[Dict[str, Any]] = None,
    steering_guidance: str = "",
    mvc_score: int = 0,
    creator_profile: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
    user_state: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    SDD-CVR Implementation:
    1. Meaning Draft (Neutral)
    2. Voice Render (Creator Persona + Style DNA)
    3. Verification & Repair Loop
    """
    from backend.services.decision_service import decision_service
    
    # Resolve decision policy
    policy = decision_policy or decision_service.DEFAULT_POLICY
    q_type, topic, sufficiency = decision_service.classify_question(question, intent)
    move = decision_service.choose_move(policy, q_type, topic, intent=intent, sufficiency=sufficiency)
    
    # --- User Priority & Real Conversation Engine ---
    # Use passed state if available, else detect
    if not user_state:
        user_state = user_priority_service.detect_user_state(question, conversation_history, current_memory=current_memory)
    
    conv_mode = user_priority_service.select_response_mode(user_state, q_type, mvc_score)
    mode_constraints = user_priority_service.get_mode_constraints(conv_mode, user_state)
    
    logger.info(f"User State: {user_state}")
    logger.info(f"Conversation Mode: {conv_mode}")
    logger.info(f"Decision Move: {move}")

    # --- Conversation Steering Layer ---
    if not steering_guidance:
        steering_result = steering_service.determine_steering_move(user_state, current_memory or {}, question)
        steering_move = steering_result["steering_move"]
        steering_guidance = steering_result["steering_guidance"]
    else:
        # If passed, we still might want steering_move for the prompt
        # But we'll trust the caller for most things
        steering_move = "GUIDED_RESPONSE" 
    
    # Update progress stage and topic tracking in memory
    if current_memory and not steering_guidance:
        current_memory["progress_stage"] = steering_result["new_stage"]
        current_memory["current_topic"] = steering_result["detected_topic"]
        current_memory["topic_depth_level"] = steering_result["topic_depth"]
        logger.info(f"Steering: Move={steering_move}, Topic='{current_memory['current_topic']}', Depth={current_memory['topic_depth_level']}")

    logger.info(f"Steering Move: {steering_move}")
    
    # --- Dependencies ---
    distiller = StyleDistiller()
    scorer = StyleScorer(style_fingerprint)
    
    # --- Context Construction ---
    # Build context from support set with rich source provenance
    context_parts = []
    for i, chunk in enumerate(support_set):
        source = chunk.get("source_ref") or {}
        platform = source.get("platform", "unknown")
        title = source.get("title", "")
        content_type = source.get("content_type", "")
        published_at = source.get("published_at", "")
        canonical_url = source.get("canonical_url", "")

        # Add origin tag to help model distinguish between creator voice and public info
        origin = "Ingested"
        if chunk.get("is_research"):
            origin = "Creator-Verified Research"
        elif chunk.get("is_public_info"):
            origin = "Public Info (General Knowledge)"

        # ── Build rich provenance header ──
        header_parts = [f"Source {i+1}", platform, origin]
        if title:
            header_parts.append(f'Title: "{title}"')
        if content_type and content_type not in ("unknown",):
            header_parts.append(f"Type: {content_type}")
        if published_at:
            try:
                dt = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
                header_parts.append(f"Content uploaded: {dt.strftime('%b %d, %Y')}")
            except Exception:
                pass

        # ── Video timestamp context (if available) ──
        start_sec = source.get("start_time_sec")
        end_sec = source.get("end_time_sec")
        if start_sec is not None:
            def _fmt_ts(secs):
                m, s = divmod(int(secs), 60)
                h, m = divmod(m, 60)
                return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
            ts_str = f"Video timestamp: ~{_fmt_ts(start_sec)}"
            if end_sec is not None and end_sec > start_sec:
                ts_str += f" – {_fmt_ts(end_sec)}"
            header_parts.append(ts_str)
        elif chunk.get("chunk_index") is not None and content_type in ("video", "short"):
            # Rough positional estimate for chunks without exact timestamps
            total_chunks = chunk.get("total_chunks")
            idx = chunk.get("chunk_index", 0)
            if total_chunks and total_chunks > 2:
                ratio = idx / (total_chunks - 1)
                if ratio < 0.25:
                    header_parts.append("Position: early in the video")
                elif ratio < 0.55:
                    header_parts.append("Position: around the middle of the video")
                else:
                    header_parts.append("Position: towards the end of the video")

        header = "[" + " | ".join(header_parts) + "]"
        context_parts.append(header + ":\n" + chunk["content"])
    context = "\n\n".join(context_parts) if context_parts else "No relevant content found."
    live_web_chunks = sum(1 for chunk in support_set if _is_live_web_chunk(chunk))
    logger.info(f"[SEARCH_TRACE] prompt_context: support_chunks={len(support_set)} live_web_chunks={live_web_chunks} context_len={len(context)}")

    # Fetch verified facts
    from backend.services.fact_verification import FactVerificationService
    fv_service = FactVerificationService()
    verified_facts_str = "No verified facts loaded."
    if creator_id:
        verified_facts_str = fv_service.get_verified_facts_formatted(creator_id)

    # --- Fetch Conversational Memory ---
    memory_context_str = ""
    if user_id and creator_id and thread_id:
        mem_facts = memory_service.get_relevant_context(user_id, creator_id, thread_id, question)
        if mem_facts:
            memory_context_str = "RELEVANT USER FACTS (Conversational Memory):\n"
            for f in mem_facts:
                val = f.get('value', '')
                slot = f.get('slot', '')
                memory_context_str += f"- {slot}: {val}\n"

    # --- Step 1: Meaning Draft (Neutral) ---
    logger.info(f"Generating Meaning Draft (Mode: {mode})...")
    
    if conv_mode == "CURIOSITY_GATE" and creator_profile:
        curious_q = user_priority_service.get_curious_question(creator_profile, user_state)
        # Seed the draft with the curious question
        # If we have a goal in the current thread memory, acknowledge it. Otherwise, just ask.
        has_goal = current_memory.get("user_goal") if current_memory else None
        draft = {
            "meaning_draft": f"Acknowledge the user's intent and ask: {curious_q}" if not has_goal else f"Briefly acknowledge the goal of '{has_goal}' and ask: {curious_q}",
            "is_meaning_complete": True,
            "facts_used": []
        }
    else:
        draft = generate_meaning_draft(
            question, 
            context, 
            verified_facts_str, 
            intent, 
            conversation_history, 
            target_title=target_title, 
            backup_titles=backup_titles, 
            mode=mode, 
            memory_context=memory_context_str, 
            user_state=user_state,
            steering_guidance=steering_guidance,
            steering_move=steering_move,
            support_set=support_set,
        )
    
    # --- Step 1.5: Fast Path for Greetings (GreetingService) ---
    if intent in ["greeting", "greeting_only"] and conv_mode == "GREETING_MODE":
        logger.info("Fast Path: Using GreetingService for greeting.")
        try:
            # Deterministic greeting to prevent hallucinations
            simple_greeting = greeting_service.generate_greeting(
                user_name,
                voice_profile or {},
                creator_name=(creator_profile or {}).get("name"),
                creator_category=(creator_profile or {}).get("creator_category"),
                style_fingerprint=style_fingerprint or {},
                conversation_history=conversation_history or [],
                creator_profile=creator_profile or {},
            )
            # We still might want the LLM to 'voice' it slightly if the DNA is complex,
            # but for now, returning the simple greeting is safer.
            # To maintain compatibility with the polish layer, we update the draft.
            draft["meaning_draft"] = simple_greeting
            draft["is_meaning_complete"] = True
        except Exception as e:
            logger.error(f"GreetingService failed: {e}")
    
    # --- Step 2: Voice Render (Creator Persona) ---
    logger.info("Rendering Voice...")
    
    # Energy Modulation & Tone Mirroring
    energy_data = voice_profile.get("energy", {"default_score": 0.5, "bucket": "MID"})
    current_score = energy_data.get("default_score", 0.5)
    
    # Analyze user style
    user_style = analyze_user_style(question)
    user_tone = user_style["tone"]
    user_len = user_style["length"]
    
    # Tone Mirroring: Calculate target length
    # rule: reply_target_length = clamp(user_length * 2, min=15 words, max=80 words)
    # Only apply strictly if it's a greeting or short exchange
    tone_mirror_limit = 0
    if intent in ["greeting_only", "small_talk", "identity"]:
        target = max(15, min(80, user_len * 2))
        tone_mirror_limit = target
        logger.info(f"Tone Mirroring Active: User={user_len} words, Target Limit={target}")
    
    # Modulate score (don't modulate on FIRST message after switch if we wanted it "obvious", 
    # but the prompt handles that. Let's apply slight modulation)
    if user_tone == "serious":
        current_score *= 0.9
    elif user_tone == "hyped":
        current_score *= 1.05
    current_score = max(0.0, min(1.0, current_score))
    
    # Determine bucket from modulated score
    energy_bucket = "MID"
    if current_score < 0.35: energy_bucket = "LOW"
    elif current_score > 0.70: energy_bucket = "HIGH"
    
    logger.info(f"Energy: baseline={energy_data.get('bucket')}, modulated_score={current_score:.2f}, bucket={energy_bucket}")

    runtime_mode = "greeting" if intent in ["greeting", "greeting_only", "small_talk", "vague_request"] else "task"
    style_dna = distiller.get_style_dna(creator_id or 0, style_fingerprint or {})
    identity_packet = distiller.build_runtime_identity_packet(
        question,
        creator_profile or {"style_fingerprint": style_fingerprint},
        user_state=user_state,
        mode=runtime_mode,
        support_set=support_set,
    )
    dna_instruction = distiller.format_for_prompt(
        style_dna,
        voice_profile=voice_profile,
        mode=runtime_mode,
        identity_packet=identity_packet,
    )
    stance_packet = identity_packet.get("stance") or {}
    
    resource_context = _resource_prompt_context(support_set, question)

    # Intent-specific length and behavioral guidance
    len_guidance = response_length_instruction(
        intent,
        mode=mode,
        energy_bucket=energy_bucket,
        tone_mirror_limit=tone_mirror_limit,
        user_priority_constraints=mode_constraints,
        resource_context=resource_context,
    )
    intent_specific_rule = ""
    if intent == "introduce_content":
        primary_label = resource_context.get("primary_label") or "resource"
        if resource_context.get("video_requested") and not resource_context.get("primary_is_video"):
            if resource_context.get("closest_video_title"):
                intent_specific_rule = f"""
        7. RESOURCE TYPE ACCURACY: The best direct match in context is a {primary_label}, not a video.
           Do NOT call it a video and do NOT tell them to watch it.
           Say clearly that you did not find an exact video for this.
           Present the {primary_label} in Source 1 by its exact title as the best direct match.
           Then mention the closest video alternative, "{resource_context.get("closest_video_title")}", and explain why it is the closest watchable fit.
        """
            else:
                intent_specific_rule = f"""
        7. RESOURCE TYPE ACCURACY: The best direct match in context is a {primary_label}, not a video.
           Do NOT call it a video and do NOT tell them to watch it.
           Say clearly that you did not find an exact video for this.
           Present the {primary_label} in Source 1 by its exact title as the best direct match.
           If they still want something to watch, invite them to ask for the closest video.
        """
        elif not resource_context.get("primary_is_video"):
            intent_specific_rule = f"""
        7. SPECIFIC RECOMMENDATION: The Neutral Plan mentions a specific {primary_label} from my context.
           Mention that {primary_label} literally by its title.
           Use verbs that match the medium, like check out, read, or look at, not watch.
           Explain why this exact {primary_label} is the best next step.
        """
        else:
            intent_specific_rule = """
        7. SPECIFIC RECOMMENDATION: The Neutral Plan mentions a specific video/resource title from my context. 
           Your task is to mention this video LITERALLY by its title (e.g. "Check out my video 'Exact Title'"). 
           Explain WHY this specific video is the bridge to their next level.
           DO NOT use generic phrases like "seek out videos on this topic". NAME THE VIDEO.
        """
    elif intent == "introduce_fallback":
        intent_specific_rule = """
        7. SEARCH SUGGESTION: Tell the user to use the 'Search Channel' card attached to find content about the topic mentioned in the plan.
        """
    elif intent in ["greeting", "greeting_only", "small_talk", "vague_request"]:
        intent_specific_rule = """
        7. GREETING MODE: You are in a high-speed messaging mode.
           - NO explanations of what you can do.
           - NO instructions to the user.
           - Sentence 1: Acknowledge them naturally like a real person.
           - Sentence 2: If you ask a question, keep it broad and social, like "What's on your mind?" or "What are you working on?"
           - NEVER diagnose their business or situation from a hello.
           - NEVER describe your own communication style.
           - MAXIMUM 2 sentences total.
        """

    identity_fallback_rule = ""
    if stance_packet.get("response_mode") == "IDENTITY_FALLBACK":
        identity_fallback_rule = """
        IDENTITY FALLBACK MODE:
        - Retrieval is weak, but the question is still inferable from the creator's worldview.
        - Answer through the creator's values, beliefs, decision heuristics, and response structure.
        - Do NOT invent facts, personal history, dates, or exact claims.
        - Make uncertainty visible in-character when needed. Frame it as how the creator would likely approach the issue.
        """
    elif stance_packet.get("response_mode") == "KNOWLEDGE_PLUS_IDENTITY":
        identity_fallback_rule = """
        HYBRID MODE:
        - Use retrieved support first, then sharpen the answer with the creator's values and reasoning profile.
        - Do NOT let inferred worldview override grounded facts.
        """
    elif stance_packet.get("response_mode") == "BOUNDARY":
        identity_fallback_rule = """
        BOUNDARY MODE:
        - The topic is outside supported knowledge or inference safety.
        - Do not fake an answer.
        - Set a limit in the creator's voice, then redirect to a nearby principle or domain they can genuinely speak on.
        """
    
    # Decide Move-Specific Guidance
    move_guidance = ""
    if move == "ANSWER_DIRECTLY":
        move_guidance = "Answer the user directly and concisely based on the neutral plan."
    elif move == "ANSWER_WITH_QUALIFIER":
        move_guidance = "Answer with caution. Use phrases like 'From what I've shared publicly...' or 'If I recall...'"
    elif move == "DECLINE_PRIVATE":
        move_guidance = "Do NOT provide the information. Politely state that you keep that side of your life private."
    elif move == "DEFLECT_WITH_HUMOR":
        move_guidance = "Do NOT answer directly. Make a creator-appropriate joke or funny deflection, then pivot."
    elif move == "REFRAME_TO_DOMAIN":
        move_guidance = "Acknowledge the question but quickly pivot to a lesson, principle, or domain topic (business/training)."
    elif move == "BOUNDARY_PUSHBACK":
        move_guidance = "Firmly refuse to answer or entertain the question. Be polite but maintain the boundary."
    elif move == "ASK_CLARIFY":
        move_guidance = """
        Mode: ASK_CLARIFY. DO NOT explain why you are asking. DO NOT mention that the user was vague. 
        HUMAN STYLE: 'Hey! Glad you're here. How can I help you today?' or 'Yo! What's on your mind?'
        BANNED STYLE: 'I need more info', 'Since you only said hello', or assuming any specific business goal.
        One sentence greeting + one sentence question. MAX 2 sentences total.
        """

    # If intent is a greeting, ZERO OUT the support set context completely.
    # This ensures the final renderer cannot use any retrieved business facts during a hello.
    if intent in ["greeting", "greeting_only", "small_talk"]:
        context = "No relevant context (Greeting Mode)."
        support_set = []
    
    # Construct Render Prompt
    render_system_prompt = f"""
You are {creator_name or 'the creator'}. 

OFPO STEP 3 - PERSONA OVERLAY (Shader, not Driver):
Your persona is a "filter" applied to the NEUTRAL PLAN. 
- You control: sentence length, punchiness, metaphors, slang, and directness.
- You are BLOCKED from: changing the chosen "Next Action", adding extra steps, adding unrelated anecdotes, or adding "fun facts".
- Follow the NEUTRAL PLAN exactly. Do NOT add new sections.

ACTION: {mode_constraints.get('next_action', 'Explain')}
BUDGET: Max {mode_constraints.get('max_sentences', 4)} sentences, {mode_constraints.get('max_bullets', 0)} bullets.

STEERING: {steering_move} ({steering_guidance})

{memory_guidance}

{persona}
{f"GREETING CONSTRAINT: You are greeting a user. Stay in your unique voice and personality but do NOT provide advice, plans, or business topics. Keep it brief, warm, and conversational." if intent in ["greeting", "greeting_only", "small_talk", "vague_request"] else ""}

MISSION:
Rewrite the NEUTRAL CONTENT PLAN below into your unique voice and style.
Strictly adhere to the STYLE DNA constraints.

{dna_instruction}

{build_voice_echo_block(support_set)}

{identity_fallback_rule}

STRICT RULE: If CONVERSATION MODE is 'GREETING_MODE', you MUST NOT provide advice, plans, or mention any specific business topics. Simply greet and ask one open-ended question.

RULES:
1. CONTEXT DOMINANCE: Use ONLY the video title and facts provided in the "NEUTRAL PLAN". 
2. NO SOURCES: Do NOT mention "Source 1" or include URLs. Speak as if you know this info.
3. NO FILLER: Do not say "Here is a plan" or "I hope this helps". Dive straight in.
4. HONESTY: If the plan says "uncertainty_handling: admit_unknown", admit you don't know in your voice.
5. FORMAT: Use the structure defined in the DNA.
6. USER: You are talking to {user_name or 'a friend'}.
7. NO LINKS: Do not output any http links manually.
8. PERSONA PROTECTION: Strictly PURGE all meta-talk like "I don't have enough info" or "Based on my data". NEVER mention being an AI.
9. VERBOSITY: Strictly stay within the BUDGET. Cut content if necessary.
10. FORMATTING QUALITY: Write complete, clean sentences. Every word must be spelled as one unbroken unit (correct: "mean", wrong: "me an"). Never drop words mid-sentence. Never leave dangling punctuation or orphaned parentheses. Every sentence must be grammatically complete.
11. FACT GROUNDING: The NEUTRAL PLAN may contain things the creator SAID in their content — these are NOT your personal biography. CRITICAL: Source headers contain metadata like "Content uploaded: 2017" — that is the upload/publish date of the VIDEO or ARTICLE, NOT when something happened to you. Never say "I was published in [year]" or "I started in [year]" based on a content upload date. Only claim personal timeline events ("I started trading in...", "I moved to...") if the transcript text itself explicitly states that biographical fact in first person. When unsure, say "In one of my videos I talked about..." rather than adopting it as your life story.

NEUTRAL PLAN:
{json.dumps(draft, indent=2)}
"""

    messages = [{"role": "system", "content": render_system_prompt}]
    
    # Add history for continuity
    if conversation_history:
        for msg in conversation_history[-5:]: 
             if msg.get("role") in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg.get("content", "")})
    
    # Current User Input
    messages.append({"role": "user", "content": f"Question: {question}\n(Respond using the plan above in your voice.)"})
    
    # --- Generation & Repair Loop ---
    final_response = ""
    is_rewrite = False
    style_score = {}
    
    # Pass 1
    response_text = rag.generate_chat_completion(
        messages=messages,
        model=settings.FINAL_RESPONSE_MODEL,
        temperature=0.7,
        max_tokens=1000
    )
    
    # --- Human Compression Filter & Energy Check ---
    word_count = len(response_text.split())
    # Derive max_words from len_guidance extraction or get_energy_constraints
    # The prompt already has it, but let's be robust.
    max_w = get_energy_constraints(energy_bucket, intent)["max_words"]
    if tone_mirror_limit > 0:
        max_w = min(max_w, tone_mirror_limit)
    if mode == "ASK_ONE_QUESTION": 
        max_w = 80 if energy_bucket == "HIGH" else 100
    
    # Check for banned explanation phrases
    banned_phrases = ["i don't have enough information", "based on what you said", "to better assist you", "in order to help", "i'm not going to guess"]
    has_banned = any(b in response_text.lower() for b in banned_phrases)
    
    # User Priority Guardrail: Check for jargon if beginner
    has_jargon_failure = False
    if user_state.get("skill_level") == "beginner" and not mode_constraints.get("jargon_allowed", True):
        # Heuristic: check for complex technical terms
        technical_terms = ["asymptotic", "liquidity sweep", "order block", "hypertrophy", "gluconeogenesis", "scalability", "monetization", "retention rate"]
        found_jargon = [t for t in technical_terms if t in response_text.lower()]
        if found_jargon:
            has_jargon_failure = True
            logger.info(f"Jargon detected in beginner response: {found_jargon}")

    if word_count > max_w * 1.2 or (intent == "greeting_only" and has_banned) or has_jargon_failure:
        reason = f"Response over limit ({word_count}/{max_w})" if word_count > max_w * 1.2 else "Contains banned explanation phrases"
        if has_jargon_failure: reason = "Contains technical jargon for a beginner"
        
        logger.info(f"{reason}. Compressing...")
        compression_prompt = f"""
        {render_system_prompt}
        
        CRITICAL: Your previous response needed editing: {reason}.
        1. Remove ALL explanation phrases like "I don't have enough info" or "Based on...".
        2. Remove technical jargon if the user is a beginner.
        3. Ensure the message is under {max_w} words.
        4. Maintain the PUNCHINESS and creators style.
        
        Current Draft:
        {response_text}
        """
        response_text = rag.generate_chat_completion(
            messages=[{"role": "system", "content": compression_prompt}],
            model=settings.REWRITE_MODEL, # Fast edit
            temperature=0.3
        )
    
    # Verify
    score_result = scorer.score_response(response_text)
    style_score = score_result
    
    if score_result["passed"]:
        final_response = response_text
    else:
        logger.info(f"Style Check Failed: {score_result['final_score']}. Rewriting...")
        is_rewrite = True
        
        repair_prompt = f"""
CRITIQUE: The response failed the style check (Score: {score_result['final_score']}).
VIOLATIONS:
- Structure: {score_result['structural_score']} (Check sentence length/paragraphing)
- Lexical: {score_result['lexical_score']} (Check vocabulary)
- Behavioral: {score_result['behavioral_score']} (Check tone/frameworks)

REPAIR INSTRUCTION:
Rewrite the response to fix these violations. 
- Vary sentence structure.
- Remove generic filler.
- Be more authentic.
- KEEP THE INFORMATION THE SAME.
"""
        messages.append({"role": "assistant", "content": response_text})
        messages.append({"role": "user", "content": repair_prompt})
        
        final_response = rag.generate_chat_completion(
            messages=messages,
            model=settings.REWRITE_MODEL, # Fast edit
            temperature=0.75
        )

    # --- Step 4: Post-Processing ---
    # Strip URLs to enforce "No sources shown in chat" (just in case model hallucinated them)
    try:
        if not include_links_in_output:
            final_response = strip_urls_from_text(final_response)
    except:
        pass

    # Build sources list for UI (not for chat text)
    unique_sources = []
    seen_urls = set()
    for chunk in support_set:
        ref = chunk.get("source_ref", {})
        url = ref.get("canonical_url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_sources.append({
                "platform": ref.get("platform", ""),
                "canonical_url": url,
                "title": ref.get("title", ""),
                "published_at": ref.get("published_at"),
                "content_type": ref.get("content_type", ""),
            })

    # --- Step 5: Grounding & Rhythm application ---
    # Prepare draft for validation (map keys)
    draft["sources"] = answer_contract.get("sources", [])
    if "facts" not in draft:
        draft["facts"] = draft.get("required_facts", [])

    grounding_report = validate_grounding(final_response, draft, support_set)
    
    # --- Steering Validation ---
    steering_report = steering_service.validate_steering(final_response, steering_move, intent)
    if steering_report.get("drift_detected") or steering_report.get("overwhelmed"):
        logger.warning(f"Steering Violation: {steering_report.get('reason')}")
        # Mark for repair
        grounding_report["steering_violation"] = steering_report.get("reason")
        grounding_report["is_grounded"] = False 
    final_response = repair_answer(
        final_response,
        draft,
        support_set,
        grounding_report,
        question,
        persona,
        allow_cta,
        enabled_platforms,
        intent,
        voice_profile=voice_profile
    )
    
    # --- Step 6: Final Persona Surface Filter ---
    # Guaranteed removal of system voice / meta-statements
    final_response = apply_persona_surface_filter(
        final_response,
        intent,
        voice_profile,
        creator_name=creator_name or "The Creator",
        style_fingerprint=style_fingerprint,
    )
    final_response = clean_response(
        final_response, 
        strip_hyphens=should_strip_hyphens(creator_profile)
    )

    regurgitation_report = check_for_regurgitation(final_response, support_set)
    if not regurgitation_report.get("is_clean", True):
        logger.warning(
            "Transcript regurgitation guard flagged sync reply for creator_id=%s: %s",
            creator_id,
            regurgitation_report.get("reason"),
        )
    if creator_profile:
        final_response = interaction_engine._apply_creator_integrity_guard(
            final_response,
            creator_profile,
            support_set,
            question,
            allow_links=include_links_in_output,
            persona=persona,
        )
    creator_markers: List[str] = []
    if creator_profile:
        style_fp = creator_profile.get("style_fingerprint") or {}
        if isinstance(style_fp, str):
            try:
                style_fp = json.loads(style_fp)
            except Exception:
                style_fp = {}
        if isinstance(style_fp, dict):
            lexical_rules = style_fp.get("lexical_rules") or {}
            creator_markers = list((style_fp.get("evidence_snippets") or [])[:6])
            creator_markers += list((lexical_rules.get("signature_phrases") or [])[:4])
    quality_report = score_response_quality(
        question,
        final_response,
        support_set,
        creator_markers=creator_markers,
    )
    if quality_report.get("grade") in {"fair", "weak"}:
        logger.warning(
            "Response quality check for creator_id=%s returned %s (%s)",
            creator_id,
            quality_report.get("grade"),
            ", ".join(quality_report.get("penalties") or []) or "no penalties",
        )

    debug_info = {
        "draft": draft,
        "style_score": style_score,
        "is_rewrite": is_rewrite,
        "dna_used": style_dna,
        "retrieved_count": len(support_set),
        "sources": unique_sources[:5],
        "identity_packet": identity_packet,
        "regurgitation_report": regurgitation_report,
        "quality_report": quality_report,
    }
    
    return final_response, debug_info


def validate_grounding(
    answer: str,
    answer_contract: Dict[str, Any],
    support_set: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Step 6: Validate grounding - check if claims are supported.
    """
    # Simple validation: check if answer mentions sources/links
    # In production, use LLM to extract claims and verify against facts
    
    has_sources = any(
        source["source_ref"].get("canonical_url") 
        for source in answer_contract["sources"]
    )
    
    # Check if answer contains unsupported claims (heuristic)
    # Look for definitive statements without source mentions
    definitive_patterns = [
        r"he (always|never|definitely|certainly)",
        r"the creator (always|never|definitely|certainly)",
    ]
    
    ungrounded_claims = []
    for pattern in definitive_patterns:
        matches = re.findall(pattern, answer.lower())
        if matches:
            # Check if nearby text mentions a source
            # Simplified check - in production, use more sophisticated NLP
            ungrounded_claims.extend(matches)
    
    is_grounded = len(ungrounded_claims) == 0 and has_sources
    
    return {
        "is_grounded": is_grounded,
        "ungrounded_claims": ungrounded_claims,
        "has_sources": has_sources,
        "support_strength": len(answer_contract["facts"]) / max(1, len(support_set)),
    }


def repair_answer(
    answer: str,
    answer_contract: Dict[str, Any],
    support_set: List[Dict[str, Any]],
    grounding_report: Dict[str, Any],
    question: str,
    persona: Optional[str] = None,
    allow_sources: bool = True,
    enabled_platforms: Optional[List[str]] = None,
    intent: str = "how_to",
    voice_profile: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Step 7: Repair answer if grounding validation failed.
    When allow_sources is False, do not add a Sources block (link gating).
    When intent is request_sources, skip Sources block (links are inline only).
    """
    if grounding_report["is_grounded"]:
        # Apply Speech Rhythm Micro-Hesitations
        return apply_speech_rhythm(answer, voice_profile, intent)

    repaired = answer

    # --- Steering Repair ---
    if grounding_report.get("steering_violation"):
        logger.info("Applying Steering Repair...")
        repair_prompt = f"""
        REPAIR INSTRUCTION: The following response violated conversation steering rules.
        Issue: {grounding_report['steering_violation']}
        
        TASK:
        1. Remove any unrelated tangents or "drift" concepts.
        2. If too long/overwhelming, trim to the CRITICAL next step only.
        3. Maintain the original persona and voice.
        
        Original Response:
        {answer}
        """
        try:
            repaired = rag.generate_chat_completion(
                messages=[{"role": "system", "content": repair_prompt}],
                model=settings.REWRITE_MODEL,
                temperature=0.0
            )
        except Exception as e:
            logger.error(f"Steering repair failed: {e}")

    # Add source citation only when links allowed, not request_sources, and missing; skip URLs already in answer
    if (
        allow_sources
        and intent != "request_sources"
        and not grounding_report["has_sources"]
        and answer_contract.get("sources")
    ):
        extra = sources_section(
            answer_contract["sources"], max_links=3,
            enabled_platforms=enabled_platforms,
            exclude_urls=_urls_in_text(repaired),
        )
        if extra:
            repaired = (repaired.rstrip() + "\n\n" + extra).strip()

    # If grounding strength is low, do NOT add meta disclaimers like "Based on available content".
    # Rely on the Persona Surface Filter to handle low confidence naturally.
    if grounding_report["support_strength"] < 0.3:
        pass # Do nothing

    # Apply Speech Rhythm Micro-Hesitations
    repaired = apply_speech_rhythm(repaired, voice_profile, intent)

    return repaired

def apply_speech_rhythm(text: str, voice_profile: Dict[str, Any], intent: str) -> str:
    """
    Injects micro-hesitations and rhythm markers based on creator profile.
    - Max 1-2 insertions per message.
    - Only at natural boundaries (start, after comma).
    """
    import random
    
    # Skip for very short or structured outputs
    if len(text.split()) < 20 or "1." in text or "-" in text[:5]:
        return text
        
    rhythm = voice_profile.get("speech_rhythm", {})
    fillers = rhythm.get("fillers", [])
    rate = rhythm.get("filler_rate", 0.1)
    
    if not fillers or random.random() > rate:
        return text
        
    # Insertion Logic
    sentences = text.split(". ")
    if len(sentences) < 2:
        # Maybe insert at start
        if random.random() < 0.3:
            filler = random.choice(fillers)
            return f"{filler}, {text[0].lower() + text[1:]}"
        return text
        
    # Insert filler at start of 2nd or later sentence
    idx = random.randint(1, len(sentences) - 1)
    filler = random.choice(fillers)
    
    # Don't break flow if sentence starts with capital
    original = sentences[idx]
    if original[0].isupper():
        sentences[idx] = f"{filler}, {original[0].lower() + original[1:]}"
    else:
        sentences[idx] = f"{filler}, {original}"
        
    return ". ".join(sentences)


def _get_suggested_resources(history: Optional[List[Dict[str, str]]]) -> Dict[str, Set[str]]:
    """Helper to extract all YouTube/Resource URLs and Titles mentioned in chat history."""
    seen = {"ids": set(), "titles": set()}
    if not history:
        return seen
    
    # Scan a deeper history window to ensure a truly unique experience
    for m in history[-50:]:
        content = (m.get("content") or m.get("text") or "").lower()
        # 1. Match youtube IDs
        matches = re.findall(r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]+)", content)
        for vid_id in matches:
            seen["ids"].add(vid_id)

        for card in m.get("cards") or []:
            card_url = (card.get("url") or "").strip()
            if card_url:
                youtube_match = re.search(r"(?:v=|youtu\.be/|/shorts/)([\w-]+)", card_url, re.IGNORECASE)
                if youtube_match:
                    seen["ids"].add(youtube_match.group(1))
            card_title = _normalize_resource_title(card.get("title") or "")
            if len(card_title) > 5:
                seen["titles"].add(card_title)
            
        # 2. Match titles in quotes and patterns
        quoted_titles = re.findall(r'"([^"]+)"', content)
        natural = re.findall(r'(?:watch|check out|video|resource|lesson|recommend|suggest)\s+([\w\s\-\(\):]+)', content)
        
        for t in quoted_titles + natural:
            # ULTRA REGRESSIVE NORMALIZATION
            clean_t = re.sub(r'[^a-z0-9]', '', t).strip()
            if len(clean_t) > 5:
                seen["titles"].add(clean_t)
            
    return seen


def _build_resource_search_query(
    user_message: str,
    resource_intent: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    base_query = (resource_intent or {}).get("query") or user_message
    contextual_query = build_search_query(base_query, history)
    if contextual_query and len(contextual_query.split()) > len(base_query.split()):
        return contextual_query
    return base_query

def llm_rerank(candidates: List[Dict[str, Any]], intent_plan: Dict[str, Any], top_n: int = 5) -> List[Dict[str, Any]]:
    """
    Stage 4: Helpful Rerank (LLM-based).
    Take top candidates and rerank using direct goal fit, actionability, clarity, and evidence.
    """
    if not candidates: return []
    import backend.rag as rag
    import json
    
    # Prepare candidate list for LLM
    candidate_meta = []
    for c in candidates[:15]: # Review top 15
        candidate_meta.append({
            "id": c.get("id") or c.get("chunk_id"),
            "title": c.get("title") or c.get("source_ref", {}).get("title"),
            "snippet": c.get("snippet") or c.get("content")[:400],
            "type": c.get("resource_type") or c.get("source_ref", {}).get("content_type"),
            "evidence_metrics": c.get("evidence_metrics", {})
        })

    user_level = intent_plan.get("user_level", "unknown")
    
    system_prompt = f"""
You are a Content Quality Reranker. Your goal is to identify the SINGLE MOST HELPFUL resource for the user.

USER INTENT: {intent_plan.get('intent_type')}
GOAL: {intent_plan.get('implicit_goal')}
AXIS: {intent_plan.get('task_axis')}
USER LEVEL: {user_level}
HELP CRITERIA: {intent_plan.get('help_criteria')}

For each candidate, provide a Score (0-100) based on:
1. Direct Goal Fit: Does this directly answer the user's implicit goal?
2. Actionability: Does it provide concrete steps the user can take today?
3. Clarity/Level: Is it appropriate for a {user_level} user?
4. Evidence Strength: Based on the snippets and evidence metrics.
5. Creator Authenticity: Does this reflect the creator's "signature" style and unique frameworks?

Output ONLY a JSON object:
{{
  "scores": [
    {{ "id": "...", "score": 85, "internal_rationale": "..." }},
    ...
  ]
}}
"""

    user_prompt = f"Candidates: {json.dumps(candidate_meta)}"
    
    try:
        response_text = rag.generate_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model=settings.RERANK_MODEL,
            temperature=0.0,
            json_mode=True
        )
        scores_data = json.loads(response_text).get("scores", [])
        score_map = { s["id"]: s for s in scores_data }
        
        # Merge scores back into candidates
        for c in candidates:
            cid = c.get("id") or c.get("chunk_id")
            s_entry = score_map.get(cid)
            if s_entry:
                c["rerank_score"] = s_entry["score"] / 100.0
                c["internal_rationale"] = s_entry.get("internal_rationale")
            else:
                c["rerank_score"] = 0.0

        return sorted(candidates, key=lambda x: x.get("rerank_score", 0), reverse=True)
    except Exception as e:
        logger.error(f"llm_rerank failed: {e}")
        return candidates


def _pairwise_compare_candidates(
    left: Dict[str, Any],
    right: Dict[str, Any],
    intent_plan: Dict[str, Any],
) -> str:
    import backend.rag as rag

    payload = {
        "user_goal": intent_plan.get("implicit_goal") or intent_plan.get("intent_type"),
        "task_axis": intent_plan.get("task_axis"),
        "learning_phase": intent_plan.get("learning_phase"),
        "left": {
            "id": left.get("id") or left.get("chunk_id"),
            "title": left.get("title") or (left.get("source_ref") or {}).get("title"),
            "summary": ((left.get("asset_profile") or {}).get("summary") or left.get("snippet") or left.get("content") or "")[:320],
            "mode": (left.get("asset_profile") or {}).get("content_mode"),
            "audience": (left.get("asset_profile") or {}).get("audience_level"),
            "format": (left.get("asset_profile") or {}).get("format_label"),
        },
        "right": {
            "id": right.get("id") or right.get("chunk_id"),
            "title": right.get("title") or (right.get("source_ref") or {}).get("title"),
            "summary": ((right.get("asset_profile") or {}).get("summary") or right.get("snippet") or right.get("content") or "")[:320],
            "mode": (right.get("asset_profile") or {}).get("content_mode"),
            "audience": (right.get("asset_profile") or {}).get("audience_level"),
            "format": (right.get("asset_profile") or {}).get("format_label"),
        },
    }
    system_prompt = """
You are a pairwise recommendation judge.

Pick the SINGLE more helpful creator resource for the user's exact need.
Prefer directness, goal fit, correct medium, and practical usefulness.

Return JSON only:
{"winner_id": "left-or-right-id", "reason": "short reason"}
"""
    try:
        response_text = rag.generate_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            model=settings.RERANK_MODEL,
            temperature=0.0,
            json_mode=True,
        )
        winner_id = str(json.loads(response_text).get("winner_id") or "").strip()
        return winner_id
    except Exception as exc:
        logger.warning("Pairwise candidate comparison failed: %s", exc)
        return str(left.get("id") or left.get("chunk_id") or "")


def pairwise_rerank_if_ambiguous(
    candidates: List[Dict[str, Any]],
    intent_plan: Dict[str, Any],
    *,
    max_candidates: int = 5,
) -> List[Dict[str, Any]]:
    if not candidates or len(candidates) < 2:
        return candidates

    working = [dict(candidate) for candidate in candidates]
    top_slice = working[:max_candidates]
    incumbent = top_slice[0]
    win_counts = {str(item.get("id") or item.get("chunk_id") or ""): 0 for item in top_slice}

    for challenger in top_slice[1:]:
        winner_id = _pairwise_compare_candidates(incumbent, challenger, intent_plan)
        challenger_id = str(challenger.get("id") or challenger.get("chunk_id") or "")
        if winner_id == challenger_id:
            incumbent = challenger
        if winner_id:
            win_counts[winner_id] = win_counts.get(winner_id, 0) + 1

    for candidate in working:
        cid = str(candidate.get("id") or candidate.get("chunk_id") or "")
        candidate["pairwise_wins"] = win_counts.get(cid, 0)
        candidate["pairwise_score"] = float(candidate.get("rerank_score", 0.0) or 0.0) + (0.05 * candidate["pairwise_wins"])

    return sorted(
        working,
        key=lambda item: (
            float(item.get("pairwise_score") or 0.0),
            float(item.get("rerank_score") or 0.0),
        ),
        reverse=True,
    )

def calculate_gate_confidence(candidates: List[Dict[str, Any]], temperature: float = 0.1) -> float:
    """
    Stage 5: ONE-PICK decision policy (confidence gate).
    gap = score(top1) - score(top2)
    confidence = sigmoid(gap / temperature)
    """
    if not candidates:
        return 0.0
    if len(candidates) == 1:
        # If we only have 1 good candidate, confidence depends on its own score
        base_score = candidates[0].get("rerank_score", 0)
        return 0.75 if base_score > 0.7 else 0.4
    
    s1 = candidates[0].get("rerank_score", 0)
    s2 = candidates[1].get("rerank_score", 0)
    gap = s1 - s2
    
    try:
        # Sharpness temperature (default 0.1) creates a sharp cut-off
        val = gap / temperature
        conf = 1 / (1 + math.exp(-val))
        return conf
    except OverflowError:
        return 1.0 if gap > 0 else 0.0


def _can_skip_llm_rerank(
    candidates: List[Dict[str, Any]],
    resource_intent: Dict[str, Any],
    preferred_platforms: Optional[List[str]] = None,
) -> bool:
    if not candidates:
        return False
    top = candidates[0]
    top_score = float(top.get("rerank_score", 0.0) or 0.0)
    runner_up = float(candidates[1].get("rerank_score", 0.0) or 0.0) if len(candidates) > 1 else 0.0
    gap = top_score - runner_up
    title_quality = float(top.get("title_quality", _resource_title_quality(_candidate_title(top), _candidate_url(top))))
    if preferred_platforms:
        preferred = {platform.lower() for platform in preferred_platforms if platform}
        if preferred and _candidate_platform(top) not in preferred:
            return False
    if resource_intent.get("needs_resource") and title_quality >= 0.65 and top_score >= 0.55 and gap >= 0.12:
        return True
    if (
        str(resource_intent.get("resource_type") or "").lower() in {"video", "article", "course_lesson", "any"}
        and title_quality >= 0.6
        and top_score >= 0.5
        and gap >= 0.1
    ):
        return True
    return False
        
def recommend_one_content(
    user_id: int, 
    creator_id: int, 
    user_message: str, 
    conversation_history: Optional[List[Dict[str, str]]] = None,
    creator_row: Optional[Dict[str, Any]] = None,
    debug: bool = False,
    q_emb: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """
    Advanced, creator-aware 'ONE BEST CONTENT' recommender.
    Returns exactly ONE recommended piece of content or a clarifying question.
    """
    # Stage 0: Request parsing + creator context
    resource_intent = classify_resource_intent(user_message, conversation_history, creator_row)
    q_search = _build_resource_search_query(user_message, resource_intent, conversation_history)
    context_features = _build_recommendation_context_features(user_message, conversation_history, resource_intent)
    preferred_platforms = extract_requested_platforms(user_message, conversation_history)
    resource_intent["preferred_platforms"] = preferred_platforms
    seen_resources = _get_suggested_resources(conversation_history)
    wants_multiple = _wants_multiple_resources(user_message)
    
    # Early Exit for Small Talk or non-resource turns
    if not resource_intent.get("needs_resource") and resource_intent.get("request_type") == "none":
        logger.info(f"Recommender: Intent 'none' and no resource needed. Falling back to chat.")
        return {"recommended": None, "confidence": 0.0, "should_fallback": True, "resource_intent": resource_intent, "q_emb": None}
    
    # Stage 1: Candidate retrieval (Broad + Fast)
    # Get embedding for semantic search
    if not q_emb:
        from backend.rag import get_client
        try:
            emb_resp = get_client().embeddings.create(model=settings.EMBEDDING_MODEL, input=q_search)
            q_emb = emb_resp.data[0].embedding
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            return {"answer": "I'm having trouble searching my content right now.", "recommended": None}

    enabled_platforms = get_enabled_platforms_for_creator(creator_id)
    query_variants = _build_recommendation_query_variants(
        q_search,
        resource_intent,
        creator_profile=creator_row,
        history=conversation_history,
        allow_llm=False,
        limit=2,
    )
    raw_chunks, retrieval_debug = _hybrid_retrieve_recommendation_chunks(
        creator_id,
        query_variants,
        enabled_platforms=enabled_platforms,
        k_dense=K_RETRIEVE,
        k_sparse=max(8, K_RETRIEVE // 2),
        base_query=q_search,
        base_embedding=q_emb,
    )
    
    # Platform Preference: YouTube if user wants to "watch"
    wants_video = "video" in user_message.lower() or "watch" in user_message.lower()
    
    # Stage 2: Evidence-first chunk scoring
    candidates = _aggregate_document_evidence(raw_chunks)
    candidates = _filter_candidates_for_requested_platforms(candidates, preferred_platforms)
    deduped_candidates = [
        candidate for candidate in candidates
        if not _is_recent_duplicate_candidate(candidate, seen_resources)
    ]
    if deduped_candidates:
        candidates = deduped_candidates
    
    # Stage 3: Goal/Intent compatibility + Contradiction handling
    candidates = rerank_candidates(
        candidates,
        q_search,
        resource_intent,
        preferred_platforms=preferred_platforms,
        creator_id=creator_id,
        context_features=context_features,
    )

    initial_confidence = calculate_gate_confidence(candidates)
    should_expand_queries = bool(
        context_features.get("is_ambiguous")
        or context_features.get("needs_exact_match")
        or len(candidates) < 3
        or initial_confidence < 0.72
    )
    if should_expand_queries:
        expanded_variants = _build_recommendation_query_variants(
            q_search,
            resource_intent,
            creator_profile=creator_row,
            history=conversation_history,
            allow_llm=True,
            limit=3,
        )
        if len(expanded_variants) > len(query_variants):
            raw_chunks, retrieval_debug = _hybrid_retrieve_recommendation_chunks(
                creator_id,
                expanded_variants,
                enabled_platforms=enabled_platforms,
                k_dense=K_RETRIEVE,
                k_sparse=max(10, K_RETRIEVE // 2),
                base_query=q_search,
                base_embedding=q_emb,
            )
            candidates = _aggregate_document_evidence(raw_chunks)
            candidates = _filter_candidates_for_requested_platforms(candidates, preferred_platforms)
            deduped_candidates = [
                candidate for candidate in candidates
                if not _is_recent_duplicate_candidate(candidate, seen_resources)
            ]
            if deduped_candidates:
                candidates = deduped_candidates
            candidates = rerank_candidates(
                candidates,
                q_search,
                resource_intent,
                preferred_platforms=preferred_platforms,
                creator_id=creator_id,
                context_features=context_features,
            )
            query_variants = expanded_variants
    
    # Stage 4: Helpfulness rerank (LLM) only when the heuristic top pick is ambiguous.
    if _can_skip_llm_rerank(candidates, resource_intent, preferred_platforms=preferred_platforms):
        logger.info("Recommender: Skipping LLM rerank for strong top candidate.")
    else:
        candidates = llm_rerank(candidates, resource_intent)
    if len(candidates) > 1:
        top_gap = float(candidates[0].get("rerank_score", 0.0) or 0.0) - float(candidates[1].get("rerank_score", 0.0) or 0.0)
        if top_gap < 0.08:
            candidates = pairwise_rerank_if_ambiguous(candidates, resource_intent, max_candidates=5)
    
    # Stage 5: ONE-PICK decision policy (Confidence Gate)
    confidence = calculate_gate_confidence(candidates)
    
    if confidence < 0.65:
        # LOW CONFIDENCE: Ask clarifying question
        cl_question = candidates[0].get("llm_reason") if candidates and candidates[0].get("llm_reason") else "Could you tell me more about what you're looking for so I can give you the best pick?"
        # The prompt for clarification should be short and creator-voiced (handled in render stage)
        return {
            "recommended": None,
            "confidence": confidence,
            "clarify_question": cl_question,
            "candidates": candidates[:1], # Pass top 1 for context
            "resource_intent": resource_intent,
            "q_emb": q_emb,
            "query_variants": query_variants,
            "retrieval_debug": retrieval_debug,
        }
    
    # HIGH CONFIDENCE: Pick top 1
    best_one = candidates[0]
    best_kind = _candidate_resource_kind(best_one)
    alternate_candidates = []
    if wants_multiple:
        for candidate in candidates[1:]:
            if _candidate_url(candidate) and _candidate_title(candidate):
                alternate_candidates.append(candidate)
            if len(alternate_candidates) >= 2:
                break
    explicit_video_request = bool(
        wants_video
        or str(resource_intent.get("resource_type") or "").strip().lower() == "video"
    )
    if explicit_video_request and not _is_video_like_kind(best_kind):
        for candidate in candidates[1:]:
            if not _candidate_url(candidate) or not _candidate_title(candidate):
                continue
            candidate_kind = _candidate_resource_kind(candidate)
            if not _is_video_like_kind(candidate_kind):
                continue
            candidate_url = (_candidate_url(candidate) or "").strip().lower()
            existing_urls = {(_candidate_url(item) or "").strip().lower() for item in alternate_candidates}
            if candidate_url and candidate_url not in existing_urls:
                alternate_candidates.insert(0, candidate)
            break
    best_title = _candidate_title(best_one)
    best_url = _candidate_url(best_one)
    best_one["title_quality"] = _resource_title_quality(best_title, best_url)
    if preferred_platforms and _candidate_platform(best_one) not in {platform.lower() for platform in preferred_platforms}:
        confidence = min(confidence, 0.45)
    if best_one["title_quality"] < 0.45:
        confidence = min(confidence, 0.45)
    if confidence < 0.65:
        cl_question = best_one.get("llm_reason") or "Give me one more detail on what kind of resource you want."
        return {
            "recommended": None,
            "confidence": confidence,
            "clarify_question": cl_question,
            "best_candidate": best_one,
            "candidates": candidates[:1],
            "alternate_candidates": alternate_candidates,
            "card_limit": 1 if not wants_multiple else min(3, 1 + len(alternate_candidates)),
            "resource_intent": resource_intent,
            "q_emb": q_emb,
            "query_variants": query_variants,
            "retrieval_debug": retrieval_debug,
        }
    resource_intent["matched_resource_kind"] = best_kind
    resource_intent["matched_resource_label"] = _resource_label(best_kind, _candidate_platform(best_one))
    resource_intent["video_request_without_exact_video"] = bool(
        explicit_video_request and not _is_video_like_kind(best_kind)
    )
    if alternate_candidates and explicit_video_request and not _is_video_like_kind(best_kind):
        resource_intent["closest_video_title"] = _candidate_title(alternate_candidates[0])

    card_limit = 1 if not wants_multiple else min(3, 1 + len(alternate_candidates))
    if explicit_video_request and not _is_video_like_kind(best_kind) and alternate_candidates:
        card_limit = max(card_limit, 2)

    return {
        "recommended": {
            "id": best_one.get("id"),
            "title": best_title,
            "url": best_url,
            "thumbnail": best_one.get("thumbnail"),
            "platform": best_one.get("platform"),
            "creator_id": creator_id
        },
        "confidence": confidence,
        "clarify_question": None,
        "best_candidate": best_one,
        "alternate_candidates": alternate_candidates,
        "card_limit": card_limit,
        "resource_intent": resource_intent,
        "q_emb": q_emb,
        "query_variants": query_variants,
        "retrieval_debug": retrieval_debug,
    }

def grounded_rag_ask(
    creator_id: int,
    question: str,
    thread_id: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    top_k: int = K_FINAL,
    max_distance: float = 1.15,
    debug: bool = False,
    user_preferences: Optional[Dict[str, Any]] = None,
    user_name: Optional[str] = None,
    creator_name: Optional[str] = None,
    images: Optional[List[Dict[str, Any]]] = None,
    user_id: int = 1,
) -> Dict[str, Any]:
    conversation_history = conversation_history or []
    resolved_question = decision_service.resolve_followup_question(question, conversation_history)
    if resolved_question != question:
        logger.info("Resolved short follow-up '%s' to '%s'", question, resolved_question)
        question = resolved_question

    def _verify_source_grounding(answer_text: str, support_set: List[Dict[str, Any]]) -> str:
        """Remove fabricated video/content title mentions that don't match any
        retrieved chunk.  Keeps the rest of the answer intact.

        Strategy: extract quoted titles from the LLM output (patterns like
        "in my video …" or "I covered this in …") and check each against the
        support set titles.  If a title is completely ungrounded, redact the
        specific mention while preserving the surrounding advice.
        """
        if not answer_text or not support_set:
            return answer_text

        # Collect all actual titles from the support set
        real_titles: Set[str] = set()
        for chunk in support_set:
            ref = chunk.get("source_ref") or {}
            t = (ref.get("title") or chunk.get("title") or "").strip().lower()
            if t and len(t) > 3:
                real_titles.add(t)

        if not real_titles:
            return answer_text

        # Find title-like mentions in the LLM output
        # Patterns: "in my video [title]", "in [title]", etc.
        _TITLE_MENTION_RE = re.compile(
            r'(?:in\s+(?:my\s+)?(?:video|episode|clip)\s+["""]([^"""\n]{5,120})["""]'
            r'|["""]([^"""\n]{5,120})["""])',
            re.IGNORECASE,
        )

        def _is_grounded(mentioned_title: str) -> bool:
            mt = mentioned_title.strip().lower()
            for rt in real_titles:
                # Fuzzy: if >60% of words overlap, consider it grounded
                mt_words = set(re.findall(r"\w+", mt))
                rt_words = set(re.findall(r"\w+", rt))
                if not mt_words:
                    return False
                overlap = len(mt_words & rt_words)
                if overlap / max(len(mt_words), 1) > 0.55:
                    return True
                if mt in rt or rt in mt:
                    return True
            return False

        result = answer_text
        for m in _TITLE_MENTION_RE.finditer(answer_text):
            title = m.group(1) or m.group(2) or ""
            if title and not _is_grounded(title):
                logger.warning("Source grounding: redacting ungrounded title '%s'", title)
                # Replace the fabricated title with a generic reference
                result = result.replace(m.group(0), "in one of my videos")

        return result

    def apply_final_polish(result_dict: Dict[str, Any], profile: Dict[str, Any], state_mgr: Any, mvc_score: int = 0, plan: Optional[InteractionPlan] = None) -> Dict[str, Any]:
        if "answer" in result_dict:
            answer = _unwrap_structured_answer(result_dict["answer"])
            
            # The interaction engine already handles voice, structure, reduction,
            # and markdown stripping. The old post-processors are fully bypassed.
            
            is_light_route = plan and getattr(plan, 'route', '') in ["ROUTE_0_GREETING", "ROUTE_1_SMALL_TALK"]
            
            # ABSOLUTE FIRST: strip any remaining markdown artifacts
            allow_links = plan.grounding.requires_sources or plan.grounding.video_policy != "none" if plan else False
            result_dict["answer"] = strip_all_markdown(answer.strip(), allow_links=allow_links)
            
            # ── Source-grounding guard: verify cited titles against actual support set ──
            if not is_light_route:
                result_dict["answer"] = _verify_source_grounding(
                    result_dict["answer"],
                    result_dict.get("retrieved") or [],
                )

            if is_light_route:
                logger.info(f"apply_final_polish: Light route {plan.route} — strip only")
                state_mgr.save_state()
                return result_dict
            
            # --- Memory Tracking (ASYNC — runs in background, doesn't block response) ---
            import threading
            def _async_memory_tracking(answer_text, state_mgr_ref):
                try:
                    tracking_prompt = f"""Analyze the following message. Extract:
1. Any specific actionable STEPS given (brief list).
2. Any specific RESOURCE/CONTENT recommended (title).
Output JSON only: {{"steps": ["step 1"], "recommendation": "title or null"}}
Message: {answer_text[:500]}"""
                    resp = rag.generate_chat_completion(
                        messages=[{"role": "system", "content": tracking_prompt}],
                        model=settings.ROUTER_MODEL,
                        temperature=0.0,
                        json_mode=True
                    )
                    tracking = json.loads(resp)
                    mem = state_mgr_ref.state.get("memory_loop", {})
                    if tracking.get("steps"):
                        mem["previous_steps_given"] = list(set(mem.get("previous_steps_given", []) + tracking["steps"]))
                    if tracking.get("recommendation"):
                        mem["last_recommendation"] = tracking["recommendation"]
                    state_mgr_ref.save_state()
                    logger.info(f"Async Memory Tracking Update: {tracking}")
                except Exception as e:
                    logger.error(f"Async memory tracking failed: {e}")
            
            # Fire and forget — response returns immediately
            threading.Thread(
                target=_async_memory_tracking, 
                args=(answer, state_mgr), 
                daemon=True
            ).start()
            
        return result_dict
    """
    Main Grounded-RAG Loop function.
    Funnels into ONE BEST CONTENT engine.
    """
    import json
    
    # --- Step 1: Fetch Profiles & State ---
    from backend.db import db
    from backend.services.conversation_state_manager import ConversationStateManager
    
    creator_row = _get_creator_profile_row(creator_id, [
        "style_fingerprint",
        "voice_profile",
        "identity_fingerprint",
        "research_summary",
        "soul_md",
        "decision_policy",
        "stronghold_json",
        "rhythm_profile_json",
        "creator_category",
        "persona_style_json",
        "controller_overrides_json",
        "search_mode",
    ])
    if not creator_row:
        raise Exception(f"Creator {creator_id} not found.")

    # Prioritize soul_md from creators table over legacy persona document
    persona = creator_row.get("soul_md") or rag.get_persona(creator_id)
    style_fingerprint = creator_row.get("style_fingerprint") or {}
    if isinstance(style_fingerprint, str):
        try:
            style_fingerprint = json.loads(style_fingerprint)
        except Exception:
            style_fingerprint = {}
    enabled_platforms = get_enabled_platforms_for_creator(creator_id)
    
    # Ensure thread_id is available
    if not thread_id:
        import uuid
        thread_id = str(uuid.uuid4())
        
    csm = ConversationStateManager(user_id=user_id, creator_id=creator_id, thread_id=thread_id)
    
    # Load Stronghold Config
    stronghold_config = creator_row.get("stronghold_json") or {}
    if isinstance(stronghold_config, str): stronghold_config = json.loads(stronghold_config)

    # --- Step 2: Classify + Route (GPT-4.1) ---
    # Launch embedding early (runs in parallel with classify_all)
    import concurrent.futures as _cf
    _early_emb_query = build_search_query(question, conversation_history)
    _early_emb_executor = _cf.ThreadPoolExecutor(max_workers=1)
    _early_emb_future = _early_emb_executor.submit(rag.create_embedding, _early_emb_query)

    logger.info("Pipeline Step 2: Classifying User Input...")
    user_state = classifiers.classify_all(question, conversation_history or [], creator_row)
    
    intent = user_state.get("intent", "unknown")
    if user_state.get("flags", {}).get("greeting_only_flag") or intent in ["greeting", "small_talk"]:
        intent = "greeting_only"
        user_state["intent"] = "greeting_only"
        user_state["request_type"] = "casual"

    creator_focus = (
        creator_row.get("creator_category")
        or user_state.get("primary_domain")
        or "general"
    )
    if should_soft_decline_external_live_fact(question, creator_focus, stronghold_config):
        logger.info("Out of domain live fact detected. Triggering soft redirect.")
        bridge_topic = recent_bridge_topic(conversation_history, question)
        answer = stronghold_guard.generate_boundary_message(
            creator_row.get("name") or creator_row.get("handle") or "the creator",
            persona,
            stronghold_config,
            question,
            recent_topic=bridge_topic,
            creator_focus=creator_focus,
            allow_handoff=False,
        )
        if not bridge_topic and "?" not in answer:
            answer = f"{answer} {get_bridge_question(creator_row, creator_focus)}"
        csm.state["last_router_meta"] = {
            "mode": "BOUNDARY",
            "domain_action": "OUT_OF_DOMAIN_REDIRECT",
            "user_state": user_state,
        }
        csm.save_state()
        return apply_final_polish({
            "answer": answer,
            "retrieved": [],
            "sources": [],
            "cards": [],
            "meta": {
                "domain_action": "OUT_OF_DOMAIN_REDIRECT",
                "suggested_mode": "BRIDGE",
                "bridge_topic": bridge_topic,
            },
        }, creator_row.get("rhythm_profile_json"), csm, mvc_score=0, plan=None)

    # --- Step 2.6: General Knowledge Redirect ---
    # Prevents the bot from acting as a generic ChatGPT wrapper.
    # If the question is a generic how-to / tutorial outside the creator's domains,
    # decline in character and pivot back to the conversation or creator's domain.
    if should_redirect_general_knowledge(
        question,
        stronghold_config.get("primary_domains", []),
        stronghold_config.get("secondary_domains", []),
    ):
        logger.info("General knowledge question outside creator domain. Triggering in-character redirect.")
        bridge_topic = recent_bridge_topic(conversation_history, question)
        answer = stronghold_guard.generate_boundary_message(
            creator_row.get("name") or creator_row.get("handle") or "the creator",
            persona,
            stronghold_config,
            question,
            recent_topic=bridge_topic,
            creator_focus=creator_focus,
            allow_handoff=False,
        )
        if not bridge_topic and "?" not in answer:
            answer = f"{answer} {get_bridge_question(creator_row, creator_focus)}"
        csm.state["last_router_meta"] = {
            "mode": "BOUNDARY",
            "domain_action": "GENERAL_KNOWLEDGE_REDIRECT",
            "user_state": user_state,
        }
        csm.save_state()
        return apply_final_polish({
            "answer": answer,
            "retrieved": [],
            "sources": [],
            "cards": [],
            "meta": {
                "domain_action": "GENERAL_KNOWLEDGE_REDIRECT",
                "suggested_mode": "BRIDGE",
                "bridge_topic": bridge_topic,
            },
        }, creator_row.get("rhythm_profile_json"), csm, mvc_score=0, plan=None)
    
    # --- Step 2.7: LLM-based Off-Domain Redirect ---
    # Catches everything the regex missed. The classifier already flagged off_domain_flag.
    if user_state.get("flags", {}).get("off_domain_flag") and intent != "greeting_only":
        logger.info("Classifier off_domain_flag=True. Triggering domain boundary redirect.")
        bridge_topic = recent_bridge_topic(conversation_history, question)
        answer = stronghold_guard.generate_boundary_message(
            creator_row.get("name") or creator_row.get("handle") or "the creator",
            persona,
            stronghold_config,
            question,
            recent_topic=bridge_topic,
            creator_focus=creator_focus,
            allow_handoff=False,
        )
        if not bridge_topic and "?" not in answer:
            answer = f"{answer} {get_bridge_question(creator_row, creator_focus)}"
        csm.state["last_router_meta"] = {
            "mode": "BOUNDARY",
            "domain_action": "OFF_DOMAIN_LLM_REDIRECT",
            "user_state": user_state,
        }
        csm.save_state()
        return apply_final_polish({
            "answer": answer,
            "retrieved": [],
            "sources": [],
            "cards": [],
            "meta": {
                "domain_action": "OFF_DOMAIN_LLM_REDIRECT",
                "suggested_mode": "BRIDGE",
                "bridge_topic": bridge_topic,
            },
        }, creator_row.get("rhythm_profile_json"), csm, mvc_score=0, plan=None)

    # Calculate MVC Score
    mvc_score = user_priority_service.calculate_mvc_score(user_state, csm.state.get("memory_loop", {}))
    logger.info(f"MVC Score: {mvc_score}")
    
    # --- Step 3: Memory Loop Update (ASYNC — doesn't block response) ---
    # Memory loop output isn't used by routing, retrieval, or rendering in this request.
    # Fire it in background to save ~1-2s latency.
    import threading
    def _async_memory_update(q, mem_state, u_state, hist, csm_ref):
        try:
            logger.info("Pipeline Step 3 (async): Updating Memory...")
            updated = memory_loop_service.extract_memory_updates(
                q, mem_state, u_state, history=hist
            )
            csm_ref.state["memory_loop"] = updated
            csm_ref.save_state()
            logger.info("Pipeline Step 3 (async): Memory updated OK")
        except Exception as e:
            logger.error(f"Async memory update failed: {e}")
    
    threading.Thread(
        target=_async_memory_update,
        args=(question, csm.state.get("memory_loop", {}), user_state, conversation_history, csm),
        daemon=True
    ).start()

    # --- Step 4: Stronghold Guard ---
    logger.info("Pipeline Step 4: Stronghold Check...")
    domain_action = stronghold_guard.calculate_domain_match(
        question, 
        stronghold_config, 
        user_state.get("primary_domain", "general")
    )
    
    if domain_action == "DECLINE_HANDOFF":
        logger.info("Stronghold: Triggering DECLINE_HANDOFF")
        answer = stronghold_guard.generate_boundary_message(
            creator_row["name"], persona, stronghold_config, question
        )
        # Suggest 2-3 other creators
        suggestions = db.execute_query("""
            SELECT id, name, handle, profile_picture_url
            FROM creators WHERE id != %s LIMIT 3
        """, (creator_id,))

        return apply_final_polish({
            "answer": answer,
            "retrieved": [],
            "sources": [],
            "cards": [],
            "meta": {
                "domain_action": "DECLINE_HANDOFF",
                "suggested_mode": "DECLINE",
                "suggestions": suggestions
            }
        }, creator_row.get("rhythm_profile_json"), csm, mvc_score=mvc_score, plan=None)

    # --- Step 4.5: Image Understanding / Identity Routing ---
    image_result = None
    if images:
        logger.info("Pipeline Step 4.5: Inspecting attached images...")
        image_result = image_identity_service.inspect(
            question=question,
            images=images,
            creator_id=creator_id,
            creator_profile=creator_row,
            allow_web=((creator_row.get("search_mode") or "hybrid") == "hybrid"),
        )
        if image_result.get("handled"):
            return apply_final_polish({
                "answer": image_result.get("answer", "I can tell you what I see, but I wouldn't want to guess who it is."),
                "retrieved": [image_result.get("support_chunk")] if image_result.get("support_chunk") else [],
                "sources": image_result.get("sources") or [],
                "cards": [],
                "meta": image_result.get("meta") or {},
            }, creator_row.get("rhythm_profile_json"), csm, mvc_score=mvc_score, plan=None)

    # --- Step 5: Personal / Biographical Routing ---
    rule_intent = classify_intent(question)
    route_q_type, route_topic, _ = decision_service.classify_question(question, rule_intent, conversation_history)
    creator_personal_detector = getattr(decision_service, "is_creator_personal_fact_question", None)
    route_creator_personal = bool(creator_personal_detector(question)) if callable(creator_personal_detector) else False
    route_evidence_plan = _build_evidence_plan(question, creator_row, conversation_history)
    route_personal_via_evidence = bool(
        route_evidence_plan
        and (
            route_evidence_plan.query_goal in {
                "entity_confirmation",
                "entity_overview",
                "entity_catalog_lookup",
                "availability_lookup",
                "timeline_lookup",
                "price_lookup",
                "stat_lookup",
                "current_stat_lookup",
            }
            or (
                route_evidence_plan.primary_world in {"creator_world", "live_world"}
                and route_evidence_plan.entity_subject
            )
        )
    )
    route_personal_from_classifier = bool(
        user_state.get("flags", {}).get("personal_question_flag") and route_creator_personal
    )
    route_personal_from_rules = bool(
        route_q_type == "personal_bio" or rule_intent == "personal_bio_question"
    )
    if route_personal_from_classifier or route_personal_from_rules or route_personal_via_evidence:
        logger.info("Pipeline Step 5: Routing personal factual question through PersonalBioService...")
        personal_result = personal_bio_service.handle_personal_question(
            user_id=user_id,
            creator_id=creator_id,
            question=question,
            voice_profile=creator_row.get("voice_profile") or {},
            creator_name=creator_row.get("name") or creator_row.get("handle") or "the creator",
            decision_policy=creator_row.get("decision_policy") or {},
            creator_profile=creator_row,
            conversation_history=conversation_history,
            allow_web=((creator_row.get("search_mode") or "hybrid") == "hybrid"),
        )
        personal_sources = []
        for idx, source in enumerate(personal_result.get("sources") or [], start=1):
            title = source.get("title") or source.get("text") or f"Source {idx}"
            url = source.get("url")
            if url or title:
                personal_sources.append({
                    "source_id": f"personal_{idx}",
                    "title": title[:140],
                    "url": url,
                    "snippet": source.get("text", "")[:240],
                    "platform": source.get("source", "profile"),
                })
        return apply_final_polish({
            "answer": personal_result.get("answer", "I haven't really talked about that publicly."),
            "retrieved": [],
            "sources": personal_sources,
            "cards": [],
            "meta": {
                "move": personal_result.get("move"),
                "confidence": personal_result.get("confidence"),
                "question_type": "personal_bio_question",
                "evidence_plan": personal_result.get("evidence_plan"),
                "fact_cache_hit": personal_result.get("fact_cache_hit"),
                "contradiction_report": personal_result.get("contradiction_report"),
            },
        }, creator_row.get("rhythm_profile_json"), csm, mvc_score=mvc_score, plan=None)

    # --- Step 5: Personal / Factual Check (Web Verify) ---
    verified_fact_data = None
    if route_personal_from_classifier or route_personal_from_rules:
        logger.info("Pipeline Step 5: Web Verifying Personal Question...")
        verified_fact_data = web_verify.verify_fact(
            question,
            creator_profile=creator_row,
            conversation_history=conversation_history,
        )
        if verified_fact_data["confidence"] < 0.4:
            # Low confidence fallback logic
            verified_fact_data["answer"] = "I'm not quite sure about that one myself, best to check my official sources."

    # --- Step 6: RAG Retrieval ---
    logger.info("Pipeline Step 6: Retrieval & Synthesis...")
    # Use existing recommendation/retrieval engine
    # BYPASS RAG for greetings to prevent hallucinations
    if intent in ["greeting", "greeting_only", "small_talk"]:
        logger.info("Bypassing Retrieval/Recommendation for greeting mode.")
        rec_result = {"best_candidate": None, "q_emb": [0.0]*1536}
    else:
        last_bot_msg = ""
        if conversation_history:
            for m in reversed(conversation_history):
                if m.get("role") == "assistant":
                    last_bot_msg = (m.get("content") or "").lower()
                    break
        explicit_link_request = needs_links(question)
        context_needs_video = _is_followup_resource_request(question, last_bot_msg)
        should_run_recommender = _should_run_resource_recommender(question, conversation_history, last_bot_msg)
        preferred_platforms = extract_requested_platforms(question, conversation_history)
        search_mode = creator_row.get("search_mode") or "hybrid"
        wants_link = explicit_link_request or context_needs_video
        video_intent_kws = ["video", "watch", "reel", "short", "clip", "tutorial"]
        is_video_request = any(kw in question.lower() for kw in video_intent_kws) or context_needs_video
        evidence_plan = route_evidence_plan or _build_evidence_plan(question, creator_row, conversation_history)
        if evidence_plan:
            log_evidence_plan(
                creator_id,
                question,
                evidence_plan,
                metadata={"service": "grounded_rag_sync", "phase": "pre_retrieval"},
            )
        search_question = (evidence_plan.resolved_query if evidence_plan and evidence_plan.resolved_query else question)

        # ── follow-up context resolution (non-streaming) ──
        if (conversation_history and len(search_question.split()) < 10
                and search_question == question):
            _prev_user = ""
            _prev_asst = ""
            for _m in reversed(conversation_history):
                if not _prev_asst and (_m.get("role") or "") == "assistant":
                    _prev_asst = (_m.get("content") or _m.get("text") or "").strip()
                elif _prev_asst and (_m.get("role") or "") == "user":
                    _prev_user = (_m.get("content") or _m.get("text") or "").strip()
                    break
            if _prev_user and _prev_asst:
                _ctx_snippet = (
                    " ".join(_prev_user.split()[:15])
                    + " "
                    + " ".join(_prev_asst.split()[:25])
                )
                search_question = f"{_ctx_snippet.strip()} {search_question}"

        search_engine = SearchDecisionEngine(creator_row)
        pre_search_decision = (
            search_engine.pre_retrieval_decision(question, conversation_history=conversation_history)
            if search_mode == "hybrid"
            else SearchDecision(False, "search_disabled", "Creator search mode disabled live search", "pre_retrieval", 1.0)
        )
        pre_web_results: List[Dict[str, Any]] = []
        pre_web_future = None
        pre_web_executor = None
        if search_mode == "hybrid" and pre_search_decision.should_search:
            log_search_decision(str(creator_id), question, pre_search_decision)
            if evidence_plan and evidence_plan.should_search_corpus:
                import concurrent.futures

                pre_web_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                pre_web_future = pre_web_executor.submit(
                    _run_live_web_search,
                    search_question,
                    creator_row,
                    conversation_history,
                    preferred_platforms,
                    is_video_request,
                    {"intent": "PUBLIC_CREATOR_FACT"},
                )
            else:
                pre_web_results = _run_live_web_search(
                    search_question,
                    creator_row,
                    conversation_history=conversation_history,
                    preferred_platforms=preferred_platforms,
                    is_video_request=is_video_request,
                    intent_metadata={"intent": "PUBLIC_CREATOR_FACT"},
                )
        if should_run_recommender:
            # Resolve pre-launched embedding for the recommender
            _pre_emb = None
            try:
                _pre_emb = _early_emb_future.result(timeout=5)
            except Exception as _emb_err:
                logger.warning(f"Early embedding failed, recommender will create its own: {_emb_err}")
            finally:
                _early_emb_executor.shutdown(wait=False)
            rec_result = recommend_one_content(
                user_id=user_id,
                creator_id=creator_id,
                user_message=question,
                conversation_history=conversation_history,
                creator_row=creator_row,
                q_emb=_pre_emb,
            )
            preferred_platforms = rec_result.get("resource_intent", {}).get("preferred_platforms") or preferred_platforms
        else:
            # Clean up early embedding executor when recommender is skipped
            _early_emb_executor.shutdown(wait=False)
            rec_result = {
                "best_candidate": None,
                "q_emb": None,
                "confidence": 0.0,
                "resource_intent": {"preferred_platforms": preferred_platforms},
            }
        resource_locked = should_run_recommender and _should_lock_single_resource(
            question,
            rec_result,
            preferred_platforms=preferred_platforms,
        )
    
    # Synthesis (Compact Support Pack)
    if intent in ["greeting", "greeting_only", "small_talk"]:
        support_set = []
    else:
        support_set = (
            _selected_recommendation_chunks(rec_result, preferred_platforms=preferred_platforms)
            if should_run_recommender
            else []
        )
        selected_resource_count = max(1, int((rec_result or {}).get("card_limit") or 1)) if should_run_recommender else 0
        if resource_locked:
            support_set = support_set or []
        elif not support_set:
            # Fallback to standard RAG if no recommendation
            q_emb = rec_result.get("q_emb")
            if not q_emb:
                # Use pre-launched embedding if available
                try:
                    q_emb = _early_emb_future.result(timeout=0.1)
                except Exception:
                    pass
            if not q_emb:
                try:
                    fallback_query = build_search_query(question, conversation_history)
                    q_emb = rag.create_embedding(fallback_query)
                except Exception as e:
                    logger.error(f"Fallback embedding build failed: {e}")
                    q_emb = [0.0] * 1536
            support_set = retrieve_candidates(creator_id, q_emb, 3, enabled_platforms=enabled_platforms)
        elif selected_resource_count > 1:
            support_set = support_set[:selected_resource_count]
        else:
            support_set = merge_support_sets(support_set, retrieve_candidates(creator_id, rec_result.get("q_emb"), 3, enabled_platforms=enabled_platforms), limit=4)

        if not resource_locked and _should_run_exact_text_match(question, conversation_history, wants_resource=should_run_recommender):
            exact_text_matches = retrieve_exact_text_matches(
                creator_id,
                question,
                limit=4,
                enabled_platforms=enabled_platforms,
            )
            if exact_text_matches:
                support_set = merge_support_sets(support_set, exact_text_matches, limit=4)
        support_set, resolved_entity = _inject_entity_graph_support(
            support_set,
            question,
            creator_row,
            conversation_history,
            evidence_plan=evidence_plan,
        )
            
        # --- NEW: Real-Time Web Search Fallback (Sync) ---
        # Check context: Did the bot just talk about a video/link?
        has_recommendable_ingested_resource = _has_recommendable_resource(
            rec_result,
            preferred_platforms=preferred_platforms,
        )
        has_linkable_ingested_resource = _support_set_has_linkable_ingested_resource(
            support_set,
            preferred_platforms=preferred_platforms,
            require_video=is_video_request,
        )
        
        no_online_fallback = None
        corpus_support_snapshot = list(support_set)
        if pre_web_future:
            try:
                pre_web_results = pre_web_future.result(timeout=12)
            except Exception as exc:
                logger.warning("Parallel pre-retrieval web search failed: %s", exc)
                pre_web_results = []
            finally:
                try:
                    pre_web_executor.shutdown(wait=False)
                except Exception:
                    pass
        web_results = list(pre_web_results)
        explicit_or_live_request_fallback = _should_block_on_web_fallback(
            question,
            conversation_history,
            wants_link=wants_link,
            is_video_request=is_video_request,
            support_set=support_set,
            has_recommendable_ingested_resource=has_recommendable_ingested_resource,
            has_linkable_ingested_resource=has_linkable_ingested_resource,
            search_mode=search_mode,
            images=bool(images),
        )
        top_support_score = _support_set_top_score(support_set)
        evidence_plan_post = _build_evidence_plan(
            question,
            creator_row,
            conversation_history,
            top_score=top_support_score,
            support_set=support_set,
            web_results=web_results,
        )
        if evidence_plan_post:
            log_evidence_plan(
                creator_id,
                question,
                evidence_plan_post,
                metadata={"service": "grounded_rag_sync", "phase": "post_retrieval"},
            )
        post_search_decision = (
            search_engine.post_retrieval_decision(
                question,
                support_set,
                top_support_score,
                conversation_history=conversation_history,
            )
            if search_mode == "hybrid" and not pre_search_decision.should_search
            else None
        )
        if post_search_decision and post_search_decision.should_search:
            log_search_decision(str(creator_id), question, post_search_decision)
        needs_fallback = explicit_or_live_request_fallback or bool(post_search_decision and post_search_decision.should_search)
        active_plan = evidence_plan_post or evidence_plan
        entity_memory_query = bool(active_plan and active_plan.query_goal in {"entity_confirmation", "entity_overview"})
        fact_search_requested = (
            not entity_memory_query
            and (_decision_is_creator_public_fact(pre_search_decision) or _decision_is_creator_public_fact(post_search_decision))
        )
        contradiction_report = detect_evidence_contradiction(
            question,
            corpus_chunks=corpus_support_snapshot,
            web_results=web_results,
        )

        if web_results:
            support_set = _inject_live_web_results(support_set, web_results)
        elif fact_search_requested:
            # No web results found. Instead of returning a generic fallback
            # immediately, let the main LLM handle the question — it has
            # domain lock rules and RAG context to answer or redirect properly.
            logger.info("[LATENCY] fact_search_requested but no web results in sync path; falling through to main LLM.")

        if needs_fallback and not web_results:
            logger.info("Triggering live web search fallback for explicit live/source request.")
            import concurrent.futures
            intent_metadata = {"intent": "EVENT_PUBLIC_FACTS"} if needs_fresh_public_web_search(question, conversation_history) else None
            if _decision_is_creator_public_fact(pre_search_decision) or _decision_is_creator_public_fact(post_search_decision):
                intent_metadata = {"intent": "PUBLIC_CREATOR_FACT"}
            
            # Explicit live/source requests can block on web results because the
            # user asked for current facts or trustworthy links.
            if not support_set:
                try:
                    web_results = _run_live_web_search(
                        search_question,
                        creator_row,
                        conversation_history=conversation_history,
                        preferred_platforms=preferred_platforms,
                        is_video_request=is_video_request,
                        intent_metadata=intent_metadata,
                    )
                except Exception as e:
                    logger.error(f"Sync web search failed: {e}")
            else:
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    search_future = executor.submit(
                        _run_live_web_search,
                        search_question,
                        creator_row,
                        conversation_history,
                        preferred_platforms,
                        is_video_request,
                        intent_metadata,
                    )
                    
                    try:
                        web_results = search_future.result(timeout=10)
                    except concurrent.futures.TimeoutError:
                        logger.warning("Blocking web search timed out, proceeding with existing RAG results.")

            if web_results:
                support_set = _inject_live_web_results(support_set, web_results)
            elif wants_link:
                no_online_fallback = _build_not_online_fallback(question, creator_row.get("name") or creator_row.get("handle") or "the creator", conversation_history, kind="video" if (is_video_request if 'is_video_request' in locals() else False) else "source", style_fingerprint=style_fingerprint)
        elif wants_link and not has_recommendable_ingested_resource:
            no_online_fallback = _build_not_online_fallback(
                question,
                creator_row.get("name") or creator_row.get("handle") or "the creator",
                conversation_history,
                kind="video" if is_video_request else "source",
                style_fingerprint=style_fingerprint,
            )

        if image_result and image_result.get("support_chunk"):
            support_set = [image_result["support_chunk"], *support_set]
        support_set = shape_support_set(question, support_set, limit=4)

    if _should_force_resource_fallback(
        no_online_fallback,
        wants_link=wants_link if 'wants_link' in locals() else False,
        has_linkable_ingested_resource=has_linkable_ingested_resource if 'has_linkable_ingested_resource' in locals() else False,
        web_results=web_results if 'web_results' in locals() else [],
    ):
        logger.info("Using deterministic resource fallback with no safe link to attach.")
        answer = no_online_fallback
        csm.state["last_router_meta"] = {
            "mode": "RESOURCE_FALLBACK",
            "domain_action": "NO_LINKABLE_RESOURCE",
            "user_state": user_state,
        }
        csm.save_state()
        return apply_final_polish({
            "answer": answer,
            "retrieved": support_set,
            "sources": [],
            "cards": [],
            "meta": {
                "resource_fallback": True,
                "reason": "no_linkable_resource",
            },
        }, creator_row.get("rhythm_profile_json"), csm, mvc_score=mvc_score, plan=None)

    # --- Step 7: PASS 1 - Interaction Planning (UCR Classifier + Planner) ---

    logger.info("Pipeline Step 7: UCR Classification + Interaction Planning...")
    
    plan_obj = interaction_engine.build_interaction_plan(
        question, 
        conversation_history or [], 
        creator_row, 
        support_set
    )
    
    logger.info(f"UCR Route: {plan_obj.route} | Mode: {plan_obj.mode} | Stage: {plan_obj.stage} | Routing: {plan_obj.routing}")

    # --- Step 8: PASS 2 - Persona Rendering (Route-Aware) ---
    logger.info(f"Pipeline Step 8: Rendering ({plan_obj.route})...")
    
    answer = interaction_engine.render_response(
        plan_obj, 
        creator_row, 
        support_set,
        creator_id,
        user_id,
        thread_id,
        user_name=user_name,
        user_msg=question,
        persona=persona,
        history=conversation_history or [],
        user_preferences=user_preferences
    )
    if 'no_online_fallback' in locals() and no_online_fallback and (
        not (answer or '').strip() or _contains_placeholder_link_artifacts(answer)
    ):
        answer = no_online_fallback
    
    # Log the turn
    interaction_engine.log_turn(
        creator_id,
        user_id,
        thread_id,
        "assistant",
        answer,
        plan_obj,
        len(support_set) > 0,
        len(support_set)
    )
    
    # Store in Mem0 Persistent Memory
    try:
        interaction_engine.store_interaction(str(creator_id), str(user_id), str(thread_id), question, answer)
    except Exception as e:
        logger.error(f"Mem0 store failed: {e}")

    quality_report = score_response_quality(
        question,
        answer,
        support_set,
        creator_markers=_quality_markers_for_creator(creator_row),
    )
    if quality_report.get("grade") in {"fair", "weak"}:
        logger.warning(
            "Grounded answer quality for creator_id=%s returned %s (%s)",
            creator_id,
            quality_report.get("grade"),
            ", ".join(quality_report.get("penalties") or []) or "no penalties",
        )

    # gen_debug for compatibility (includes UCR route info)
    gen_debug = {
        "plan": plan_obj.dict(),
        "route": plan_obj.route,
        "routing": plan_obj.routing,
        "mvc_score": mvc_score,
        "quality_report": quality_report,
        "evidence_plan": evidence_plan_post.to_dict() if 'evidence_plan_post' in locals() and evidence_plan_post else (evidence_plan.to_dict() if 'evidence_plan' in locals() and evidence_plan else None),
        "contradiction_report": contradiction_report if 'contradiction_report' in locals() else None,
    }

    # --- Step 9: Video Recommendation (ONE ONLY) ---
    card = []
    image_turn_active = bool(images or (image_result and image_result.get("support_chunk")))
    # Check if RAG support_set has substantive ingested content (not live web)
    _has_substantive_rag = bool(
        support_set
        and any(
            not str(c.get("content") or "").startswith("[LIVE WEB SEARCH RESULT]")
            and (c.get("url") or (c.get("source_ref") or {}).get("canonical_url"))
            for c in (support_set or [])[:6]
        )
    )
    _should_try_cards = (
        plan_obj.grounding.video_policy in ["one_if_helpful", "forced"]
        or wants_link
        or is_video_request
        or _has_substantive_rag
    )
    if _should_try_cards and not image_turn_active:
        card = _build_response_cards(
            rec_result,
            support_set,
            preferred_platforms=(rec_result.get("resource_intent", {}) or {}).get("preferred_platforms"),
            question=question,
            answer_text=answer,
        )

    # --- Step 9b: Card rescue — if the answer mentions a video title from the
    # support set but no card was built (e.g. video_policy was "none"), attach it.
    if not card and not image_turn_active:
        card = _rescue_cards_from_answer(answer, support_set)

    recommendation_event_id = None
    if (rec_result or {}).get("best_candidate"):
        recommendation_event_id = recommendation_feedback_service.log_impression(
            user_id=user_id,
            creator_id=creator_id,
            thread_id=thread_id,
            query=question,
            best_candidate=(rec_result or {}).get("best_candidate"),
            alternate_candidates=(rec_result or {}).get("alternate_candidates") or [],
            resource_intent=(rec_result or {}).get("resource_intent") or {},
            confidence=(rec_result or {}).get("confidence"),
            query_variants=(rec_result or {}).get("query_variants") or [],
            retrieval_debug=(rec_result or {}).get("retrieval_debug") or {},
        )

    # --- Step 10: Persist + Return ---
    logger.info("Pipeline Step 10: Finalizing Output...")
    
    # Update CSM with router metadata
    csm.state["last_router_meta"] = {
        "mode": plan_obj.mode,
        "domain_action": domain_action or "GENERAL_CHAT",
        "user_state": user_state
    }
    csm.save_state()

    # --- Step 11: Background Memory Update (Long-term Facts) ---
    try:
        memory_service.update_memory(user_id, creator_id, thread_id, question)
    except Exception as e:
        logger.error(f"Background memory update failed: {e}")

    gen_debug["recommendation_feedback_event_id"] = recommendation_event_id
    gen_debug["recommendation_query_variants"] = (rec_result or {}).get("query_variants") if rec_result else None
    gen_debug["recommendation_retrieval_debug"] = (rec_result or {}).get("retrieval_debug") if rec_result else None

    return apply_final_polish({
        "answer": answer,
        "retrieved": support_set,
        "sources": build_source_list(support_set),
        "citations": build_inline_citations(
            support_set,
            question=question,
            answer_text=answer,
        ),
        "cards": card,
        "debug": gen_debug,
        "meta": {
            "gen_debug": gen_debug,
            "plan_obj": plan_obj.dict() if plan_obj else None,
            "quality_report": quality_report,
            "evidence_plan": evidence_plan_post.to_dict() if 'evidence_plan_post' in locals() and evidence_plan_post else (evidence_plan.to_dict() if 'evidence_plan' in locals() and evidence_plan else None),
            "contradiction_report": contradiction_report if 'contradiction_report' in locals() else None,
            "recommendation_feedback_event_id": recommendation_event_id,
            "recommendation_query_variants": (rec_result or {}).get("query_variants") if rec_result else None,
            "recommendation_retrieval_debug": (rec_result or {}).get("retrieval_debug") if rec_result else None,
        }
    }, creator_row.get("rhythm_profile_json"), csm, mvc_score=mvc_score, plan=plan_obj)

import re

def _is_social_request(user_text: str) -> Optional[str]:
    """Deterministically identifies user requests for social profiles."""
    text = user_text.lower()
    if not any(w in text for w in ["what is", "whats", "what's", "where", "link", "handle", "profile", "social"]):
        return None
        
    if re.search(r'\b(instagram|ig|insta)\b', text): return "instagram"
    if re.search(r'\b(youtube|yt|channel)\b', text): return "youtube"
    if re.search(r'\b(tiktok)\b', text): return "tiktok"
    if re.search(r'\b(twitter|x)\b', text): return "x"
    if re.search(r'\b(linkedin)\b', text): return "linkedin"
    if re.search(r'\b(facebook|fb)\b', text): return "facebook"
    if re.search(r'\b(website|site|domain)\b', text): return "website"
    
    return None

async def grounded_rag_stream(
    creator_id: int,
    question: str,
    thread_id: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    top_k: int = K_FINAL,
    user_preferences: Optional[Dict[str, Any]] = None,
    user_name: Optional[str] = None,
    user_id: int = 1,
):
    """
    ZERO-WAIT streaming version of the Grounded-RAG pipeline.
    Optimized for sub-500ms TTFT by early-routing and parallel execution.
    """
    from backend.db import db
    import asyncio

    conversation_history = conversation_history or []
    resolved_question = decision_service.resolve_followup_question(question, conversation_history)
    if resolved_question != question:
        logger.info("Resolved short follow-up '%s' to '%s'", question, resolved_question)
        question = resolved_question
    
    # 1. Deterministic Routing (Instant) - MUST BE FIRST
    route = interaction_engine.classify_route(question, conversation_history)
    
    # Early heartbeat yield to drop TTFB to <100ms
    yield " "

    # FAST-PATH: Social Intent Bypass
    social_key = _is_social_request(question)
    if social_key:
        creator_row = await asyncio.to_thread(
            db.execute_one, 
            "SELECT name, handle, platform_configs FROM creators WHERE id = %s", 
            (creator_id,)
        )
        if not creator_row:
            yield "I don't have a verified profile for this creator."
            return
            
        pc = creator_row.get("platform_configs") or {}
        if isinstance(pc, str): pc = json.loads(pc)
        plat = pc.get(social_key, {})
        url = plat.get("verified_url") or plat.get("url")
        
        creator_name = creator_row.get("name") or creator_row.get("handle") or "the creator"

        if url:
             card_html = f"Here is the verified link you requested:\n\n[{social_key.title()}]({url})"
             yield card_html
             return
             
        # Fallback to research provider SOCIAL_LOOKUP
        if social_key != "website":
            from backend.services.research_provider import GeminiResearchProvider
            rp = GeminiResearchProvider()
            query = f"site:{social_key}.com \"{creator_name}\" official channel profile"
            
            # Execute search in thread to avoid blocking loop
            candidates = await asyncio.to_thread(
                rp.search, 
                query=query, 
                creator_profile=creator_row, 
                resource_type="any", 
                intent_metadata={"intent": "SOCIAL_LOOKUP"}
            )
            
            if candidates:
                best_match = candidates[0]
                best_url = best_match.get("url")
                if best_url:
                    from backend.services.identity_manager import _grade_social_identity
                    confidence, reasons = _grade_social_identity(
                        best_url, social_key, creator_row, 
                        best_match.get("title", ""), best_match.get("snippet", "")
                    )
                    
                    existing_conf = plat.get("social_confidence", 0.0)
                    is_user_provided = plat.get("social_source") == "user_provided"
                    
                    if confidence >= 0.85 and not is_user_provided and (confidence > existing_conf + 0.15 or not plat.get("verified_url")):
                        # Update DB configs 
                        plat["verified_url"] = best_url
                        plat["social_source"] = "verified_search"
                        plat["social_confidence"] = confidence
                        pc[social_key] = plat
                        await asyncio.to_thread(
                            db.execute_update, 
                            "UPDATE creators SET platform_configs = %s WHERE id = %s", 
                            (json.dumps(pc), creator_id)
                        )
                        
                        card_html = f"I found the verified link for you:\n\n[{social_key.title()}]({best_url})"
                        yield card_html
                        return
                    elif confidence >= 0.6:
                        import logging
                        logging.getLogger(__name__).info(f"SOCIAL_LOOKUP: platform={social_key} confidence={confidence:.2f} action=low_confidence_not_saved reasons=[{reasons}]")
                    else:
                        import logging
                        logging.getLogger(__name__).info(f"SOCIAL_LOOKUP: platform={social_key} confidence={confidence:.2f} action=discard reasons=[{reasons}]")

        yield f"I don't currently have a verified {social_key.title()} link saved for {creator_name}."
        return    
    # 2. Launch Basic Metadata Tasks (Fast/Async)
    creator_task = asyncio.create_task(asyncio.to_thread(
        _get_creator_profile_row,
        creator_id,
        [
            "creator_category",
            "rhythm_profile_json",
            "identity_fingerprint",
            "research_summary",
            "soul_md",
            "style_fingerprint",
            "voice_profile",
            "decision_policy",
            "search_mode",
            "stronghold_json",
        ],
    ))
    creator_row = None
    
    # 3. Handle Context Gathering (Skip embeddings for greetings)
    support_set = []
    mems = []
    no_online_fallback = None
    rule_intent = classify_intent(question)
    route_q_type, route_topic, _ = decision_service.classify_question(question, rule_intent, conversation_history)

    # LATENCY: Launch embedding early for ROUTE_2_TASK so it runs in parallel
    # with domain checks instead of waiting for them to finish first.
    early_embedding_task = None
    if route == "ROUTE_2_TASK":
        question_for_search = question
        if conversation_history:
            last_msg = ""
            for m in reversed(conversation_history):
                if m and m.get("role") != "user":
                    last_msg = m.get("content", "")
                    break
            if len(question.split()) < 10 and last_msg:
                last_snippet = " ".join(last_msg.split()[:30])
                question_for_search = f"Context: {last_snippet} | Query: {question}"
        early_embedding_task = rag.get_async_client().embeddings.create(
            input=question_for_search,
            model="text-embedding-3-small"
        )

    if detect_external_live_fact_topic(question):
        creator_row = await creator_task
        if not creator_row:
            yield "I couldn't find information about that creator."
            return
        persona = creator_row.get("soul_md") or ""
        stronghold_config = creator_row.get("stronghold_json") or {}
        if isinstance(stronghold_config, str):
            stronghold_config = json.loads(stronghold_config)
        creator_focus = creator_row.get("creator_category") or "general"
        if should_soft_decline_external_live_fact(question, creator_focus, stronghold_config):
            bridge_topic = recent_bridge_topic(conversation_history, question)
            answer = await asyncio.to_thread(
                stronghold_guard.generate_boundary_message,
                creator_row.get("name") or creator_row.get("handle") or "the creator",
                persona,
                stronghold_config,
                question,
                bridge_topic,
                creator_focus,
                False,
            )
            if not bridge_topic and "?" not in answer:
                answer = f"{answer} {get_bridge_question(creator_row, creator_focus)}"
            yield answer
            return

    # --- General Knowledge Redirect (streaming path) ---
    # Prevents the bot from acting as a generic ChatGPT wrapper for off-domain how-to questions.
    from backend.services.out_of_domain_rules import detect_general_knowledge_topic
    if detect_general_knowledge_topic(question):
        creator_row = creator_row or await creator_task
        if creator_row:
            _sc = creator_row.get("stronghold_json") or {}
            if isinstance(_sc, str):
                _sc = json.loads(_sc)
            _persona = creator_row.get("soul_md") or ""
            _focus = creator_row.get("creator_category") or "general"
            if should_redirect_general_knowledge(
                question,
                _sc.get("primary_domains", []),
                _sc.get("secondary_domains", []),
            ):
                bridge_topic = recent_bridge_topic(conversation_history, question)
                answer = await asyncio.to_thread(
                    stronghold_guard.generate_boundary_message,
                    creator_row.get("name") or creator_row.get("handle") or "the creator",
                    _persona,
                    _sc,
                    question,
                    bridge_topic,
                    _focus,
                    False,
                )
                if not bridge_topic and "?" not in answer:
                    answer = f"{answer} {get_bridge_question(creator_row, _focus)}"
                yield answer
                return

    # --- LLM-based Off-Domain Check (streaming path) ---
    # Catches off-domain questions that regex patterns missed.
    # Only runs for ROUTE_2_TASK (skips greetings/small talk).
    if route == "ROUTE_2_TASK":
        creator_row = creator_row if (creator_row is not None) else (await creator_task if creator_task else None)
        if creator_row:
            _sc = creator_row.get("stronghold_json") or {}
            if isinstance(_sc, str):
                _sc = json.loads(_sc)
            _focus = creator_row.get("creator_category") or "general"
            if _focus != "general":
                # Skip expensive LLM domain check for questions obviously about the creator
                _q_lower = (question or "").lower()
                _word_count = len(_q_lower.split())
                _is_obviously_on_domain = bool(
                    re.search(r"\b(you|your|u|ur)\b", _q_lower)
                    or re.search(r"\b(book|course|program|podcast|video|reel|content|channel|product|service|offer|coaching)\b", _q_lower)
                    or re.search(r"\b(recommend|advice|tip|help|opinion|thought|think|suggest|strategy|plan|approach)\b", _q_lower)
                    or re.search(r"\b(how do i|what should i|can i|should i|where do i|how can i|how to|what do you)\b", _q_lower)
                    or re.search(r"\b(struggling|stuck|confused|lost|nervous|anxious|scared|motivated|overwhelmed|frustrated)\b", _q_lower)
                    or re.search(r"\b(best|worst|top|first|beginner|start|starting|getting started)\b", _q_lower)
                    or re.search(r"\b(routine|workout|diet|nutrition|training|bulk|cut|macro|calorie|protein|cardio|lift|squat|deadlift|bench)\b", _q_lower)
                    or re.search(r"\b(trade|trading|stock|option|crypto|forex|market|invest|portfolio|position|entry|exit)\b", _q_lower)
                    or re.search(r"\b(business|revenue|income|money|profit|sales|client|customer|funnel|launch|brand|niche|scale)\b", _q_lower)
                    or re.search(r"\b(mindset|discipline|consistency|goals|habits|accountability|focus|confidence)\b", _q_lower)
                    # Short messages (<=6 words) that aren't trivia-like are almost always in-domain follow-ups
                    or (_word_count <= 6 and not re.search(r"\b(capital|president|who invented|what year|which country|population)\b", _q_lower))
                    # Conversational continuation signals
                    or bool(conversation_history and _word_count <= 12 and not re.search(r"\b(how to cook|recipe|weather|score|game)\b", _q_lower))
                )
                if not _is_obviously_on_domain:
                    _name = creator_row.get("name") or creator_row.get("handle") or "the creator"
                    _primary = _sc.get("primary_domains", []) or []
                    _secondary = _sc.get("secondary_domains", []) or []
                    _domain_list = ", ".join(list(_primary) + list(_secondary) + [_focus])
                    try:
                        _check_prompt = (
                            f"Creator: {_name}\n"
                            f"Expertise domains: {_domain_list}\n"
                            f"User question: \"{question}\"\n\n"
                            "Does this question fall WITHIN or DIRECTLY RELATE to the creator's expertise domains?\n"
                            "Rules:\n"
                            "- YES only if the question is clearly about one of the listed domains.\n"
                            "- NO if it is a generic how-to, tutorial, trivia, or factual question unrelated to those domains.\n"
                            "- Greetings and personal questions about the creator = YES.\n"
                            "- When in doubt, answer NO.\n"
                            "Answer YES or NO only."
                        )
                        _check_resp = await asyncio.to_thread(
                            rag.generate_chat_completion,
                            messages=[{"role": "user", "content": _check_prompt}],
                            model=settings.MODEL_CLASSIFICATION,
                            temperature=0.0,
                            max_tokens=5,
                        )
                        if _check_resp.strip().upper().startswith("NO"):
                            logger.info("Streaming LLM domain check: off-domain. Triggering redirect.")
                            _persona = creator_row.get("soul_md") or ""
                            bridge_topic = recent_bridge_topic(conversation_history, question)
                            answer = await asyncio.to_thread(
                                stronghold_guard.generate_boundary_message,
                                _name, _persona, _sc, question, bridge_topic, _focus, False,
                            )
                            if not bridge_topic and "?" not in answer:
                                answer = f"{answer} {get_bridge_question(creator_row, _focus)}"
                            yield answer
                            return
                    except Exception as e:
                        logger.warning(f"Streaming off-domain LLM check failed (non-blocking): {e}")

    route_evidence_plan = None
    route_personal_via_evidence = False
    if creator_task:
        try:
            creator_preview = await creator_task
            if creator_preview:
                route_evidence_plan = _build_evidence_plan(question, creator_preview, conversation_history)
                route_personal_via_evidence = bool(
                    route_evidence_plan
                    and (
                        route_evidence_plan.query_goal in {
                            "entity_confirmation",
                            "entity_overview",
                            "entity_catalog_lookup",
                            "availability_lookup",
                            "timeline_lookup",
                            "price_lookup",
                            "stat_lookup",
                            "current_stat_lookup",
                        }
                        or (
                            route_evidence_plan.primary_world in {"creator_world", "live_world"}
                            and route_evidence_plan.entity_subject
                        )
                    )
                )
            creator_task = asyncio.create_task(asyncio.sleep(0, result=creator_preview))
        except Exception:
            pass

    route_personal_from_rules = bool(route_q_type == "personal_bio" or rule_intent == "personal_bio_question")
    if route_personal_from_rules or route_personal_via_evidence:
        creator_row = await creator_task
        if not creator_row:
            yield "I couldn't find information about that creator."
            return
        personal_result = await asyncio.to_thread(
            personal_bio_service.handle_personal_question,
            user_id=user_id,
            creator_id=creator_id,
            question=question,
            voice_profile=creator_row.get("voice_profile") or {},
            creator_name=creator_row.get("name") or creator_row.get("handle") or "the creator",
            decision_policy=creator_row.get("decision_policy") or {},
            creator_profile=creator_row,
            conversation_history=conversation_history,
            allow_web=((creator_row.get("search_mode") or "hybrid") == "hybrid"),
        )
        yield personal_result.get("answer", "I haven't really talked about that publicly.")
        return
    
    if route == "ROUTE_2_TASK":
        # Full RAG Route: Needs Embeddings
        # Use the early-launched embedding task instead of creating a new one
        embedding_task = early_embedding_task
        if embedding_task is None:
            question_for_search = question
            if conversation_history:
                last_msg = ""
                for m in reversed(conversation_history):
                    if m and m.get("role") != "user":
                        last_msg = m.get("content", "")
                        break
                if len(question.split()) < 10 and last_msg:
                    last_snippet = " ".join(last_msg.split()[:30])
                    question_for_search = f"Context: {last_snippet} | Query: {question}"
            embedding_task = rag.get_async_client().embeddings.create(
                input=question_for_search,
                model="text-embedding-3-small"
            )

        # Await metadata while embedding is in flight
        creator_row, embedding_resp = await asyncio.gather(
            creator_task, embedding_task
        )
        if not creator_row:
            yield "I'm sorry, I couldn't find the profile for this creator."
            return
            
        persona = creator_row.get("soul_md") or ""
        style_fingerprint = creator_row.get("style_fingerprint") or {}
        if isinstance(style_fingerprint, str):
            try:
                style_fingerprint = json.loads(style_fingerprint)
            except Exception:
                style_fingerprint = {}
        
        q_emb = embedding_resp.data[0].embedding

        last_bot_msg = ""
        if conversation_history:
            for m in reversed(conversation_history):
                if m and m.get("role") == "assistant":
                    last_bot_msg = (m.get("content") or "").lower()
                    break

        explicit_link_request = needs_links(question)
        context_needs_video = _is_followup_resource_request(question, last_bot_msg)
        should_run_recommender = _should_run_resource_recommender(question, conversation_history, last_bot_msg)
        preferred_platforms = extract_requested_platforms(question, conversation_history)
        search_mode = creator_row.get("search_mode") or "hybrid"
        wants_link = explicit_link_request or context_needs_video
        video_intent_kws = ['video', 'watch', 'reel', 'short', 'clip', 'tutorial', 'recommend', 'reccomend']
        is_video_request = any(kw in question.lower() for kw in video_intent_kws) or context_needs_video
        evidence_plan = route_evidence_plan or _build_evidence_plan(question, creator_row, conversation_history)
        if evidence_plan:
            log_evidence_plan(
                creator_id,
                question,
                evidence_plan,
                metadata={"service": "grounded_rag_stream", "phase": "pre_retrieval"},
            )
        search_question = (evidence_plan.resolved_query if evidence_plan and evidence_plan.resolved_query else question)

        # Contextualize short follow-up queries with the previous exchange so
        # that sparse text matching and web search see the conversational topic.
        # The early embedding already gets this context (question_for_search),
        # but search_question was left bare — causing retrieval to miss the
        # referent.  e.g. "who was the lucky one" after discussing relationship
        # needs the relationship context to avoid retrieving "luck in business".
        if (
            conversation_history
            and len(search_question.split()) < 10
            and search_question == question  # not already rewritten
        ):
            _prev_user = ""
            _prev_asst = ""
            for _m in reversed(conversation_history):
                if not _prev_asst and (_m.get("role") or "") == "assistant":
                    _prev_asst = (_m.get("content") or _m.get("text") or "").strip()
                elif _prev_asst and (_m.get("role") or "") == "user":
                    _prev_user = (_m.get("content") or _m.get("text") or "").strip()
                    break
            if _prev_user and _prev_asst:
                _ctx_snippet = " ".join(_prev_user.split()[:15]) + " " + " ".join(_prev_asst.split()[:25])
                search_question = f"{_ctx_snippet.strip()} {search_question}"

        search_engine = SearchDecisionEngine(creator_row)
        pre_search_decision = (
            search_engine.pre_retrieval_decision(question, conversation_history=conversation_history)
            if search_mode == "hybrid"
            else SearchDecision(False, "search_disabled", "Creator search mode disabled live search", "pre_retrieval", 1.0)
        )
        web_query = build_live_search_query(
            search_question,
            conversation_history,
            creator_name=creator_row.get("name") or creator_row.get("handle"),
            preferred_platforms=preferred_platforms,
            require_video=is_video_request,
        )
        intent_metadata = {"intent": "EVENT_PUBLIC_FACTS"} if needs_fresh_public_web_search(question, conversation_history) else None
        if _decision_is_creator_public_fact(pre_search_decision):
            intent_metadata = {"intent": "PUBLIC_CREATOR_FACT"}
        speculative_web_task = None
        if search_mode == "hybrid" and (
            pre_search_decision.should_search or _should_speculate_live_search(
                question,
                conversation_history,
                explicit_link_request=explicit_link_request,
                context_needs_video=context_needs_video,
                should_run_recommender=should_run_recommender,
            )
        ):
            if pre_search_decision.should_search:
                log_search_decision(str(creator_id), question, pre_search_decision)
            speculative_web_task = asyncio.create_task(
                asyncio.to_thread(
                    _run_live_web_search,
                    search_question,
                    creator_row,
                    conversation_history,
                    preferred_platforms,
                    is_video_request,
                    intent_metadata,
                )
            )

        # Launch Search Tasks (Parallel)
        mems_task = interaction_engine.memory.search_with_embedding_async(
            str(creator_id), str(user_id), str(thread_id or "new"), q_emb
        )
        direct_support_task = asyncio.to_thread(
            retrieve_candidates,
            creator_id,
            q_emb,
            3,
            1.15,
            get_enabled_platforms_for_creator(creator_id),
        )
        if should_run_recommender:
            rec_task = asyncio.to_thread(
                recommend_one_content,
                user_id,
                creator_id,
                question,
                conversation_history,
                creator_row,
                False,
                q_emb,
            )
            rec_result, mems, direct_support = await asyncio.gather(rec_task, mems_task, direct_support_task)
            preferred_platforms = rec_result.get("resource_intent", {}).get("preferred_platforms") or preferred_platforms
        else:
            rec_result = {
                "best_candidate": None,
                "q_emb": q_emb,
                "confidence": 0.0,
                "resource_intent": {"preferred_platforms": preferred_platforms},
            }
            mems, direct_support = await asyncio.gather(mems_task, direct_support_task)

        resource_locked = should_run_recommender and _should_lock_single_resource(
            question,
            rec_result,
            preferred_platforms=preferred_platforms,
        )
        support_set = (
            _selected_recommendation_chunks(rec_result, preferred_platforms=preferred_platforms)
            if should_run_recommender
            else []
        )
        selected_resource_count = max(1, int((rec_result or {}).get("card_limit") or 1)) if should_run_recommender else 0
        if resource_locked:
            support_set = support_set or []
        elif selected_resource_count > 1:
            support_set = support_set[:selected_resource_count]
        else:
            support_set = merge_support_sets(support_set, direct_support, limit=4) if support_set else (direct_support or [])
        if not support_set:
            support_set = await asyncio.to_thread(retrieve_candidates, creator_id, q_emb, 3)
        if not resource_locked and _should_run_exact_text_match(question, conversation_history, wants_resource=should_run_recommender):
            exact_text_matches = await asyncio.to_thread(
                retrieve_exact_text_matches,
                creator_id,
                question,
                4,
                None,
            )
            if exact_text_matches:
                support_set = merge_support_sets(support_set, exact_text_matches, limit=4)
        support_set, resolved_entity = _inject_entity_graph_support(
            support_set,
            question,
            creator_row,
            conversation_history,
            evidence_plan=evidence_plan,
        )
        
        # --- Optimized Real-Time Web Search Fallback ---
        import time as _time
        _t_search_start = _time.time()

        has_recommendable_ingested_resource = _has_recommendable_resource(
            rec_result,
            preferred_platforms=preferred_platforms,
        )
        has_linkable_ingested_resource = _support_set_has_linkable_ingested_resource(
            support_set,
            preferred_platforms=preferred_platforms,
            require_video=is_video_request,
        )
        
        # OPTIMIZATION: If RAG returned no results or very few, launch web search
        # IN PARALLEL with sufficiency check to save ~1.5s
        needs_fallback = False
        web_results = []
        explicit_or_live_request_fallback = _should_block_on_web_fallback(
            question,
            conversation_history,
            wants_link=wants_link,
            is_video_request=is_video_request,
            support_set=support_set,
            has_recommendable_ingested_resource=has_recommendable_ingested_resource,
            has_linkable_ingested_resource=has_linkable_ingested_resource,
            search_mode=search_mode,
        )
        top_support_score = _support_set_top_score(support_set)
        evidence_plan_post = _build_evidence_plan(
            question,
            creator_row,
            conversation_history,
            top_score=top_support_score,
            support_set=support_set,
            web_results=web_results,
        )
        if evidence_plan_post:
            log_evidence_plan(
                creator_id,
                question,
                evidence_plan_post,
                metadata={"service": "grounded_rag_stream", "phase": "post_retrieval"},
            )
        post_search_decision = (
            search_engine.post_retrieval_decision(
                question,
                support_set,
                top_support_score,
                conversation_history=conversation_history,
            )
            if search_mode == "hybrid" and not pre_search_decision.should_search
            else None
        )
        if post_search_decision and post_search_decision.should_search:
            log_search_decision(str(creator_id), question, post_search_decision)
        needs_fallback = explicit_or_live_request_fallback or bool(post_search_decision and post_search_decision.should_search)
        active_plan = evidence_plan_post or evidence_plan
        entity_memory_query = bool(active_plan and active_plan.query_goal in {"entity_confirmation", "entity_overview"})
        fact_search_requested = (
            not entity_memory_query
            and (_decision_is_creator_public_fact(pre_search_decision) or _decision_is_creator_public_fact(post_search_decision))
        )

        if web_results:
            support_set = _inject_live_web_results(support_set, web_results)
        elif fact_search_requested:
            # Before returning the fallback, await any pending speculative web search.
            # Without this, we'd bail with a canned "check my website" message even
            # though the actual web results are in-flight and may contain the answer.
            if speculative_web_task and not speculative_web_task.done():
                try:
                    web_results = await speculative_web_task
                except Exception as exc:
                    logger.warning("[LATENCY] Speculative web search failed while awaiting for fact: %s", exc)
                    web_results = []
            elif speculative_web_task and speculative_web_task.done():
                try:
                    web_results = speculative_web_task.result()
                except Exception:
                    web_results = []
            if web_results:
                support_set = _inject_live_web_results(support_set, web_results)
                needs_fallback = False
            else:
                # No web results found. Instead of returning a generic fallback
                # immediately, let the main LLM handle the question — it has
                # domain lock rules and RAG context to answer or redirect properly.
                logger.info("[LATENCY] fact_search_requested but no web results; falling through to main LLM.")

        if needs_fallback:
            logger.info("[LATENCY] Blocking web fallback for explicit live/source request.")
            yield "__STATUS__websearch"
            if speculative_web_task:
                try:
                    web_results = await speculative_web_task
                except Exception as exc:
                    logger.warning("[LATENCY] Speculative web search failed: %s", exc)
                    web_results = []
            if not web_results:
                live_intent_metadata = intent_metadata
                if _decision_is_creator_public_fact(post_search_decision):
                    live_intent_metadata = {"intent": "PUBLIC_CREATOR_FACT"}
                web_results = await asyncio.to_thread(
                    _run_live_web_search,
                    search_question,
                    creator_row,
                    conversation_history,
                    preferred_platforms,
                    is_video_request,
                    live_intent_metadata,
                )
        elif search_mode == "hybrid":
            if speculative_web_task:
                speculative_web_task.cancel()
            logger.info("[LATENCY] Skipping blocking web fallback for normal chat.")
            """
            if not support_set:
                # No RAG results, so run web search immediately in hybrid mode.
                needs_fallback = True
                logger.info("[LATENCY] RAG empty. Direct web search trigger.")
                from backend.services.research_provider import get_research_provider
                rp = get_research_provider()
                web_results = await asyncio.to_thread(
                    rp.search,
                    web_query,
                    creator_row,
                    conversation_history=conversation_history,
                    intent_metadata=intent_metadata,
                )
            elif is_video_request and has_recommendable_ingested_resource:
                # No RAG results — definitely need web search, skip sufficiency check
                logger.info("[LATENCY] Strong ingested video match found. Skipping web fallback.")
            else:
                # Have RAG results — run sufficiency check
                # If few results, speculatively launch web search in parallel
                if len(support_set) <= 2 and not has_recommendable_ingested_resource:
                    # PARALLEL: sufficiency + speculative web search
                    from backend.services.research_provider import get_research_provider
                    rp = get_research_provider()
                    sufficiency_task = asyncio.to_thread(evaluate_context_sufficiency, question, support_set, conversation_history)
                    search_task = asyncio.to_thread(
                        rp.search, web_query, creator_row,
                        conversation_history=conversation_history,
                        intent_metadata=intent_metadata
                    )
                    sufficiency, web_results = await asyncio.gather(sufficiency_task, search_task)
                    logger.info(f"[LATENCY] Parallel sufficiency={sufficiency}, web_results={len(web_results)}")
                    if sufficiency in ["PARTIAL", "INSUFFICIENT"]:
                        needs_fallback = True
                else:
                    # Standard: sequential sufficiency check
                    sufficiency = await asyncio.to_thread(evaluate_context_sufficiency, question, support_set, conversation_history)
                    logger.info(f"Context Sufficiency: {sufficiency}")
                    if sufficiency in ["PARTIAL", "INSUFFICIENT"]:
                        needs_fallback = True
                        from backend.services.research_provider import get_research_provider
                        rp = get_research_provider()
                        web_results = await asyncio.to_thread(
                            rp.search, web_query, creator_row,
                            conversation_history=conversation_history,
                            intent_metadata=intent_metadata
                        )
            """

        _t_search_end = _time.time()
        logger.info(f"[LATENCY] Search fallback phase: {_t_search_end - _t_search_start:.2f}s (fallback={needs_fallback}, results={len(web_results)})")
        
        if needs_fallback and web_results:
            support_set = _inject_live_web_results(support_set, web_results)
        elif needs_fallback and wants_link:
            no_online_fallback = _build_not_online_fallback(question, creator_row.get("name") or creator_row.get("handle") or "the creator", conversation_history, kind="video" if is_video_request else "source", style_fingerprint=style_fingerprint)
        elif wants_link and not has_recommendable_ingested_resource:
            no_online_fallback = _build_not_online_fallback(
                question,
                creator_row.get("name") or creator_row.get("handle") or "the creator",
                conversation_history,
                kind="video" if is_video_request else "source",
                style_fingerprint=style_fingerprint,
            )
        support_set = shape_support_set(question, support_set, limit=4)

        if _should_force_resource_fallback(
            no_online_fallback,
            wants_link=wants_link,
            has_linkable_ingested_resource=has_linkable_ingested_resource,
            web_results=web_results,
        ):
            logger.info("Streaming deterministic resource fallback with no safe link to attach.")
            yield no_online_fallback
            return

    else:
        # Greeting/Small-talk Route: No Embeddings needed for TTFT
        # Just await metadata
        creator_row = await creator_task
        if not creator_row:
            yield "I couldn't find information about that creator."
            return
        
        persona = creator_row.get("soul_md") or ""
        
        if route == "ROUTE_0_GREETING":
            mems = []
        else:
            # For small talk, we can still use memory if we want
            mems = await interaction_engine.memory.search_async(
                str(creator_id), str(user_id), str(thread_id or "new"), question
            )

        if route == "ROUTE_0_GREETING":
            voice_profile = creator_row.get("voice_profile") or {}
            style_fingerprint = creator_row.get("style_fingerprint") or {}
            if isinstance(voice_profile, str):
                try:
                    voice_profile = json.loads(voice_profile)
                except Exception:
                    voice_profile = {}
            if isinstance(style_fingerprint, str):
                try:
                    style_fingerprint = json.loads(style_fingerprint)
                except Exception:
                    style_fingerprint = {}

            # Use the interaction engine's LLM-based greeting for creator voice
            from backend.services.conversation_closure import get_greeting_question
            _greeting_plan = InteractionPlan(
                route="ROUTE_0_GREETING",
                next_question=get_greeting_question(creator_row),
            )
            greeting_text = await asyncio.to_thread(
                interaction_engine._render_greeting,
                _greeting_plan,
                creator_row,
                question,
                user_name,
                None,
                user_preferences,
                conversation_history or [],
                thread_id or "new",
            )
            greeting_text = strip_all_markdown(greeting_text, creator_profile=creator_row)
            from backend.services.voice_dna import apply_vocabulary_resonance
            greeting_text = apply_vocabulary_resonance(greeting_text, creator_row)
            yield greeting_text
            return

    # 4. Async Synthesis Stream (Instant Start)
    stream = await interaction_engine.render_combined_pass_stream_async(
        creator_profile=creator_row,
        rag_chunks=support_set,
        creator_id=creator_id,
        user_id=user_id,
        thread_id=thread_id or "new",
        user_name=user_name,
        user_msg=question,
        persona=persona,
        history=conversation_history or [],
        user_preferences=user_preferences,
        pre_fetched_memories=mems,
        route=route
    )

    streamed_parts: List[str] = []
    async for chunk in stream:
        content = chunk.choices[0].delta.content
        if content:
            streamed_parts.append(content)
            yield content

    final_stream_text = "".join(streamed_parts)
    if no_online_fallback and _contains_placeholder_link_artifacts(final_stream_text):
        final_stream_text = no_online_fallback
        yield f"__FINAL_CONTENT__{final_stream_text}"
    # Build cards when the user asked for resources, the recommender ran,
    # OR the response is grounded in substantive ingested content.
    _has_substantive_rag_stream = bool(
        support_set
        and any(
            not str(c.get("content") or "").startswith("[LIVE WEB SEARCH RESULT]")
            and (c.get("url") or (c.get("source_ref") or {}).get("canonical_url"))
            for c in (support_set or [])[:6]
        )
    )
    _user_wanted_resource = (
        (route == "ROUTE_2_TASK" and locals().get("should_run_recommender", False))
        or _has_substantive_rag_stream
    )
    stream_cards = []
    if _user_wanted_resource:
        stream_cards = _build_response_cards(
            rec_result if route == "ROUTE_2_TASK" else None,
            support_set,
            preferred_platforms=(rec_result.get("resource_intent", {}) or {}).get("preferred_platforms") if route == "ROUTE_2_TASK" else None,
            question=question,
            answer_text=final_stream_text,
        )
    # Card rescue — attach card only if the LLM answer explicitly mentions a known title
    if not stream_cards:
        stream_cards = _rescue_cards_from_answer(final_stream_text, support_set)
    if route == "ROUTE_2_TASK" and (rec_result or {}).get("best_candidate"):
        recommendation_feedback_service.log_impression(
            user_id=user_id,
            creator_id=creator_id,
            thread_id=thread_id,
            query=question,
            best_candidate=(rec_result or {}).get("best_candidate"),
            alternate_candidates=(rec_result or {}).get("alternate_candidates") or [],
            resource_intent=(rec_result or {}).get("resource_intent") or {},
            confidence=(rec_result or {}).get("confidence"),
            query_variants=(rec_result or {}).get("query_variants") or [],
            retrieval_debug=(rec_result or {}).get("retrieval_debug") or {},
        )
    if stream_cards:
        yield f"__CARDS__{json.dumps(stream_cards)}"
    stream_citations = build_inline_citations(
        support_set,
        question=question,
        answer_text=final_stream_text,
    )
    if stream_citations:
        yield f"__CITATIONS__{json.dumps(stream_citations)}"
    support_payload = build_regurgitation_support_payload(support_set)
    if support_payload:
        yield f"__SUPPORT__{json.dumps(support_payload)}"

def build_source_list(support_set: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert support_set chunks into a flat list of unique source references."""
    seen = set()
    sources = []
    for chunk in support_set:
        ref = chunk.get("source_ref")
        if not ref: continue
        url = ref.get("canonical_url")
        if url and url not in seen:
            seen.add(url)
            sources.append(ref)
    return sources


def build_regurgitation_support_payload(support_set: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for chunk in support_set or []:
        source_ref = chunk.get("source_ref") or {}
        payload.append(
            {
                "content": str(chunk.get("content") or "")[:1200],
                "title": chunk.get("title") or source_ref.get("title") or "",
                "url": chunk.get("url") or source_ref.get("canonical_url") or "",
                "source_ref": {
                    "title": source_ref.get("title") or chunk.get("title") or "",
                    "canonical_url": source_ref.get("canonical_url") or chunk.get("url") or "",
                    "platform": source_ref.get("platform") or "",
                },
            }
        )
        if len(payload) >= 4:
            break
    return payload


def build_inline_citations(
    support_set: List[Dict[str, Any]],
    *,
    question: str = "",
    answer_text: str = "",
    limit: int = 4,
) -> List[Dict[str, Any]]:
    ranked: List[Dict[str, Any]] = []
    seen = set()
    for idx, chunk in enumerate(support_set or []):
        ref = chunk.get("source_ref") or {}
        if not isinstance(ref, dict):
            continue
        url = str(ref.get("canonical_url") or chunk.get("url") or "").strip()
        if not url:
            continue
        url_key = url.lower()
        if url_key in seen:
            continue
        seen.add(url_key)

        title = (
            str(ref.get("title") or chunk.get("title") or "").strip()
            or "Source"
        )
        snippet = str(chunk.get("snippet") or "").strip()
        content = str(chunk.get("content") or "").strip()
        platform = str(ref.get("platform") or _platform_from_url(url) or "web").strip().lower()
        is_live_web = content.startswith("[LIVE WEB SEARCH RESULT]")
        score = _score_support_resource_candidate(
            title,
            url,
            content,
            snippet=snippet,
            question=question,
            answer_text=answer_text,
            recommended=False,
            is_live_web=is_live_web,
            chunk_index=idx,
        )
        preview = snippet or re.sub(r"\s+", " ", content.replace("[LIVE WEB SEARCH RESULT]", "").strip())

        # ── Rich provenance metadata ──
        content_type = str(ref.get("content_type") or "").strip()
        published_at = ref.get("published_at")
        start_time_sec = ref.get("start_time_sec")
        end_time_sec = ref.get("end_time_sec")

        citation_entry: Dict[str, Any] = {
            "title": title[:160],
            "url": url,
            "platform": platform,
            "snippet": preview[:220],
            "is_live_web": is_live_web,
            "score": score,
            "content_type": content_type or ("web" if is_live_web else "unknown"),
        }
        if published_at:
            citation_entry["published_at"] = str(published_at)
        if start_time_sec is not None:
            citation_entry["start_time_sec"] = start_time_sec
        if end_time_sec is not None:
            citation_entry["end_time_sec"] = end_time_sec

        ranked.append(citation_entry)

    # Filter to sources that meaningfully contributed to the answer before sorting.
    # Prevents all retrieved chunks from appearing as citations when only 1-2 were used.
    _MIN_CITATION_SCORE = 0.34
    ranked = [item for item in ranked if float(item.get("score") or 0.0) >= _MIN_CITATION_SCORE]
    ranked.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    # ── URL health-check: drop dead links from citations ──
    alive_ranked: List[Dict[str, Any]] = []
    for cit in ranked:
        if check_url_alive_sync(cit.get("url") or ""):
            alive_ranked.append(cit)
        else:
            logger.info("Dead link filtered from citation: %s", cit.get("url"))
    return alive_ranked[:limit]
