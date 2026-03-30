import json
import re
from typing import Any, Dict, List, Optional

from backend.db import db


PLATFORM_ALIASES = {
    "twitter": {"twitter", "tweet", "tweets", "x"},
    "instagram": {"instagram", "insta", "ig", "reel", "reels"},
    "youtube": {"youtube", "yt", "short", "shorts", "video", "videos"},
    "linkedin": {"linkedin"},
    "tiktok": {"tiktok", "tik tok"},
    "facebook": {"facebook", "fb"},
    "reddit": {"reddit"},
}

SOCIAL_EXACT_TERMS = {
    "quote", "quotes", "tweet", "tweets", "post", "posts", "posted", "wrote", "write",
    "writing", "said", "caption", "line", "lines", "favorite", "favourite", "best",
}

NAMED_RESOURCE_PATTERNS = (
    re.compile(r"\bfrom your (?:video|podcast|episode|post|reel|short|lesson)\s+([^,\.\?\!\n]{4,140})", re.IGNORECASE),
    re.compile(r"\bin your (?:video|podcast|episode|post|reel|short|lesson)\s+([^,\.\?\!\n]{4,140})", re.IGNORECASE),
    re.compile(r"\b(?:video|podcast|episode|post|reel|short|lesson)\s+(?:called|titled)\s+([^,\.\?\!\n]{4,140})", re.IGNORECASE),
)

GENERIC_QUERY_TERMS = {
    "what", "which", "your", "about", "from", "that", "this", "have", "with", "would",
    "could", "should", "posted", "post", "posts", "tweet", "tweets", "quote", "quotes",
    "line", "lines", "favorite", "favourite", "best", "tell", "show", "give", "send",
    "find", "posted", "write", "wrote", "on", "did", "was",
}


def detect_platform_hints(question: str) -> List[str]:
    text = (question or "").lower()
    tokens = set(re.findall(r"[a-z0-9']+", text))
    hints: List[str] = []
    for platform, aliases in PLATFORM_ALIASES.items():
        if any(alias in tokens or alias in text for alias in aliases):
            hints.append(platform)
    return hints


def wants_exact_social_post(question: str) -> bool:
    words = set(re.findall(r"[a-z0-9']+", (question or "").lower()))
    if not words:
        return False
    return bool(detect_platform_hints(question)) and bool(words & SOCIAL_EXACT_TERMS)


def extract_quoted_fragments(question: str) -> List[str]:
    text = question or ""
    fragments = re.findall(r'"([^"]+)"', text)
    fragments += re.findall(r"“([^”]+)”", text)
    seen = set()
    ordered: List[str] = []
    for fragment in fragments:
        clean = re.sub(r"\s+", " ", fragment).strip()
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(clean)
    return ordered[:3]


def extract_named_resource_fragments(question: str) -> List[str]:
    text = question or ""
    seen = set()
    ordered: List[str] = []
    for pattern in NAMED_RESOURCE_PATTERNS:
        for fragment in pattern.findall(text):
            clean = re.sub(r"\s+", " ", str(fragment or "")).strip(" .,:;!?\"'")
            if len(clean) < 4:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(clean)
    return ordered[:3]


def normalize_query_terms(question: str) -> List[str]:
    words = re.findall(r"[a-z0-9']+", (question or "").lower())
    ordered: List[str] = []
    seen = set()
    for word in words:
        if len(word) < 3 or word in GENERIC_QUERY_TERMS:
            continue
        if word in seen:
            continue
        seen.add(word)
        ordered.append(word)
    return ordered[:8]


def _parse_metadata(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, dict):
                return decoded
        except Exception:
            return {}
    return {}


def _platform_for_row(row: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    return str(metadata.get("platform") or row.get("source") or "unknown").lower()


def _row_to_chunk(row: Dict[str, Any], lexical_score: float) -> Dict[str, Any]:
    metadata = _parse_metadata(row.get("metadata"))
    source_url = row.get("source_url") or metadata.get("source_url") or metadata.get("canonical_url") or ""
    platform = _platform_for_row(row, metadata)
    title = row.get("title") or metadata.get("title") or source_url or "External Resource"
    published_at = metadata.get("published_at")
    content_type = metadata.get("content_type") or metadata.get("type") or row.get("source") or "unknown"

    score = max(0.0, float(lexical_score or 0.0))
    distance = max(0.01, 0.42 - min(score, 12.0) * 0.03)
    return {
        "chunk_id": f"lexdoc_{row['doc_id']}",
        "chunk_index": 0,
        "distance": distance,
        "content": row.get("content") or "",
        "document_id": row["doc_id"],
        "source_ref": {
            "platform": platform,
            "content_id": metadata.get("content_id") or row.get("source_id") or "",
            "canonical_url": source_url,
            "title": title,
            "published_at": published_at,
            "content_type": content_type,
        },
        "lexical_score": score,
    }


def _run_match_query(
    creator_id: int,
    platform_filters: List[str],
    quoted_fragments: List[str],
    keywords: List[str],
    limit: int,
) -> List[Dict[str, Any]]:
    if not quoted_fragments and not keywords:
        return []

    select_clauses: List[str] = []
    select_params: List[Any] = []
    match_clauses: List[str] = []
    match_params: List[Any] = []

    for fragment in quoted_fragments[:3]:
        pattern = f"%{fragment.lower()}%"
        select_clauses.append("CASE WHEN LOWER(COALESCE(d.content, '')) LIKE %s THEN 12 ELSE 0 END")
        select_params.append(pattern)
        select_clauses.append("CASE WHEN LOWER(COALESCE(d.title, '')) LIKE %s THEN 6 ELSE 0 END")
        select_params.append(pattern)
        match_clauses.append("LOWER(COALESCE(d.content, '')) LIKE %s OR LOWER(COALESCE(d.title, '')) LIKE %s")
        match_params.extend([pattern, pattern])

    for keyword in keywords[:8]:
        pattern = f"%{keyword.lower()}%"
        select_clauses.append("CASE WHEN LOWER(COALESCE(d.content, '')) LIKE %s OR LOWER(COALESCE(d.title, '')) LIKE %s THEN 2 ELSE 0 END")
        select_params.extend([pattern, pattern])
        match_clauses.append("LOWER(COALESCE(d.content, '')) LIKE %s OR LOWER(COALESCE(d.title, '')) LIKE %s")
        match_params.extend([pattern, pattern])

    where_clauses = [
        "d.creator_id = %s",
        "d.source != 'persona'",
    ]
    where_params: List[Any] = [creator_id]
    if platform_filters:
        where_clauses.append("(LOWER(COALESCE(d.metadata->>'platform', d.source, '')) = ANY(%s) OR LOWER(COALESCE(d.source, '')) = ANY(%s))")
        where_params.extend([platform_filters, platform_filters])

    query = f"""
        SELECT
            d.id AS doc_id,
            d.title,
            d.content,
            d.source,
            d.source_id,
            d.metadata,
            COALESCE(d.url, d.metadata->>'source_url', d.metadata->>'canonical_url', '') AS source_url,
            ({' + '.join(select_clauses)}) AS lexical_score
        FROM documents d
        WHERE {' AND '.join(where_clauses)}
          AND ({' OR '.join(match_clauses)})
        ORDER BY lexical_score DESC, LENGTH(COALESCE(d.content, '')) ASC
        LIMIT %s
    """
    params = tuple(select_params + where_params + match_params + [limit])
    return db.execute_query(query, params)


def _run_showcase_query(
    creator_id: int,
    platform_filters: List[str],
    limit: int,
) -> List[Dict[str, Any]]:
    if not platform_filters:
        return []

    query = """
        SELECT
            d.id AS doc_id,
            d.title,
            d.content,
            d.source,
            d.source_id,
            d.metadata,
            COALESCE(d.url, d.metadata->>'source_url', d.metadata->>'canonical_url', '') AS source_url,
            (
                COALESCE(NULLIF(regexp_replace(COALESCE(d.metadata->>'likes', '0'), '[^0-9]', '', 'g'), ''), '0')::bigint +
                COALESCE(NULLIF(regexp_replace(COALESCE(d.metadata->>'views', '0'), '[^0-9]', '', 'g'), ''), '0')::bigint
            ) AS lexical_score
        FROM documents d
        WHERE d.creator_id = %s
          AND d.source != 'persona'
          AND (LOWER(COALESCE(d.metadata->>'platform', d.source, '')) = ANY(%s) OR LOWER(COALESCE(d.source, '')) = ANY(%s))
          AND LENGTH(COALESCE(d.content, '')) BETWEEN 20 AND 420
        ORDER BY lexical_score DESC, COALESCE(d.metadata->>'published_at', '') DESC
        LIMIT %s
    """
    return db.execute_query(query, (creator_id, platform_filters, platform_filters, limit))


def retrieve_exact_text_matches(
    creator_id: int,
    question: str,
    limit: int = 4,
    enabled_platforms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    platform_hints = detect_platform_hints(question)
    enabled = [str(value).lower() for value in (enabled_platforms or []) if value]
    if enabled:
        if platform_hints:
            platform_hints = [platform for platform in platform_hints if platform in enabled]
        else:
            platform_hints = enabled

    quoted_fragments = extract_quoted_fragments(question)
    named_resource_fragments = extract_named_resource_fragments(question)
    combined_fragments: List[str] = []
    seen_fragments = set()
    for fragment in quoted_fragments + named_resource_fragments:
        key = fragment.lower()
        if key in seen_fragments:
            continue
        seen_fragments.add(key)
        combined_fragments.append(fragment)
    keywords = normalize_query_terms(question)
    exact_social = wants_exact_social_post(question)

    if not exact_social and not combined_fragments:
        return []

    rows = _run_match_query(creator_id, platform_hints, combined_fragments, keywords, limit)
    if not rows and exact_social:
        rows = _run_showcase_query(creator_id, platform_hints or enabled, limit)

    chunks: List[Dict[str, Any]] = []
    for row in rows or []:
        chunks.append(_row_to_chunk(row, row.get("lexical_score") or 0.0))
    return chunks


def merge_support_sets(primary: List[Dict[str, Any]], supplemental: List[Dict[str, Any]], limit: int = 4) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()

    def add(candidate: Dict[str, Any]):
        ref = candidate.get("source_ref") or {}
        key = (
            ref.get("content_id")
            or ref.get("canonical_url")
            or candidate.get("document_id")
            or candidate.get("chunk_id")
        )
        if not key or key in seen:
            return
        seen.add(key)
        merged.append(candidate)

    for candidate in supplemental or []:
        add(candidate)
        if len(merged) >= limit:
            return merged

    for candidate in primary or []:
        add(candidate)
        if len(merged) >= limit:
            break

    return merged
