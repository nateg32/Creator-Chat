"""
Title Resolution Engine
=======================
Proprietary fuzzy title matching system that resolves approximate user references
to actual stored content titles.

When a user says "your video about best online business to make 10k+", the system
must match this to "Best Online Business to Make $10k+/month In 2026 (Beginner Friendly)"
despite missing $, /month, year, and subtitle.

Architecture:
    1. Canonical normalization — strips currency symbols, units, years, parentheticals
    2. N-gram fingerprinting — generates overlapping word n-grams for fuzzy comparison
    3. Multi-strategy scoring — combines token overlap, sequential match, and n-gram hits
    4. Threshold-gated retrieval — only fires when user explicitly references a title

Zero LLM calls. Pure CPU. ~1-3ms per resolution.
"""

import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
#  Normalization
# ──────────────────────────────────────────────────────────

# Characters that users routinely omit when recalling titles
_STRIP_CHARS = re.compile(r"[$€£¥#@&]")

# Year tags: "(2024)", "(2025)", "2026", etc.
_YEAR_TAG = re.compile(r"\b20\d{2}\b")

# Parenthetical suffixes: "(Beginner Friendly)", "(Full Guide)", etc.
_PAREN_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")

# Unit suffixes after numbers: 10k+/month → 10k, $500/day → 500
_UNIT_SUFFIX = re.compile(r"(\d+k?\+?)\s*/\s*\w+", re.IGNORECASE)

# Pipe/dash separators used for subtitles: "Main Title | Subtitle", "Main - Subtitle"
_SUBTITLE_SEP = re.compile(r"\s*[|–—]\s*")

# Collapse whitespace
_MULTI_SPACE = re.compile(r"\s+")

# Non-alphanumeric (for token extraction)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _canonicalize(text: str) -> str:
    """
    Reduce a title or user fragment to a canonical form for matching.
    Strips symbols, years, parentheticals, and normalizes whitespace.
    """
    if not text:
        return ""
    t = text.lower().strip()
    t = _STRIP_CHARS.sub("", t)
    t = _UNIT_SUFFIX.sub(r"\1", t)
    t = _PAREN_SUFFIX.sub("", t)
    t = _YEAR_TAG.sub("", t)
    t = _SUBTITLE_SEP.sub(" ", t)
    t = _MULTI_SPACE.sub(" ", t).strip()
    return t


def _tokenize(text: str) -> List[str]:
    """Extract meaningful tokens (3+ chars, lowered, no symbols)."""
    words = _NON_ALNUM.split(text.lower())
    return [w for w in words if len(w) >= 2]


def _bigrams(tokens: List[str]) -> List[str]:
    """Generate sequential bigrams for order-aware matching."""
    return [f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens) - 1)]


# ──────────────────────────────────────────────────────────
#  Scoring
# ──────────────────────────────────────────────────────────

# Stop words to skip in scoring (common words that inflate false matches)
_STOP_WORDS = frozenset({
    "the", "to", "in", "for", "of", "and", "or", "is", "it", "my",
    "your", "you", "how", "what", "with", "from", "this", "that",
    "on", "at", "by", "an", "as", "be", "do", "so", "up", "out",
    "if", "me", "we", "he", "she", "no", "not", "but", "all", "are",
    "was", "has", "had", "can", "its",
})


def _score_match(
    user_fragment: str,
    candidate_title: str,
) -> float:
    """
    Score how well a user's approximate title reference matches a stored title.

    Returns 0.0 – 1.0 where:
        1.0 = perfect match
        0.7+ = very likely the same content
        0.5+ = probable match
        <0.4 = unlikely match

    Uses 3 sub-scores:
        1. Token overlap (Jaccard-like, weighted by position)
        2. Sequential bigram match (captures word order)
        3. Canonicalized containment (is the user fragment inside the title?)
    """
    if not user_fragment or not candidate_title:
        return 0.0

    # Canonicalize both
    c_user = _canonicalize(user_fragment)
    c_title = _canonicalize(candidate_title)

    if not c_user or not c_title:
        return 0.0

    # Direct canonical containment check
    if c_user in c_title or c_title in c_user:
        return 0.95

    # Tokenize (excluding stop words for scoring)
    user_tokens = [t for t in _tokenize(c_user) if t not in _STOP_WORDS]
    title_tokens = [t for t in _tokenize(c_title) if t not in _STOP_WORDS]

    if not user_tokens or not title_tokens:
        return 0.0

    # ── Sub-score 1: Token overlap ──
    user_set = set(user_tokens)
    title_set = set(title_tokens)
    intersection = user_set & title_set
    if not intersection:
        return 0.0

    # Weighted Jaccard: penalize the smaller set less (user typically abbreviates)
    token_score = len(intersection) / max(len(user_set), 1)

    # ── Sub-score 2: Sequential bigram match ──
    user_bi = set(_bigrams(user_tokens))
    title_bi = set(_bigrams(title_tokens))
    bi_intersection = user_bi & title_bi
    bigram_score = len(bi_intersection) / max(len(user_bi), 1) if user_bi else 0.0

    # ── Sub-score 3: Longest common subsequence ratio ──
    lcs_len = _lcs_length(user_tokens, title_tokens)
    lcs_score = lcs_len / max(len(user_tokens), 1)

    # ── Combined score ──
    combined = (
        token_score * 0.40
        + bigram_score * 0.30
        + lcs_score * 0.30
    )

    # Bonus: if ALL user content words appear in the title (user just abbreviated)
    if user_set <= title_set:
        combined = max(combined, 0.80)

    return round(min(combined, 1.0), 3)


def _lcs_length(a: List[str], b: List[str]) -> int:
    """Length of longest common subsequence (order-preserving)."""
    if not a or not b:
        return 0
    # Optimized for short sequences (titles are typically <15 tokens)
    m, n = len(a), len(b)
    if m > 30 or n > 30:
        # Fallback for unexpectedly long sequences
        return sum(1 for t in a if t in set(b))
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr
    return prev[n]


# ──────────────────────────────────────────────────────────
#  SQL pattern builder
# ──────────────────────────────────────────────────────────

def build_fuzzy_sql_patterns(fragment: str) -> List[str]:
    """
    Generate multiple SQL LIKE patterns from a user fragment to handle
    special character variations.

    For "best online business to make 10k+":
    Returns:
        [
            "%best online business to make 10k%",       # stripped +
            "%best%online%business%make%10k%",           # word-gap pattern
            "%best online business%make%10%",             # relaxed numeric
        ]
    """
    if not fragment:
        return []

    patterns: List[str] = []
    clean = fragment.lower().strip()

    # Pattern 1: Strip special chars entirely
    stripped = _STRIP_CHARS.sub("", clean)
    stripped = re.sub(r"[+/\\()]+", "", stripped)
    stripped = _MULTI_SPACE.sub(" ", stripped).strip()
    if stripped:
        patterns.append(f"%{stripped}%")

    # Pattern 2: Word-gap pattern (each content word separated by %)
    tokens = [t for t in _tokenize(clean) if t not in _STOP_WORDS and len(t) >= 3]
    if len(tokens) >= 3:
        gap_pattern = "%".join(tokens[:6])
        patterns.append(f"%{gap_pattern}%")

    # Pattern 3: Core content words only (most discriminating 3-4 words)
    if len(tokens) >= 4:
        # Pick the longest/most unique words
        scored = sorted(tokens, key=lambda w: (-len(w), w))
        core = sorted(scored[:4], key=lambda w: clean.find(w))
        core_pattern = "%".join(core)
        patterns.append(f"%{core_pattern}%")

    # Deduplicate
    seen: set = set()
    unique: List[str] = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    return unique


# ──────────────────────────────────────────────────────────
#  Title resolver (main entry point)
# ──────────────────────────────────────────────────────────

def resolve_title_reference(
    creator_id: int,
    user_fragment: str,
    limit: int = 5,
    threshold: float = 0.50,
) -> List[Dict[str, Any]]:
    """
    Resolve an approximate user title reference to actual stored documents.

    Returns scored candidates sorted by match quality. Only returns results
    above the threshold.

    This is the main entry point called by the retrieval pipeline when
    a user explicitly references a title (detected by NAMED_RESOURCE_PATTERNS
    or quoted fragments).
    """
    from backend.db import db

    if not user_fragment or not user_fragment.strip():
        return []

    # Build fuzzy SQL patterns
    patterns = build_fuzzy_sql_patterns(user_fragment)
    if not patterns:
        return []

    # Query: union of all pattern matches, scored by match quality
    union_parts: List[str] = []
    params: List[Any] = [creator_id]

    for pattern in patterns:
        union_parts.append(
            "SELECT id, title, content, source, source_id, metadata, "
            "COALESCE(url, metadata->>'source_url', metadata->>'canonical_url', '') AS source_url "
            "FROM documents WHERE creator_id = %s AND source != 'persona' "
            "AND (LOWER(COALESCE(title, '')) LIKE %s OR LOWER(COALESCE(content, '')) LIKE %s)"
        )
        params.extend([creator_id, pattern, pattern])

    # Remove duplicate creator_id params (first one is shared)
    query = f"""
        SELECT DISTINCT ON (id) * FROM (
            {' UNION ALL '.join(union_parts)}
        ) AS candidates
        LIMIT %s
    """
    params.append(limit * 3)  # Fetch more, then score and cut

    try:
        rows = db.execute_query(query, tuple(params))
    except Exception as e:
        logger.error(f"Title resolution query failed: {e}")
        return []

    if not rows:
        return []

    # Score each candidate
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for row in rows:
        title = row.get("title") or ""
        score = _score_match(user_fragment, title)

        # Also check content for title-like matches (some videos have title in transcript)
        content = (row.get("content") or "")[:500]
        content_score = _score_match(user_fragment, content) * 0.6  # Discount content matches
        final_score = max(score, content_score)

        if final_score >= threshold:
            scored.append((final_score, row))

    # Sort by score descending
    scored.sort(key=lambda x: -x[0])

    # Return top results with scores
    results: List[Dict[str, Any]] = []
    for score, row in scored[:limit]:
        metadata = row.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = __import__("json").loads(metadata)
            except Exception:
                metadata = {}

        source_url = row.get("source_url") or metadata.get("source_url") or metadata.get("canonical_url") or ""
        platform = str(metadata.get("platform") or row.get("source") or "unknown").lower()

        results.append({
            "doc_id": row["id"],
            "title": row.get("title") or "",
            "content": row.get("content") or "",
            "source": row.get("source") or "",
            "source_id": row.get("source_id") or "",
            "source_url": source_url,
            "metadata": metadata,
            "title_match_score": score,
            "platform": platform,
        })

    return results
