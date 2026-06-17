import math
import re
from typing import Any, Dict, List


_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "be",
    "but",
    "by",
    "do",
    "does",
    "for",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "my",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "what",
    "when",
    "why",
    "your",
}

_STRUCTURE_PATTERN = re.compile(
    r"\b(?:stage|step)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b"
    r"|^\s*\d+[.:)]\s+",
    re.IGNORECASE | re.MULTILINE,
)
_TIMESTAMP_PATTERN = re.compile(r"\b\d{1,2}:\d{2}\b")
_TRANSCRIPT_TAG_PATTERN = re.compile(r"\[(?:[^\]\n]{1,40})\]")
_TRANSCRIPT_LINE_PATTERN = re.compile(r"(?im)^\s*\d+\s*:\s+")
_RAW_WORD_PATTERN = re.compile(r"[a-z0-9']+")


def _chunk_text(chunk: Dict[str, Any]) -> str:
    return str(
        chunk.get("text")
        or chunk.get("content")
        or chunk.get("chunk_text")
        or ""
    ).strip()


def _chunk_title(chunk: Dict[str, Any]) -> str:
    source_ref = chunk.get("source_ref") or {}
    return str(
        chunk.get("title")
        or source_ref.get("title")
        or chunk.get("source_url")
        or source_ref.get("canonical_url")
        or ""
    ).strip()


def _normalize_words(text: str) -> List[str]:
    return _RAW_WORD_PATTERN.findall(str(text or "").lower())


def _content_words(text: str) -> set[str]:
    return {word for word in _normalize_words(text) if word not in _STOPWORDS}


def _overlap_ratio(left: str, right: str) -> float:
    left_terms = _content_words(left)
    right_terms = _content_words(right)
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / max(1, len(left_terms))


def find_structure_markers(text: str) -> List[str]:
    value = str(text or "")
    markers: List[str] = []
    for match in _STRUCTURE_PATTERN.finditer(value):
        marker = match.group(0).strip()
        if re.match(r"^\d+[.:)]", marker):
            markers.append(marker)
            continue
        prefix = value[max(0, match.start() - 3):match.start()]
        if match.start() == 0 or prefix.endswith("\n") or prefix.endswith(". ") or prefix.endswith("! ") or prefix.endswith("? "):
            markers.append(marker)
    return markers


def compute_trigram_overlap_rate(source_text: str, response: str) -> float:
    source_words = _normalize_words(source_text)
    response_words = _normalize_words(response)
    if len(source_words) < 3 or len(response_words) < 3:
        return 0.0

    source_trigrams = set(zip(source_words, source_words[1:], source_words[2:]))
    response_trigrams = set(zip(response_words, response_words[1:], response_words[2:]))
    if not response_trigrams:
        return 0.0
    return len(source_trigrams & response_trigrams) / len(response_trigrams)


def response_tail_has_question(response: str, tail_chars: int = 300) -> bool:
    tail = str(response or "")[-max(1, tail_chars):]
    return "?" in tail


def query_matches_document_title(query: str, chunks: List[Dict[str, Any]]) -> bool:
    query_terms = _content_words(query)
    if not query_terms:
        return False

    for chunk in chunks or []:
        title_terms = _content_words(_chunk_title(chunk))
        if not title_terms:
            continue
        overlap = len(query_terms & title_terms) / max(1, len(query_terms))
        if overlap > 0.6:
            return True
    return False


def shape_support_set(question: str, support_set: List[Dict[str, Any]], limit: int = 4) -> List[Dict[str, Any]]:
    candidates = list(support_set or [])
    if len(candidates) <= 1:
        return candidates[:limit] if limit else candidates

    prefer_diversity = query_matches_document_title(question, candidates)
    exact_lookup = any(
        token in (question or "").lower()
        for token in ["exact", "word for word", "transcript", "quote", "quoted", "verbatim"]
    )
    max_per_document = 2 if exact_lookup else (1 if prefer_diversity else 1)

    scored = []
    for index, chunk in enumerate(candidates):
        title = _chunk_title(chunk)
        content = _chunk_text(chunk)
        distance = float(chunk.get("distance") or 0.0)
        overlap_score = (_overlap_ratio(question, title) * 1.35) + (_overlap_ratio(question, content[:500]) * 0.9)
        recency_bias = max(0.0, 1.0 - (index * 0.08))
        distance_score = max(0.0, 1.0 - min(distance, 1.5) / 1.5)
        live_web_bonus = 0.18 if str(content).startswith("[LIVE WEB SEARCH RESULT]") else 0.0
        scored.append(
            (
                overlap_score + (distance_score * 0.35) + recency_bias + live_web_bonus,
                index,
                chunk,
            )
        )

    scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    selected: List[Dict[str, Any]] = []
    per_doc_counts: Dict[str, int] = {}

    for _, _, chunk in scored:
        source_ref = chunk.get("source_ref") or {}
        doc_key = str(
            source_ref.get("content_id")
            or source_ref.get("canonical_url")
            or chunk.get("document_id")
            or chunk.get("chunk_id")
            or ""
        )
        if doc_key and per_doc_counts.get(doc_key, 0) >= max_per_document:
            continue
        selected.append(chunk)
        if doc_key:
            per_doc_counts[doc_key] = per_doc_counts.get(doc_key, 0) + 1
        if limit and len(selected) >= limit:
            break

    if not selected:
        return candidates[:limit] if limit else candidates
    return selected


def select_turn_anchors(question: str, genome: Dict[str, Any], limit: int = 3) -> List[str]:
    if not genome:
        return []

    weighted_candidates = []
    groups = [
        (genome.get("evidence_markers") or [], 1.0),
        (genome.get("worldview_markers") or [], 0.9),
        (genome.get("response_moves") or [], 0.65),
        (genome.get("signature_markers") or [], 0.55),
        (genome.get("grounded_titles") or [], 0.75),
        (genome.get("stable_public_facts") or [], 0.35),
    ]
    for values, base_weight in groups:
        for value in values:
            cleaned = str(value or "").strip()
            if not cleaned:
                continue
            score = base_weight + (_overlap_ratio(question, cleaned) * 1.25)
            weighted_candidates.append((score, len(cleaned), cleaned))

    if not weighted_candidates:
        return []

    weighted_candidates.sort(key=lambda item: (item[0], math.log(item[1] + 1)), reverse=True)
    selected: List[str] = []
    seen = set()
    for _, _, value in weighted_candidates:
        key = re.sub(r"\s+", " ", value.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(value)
        if len(selected) >= limit:
            break
    return selected


def score_response_quality(
    question: str,
    response: str,
    chunks: List[Dict[str, Any]],
    *,
    creator_markers: List[str] | None = None,
) -> Dict[str, Any]:
    regurgitation = check_for_regurgitation(response, chunks or [])
    response_text = str(response or "").strip()
    response_words = _normalize_words(response_text)
    creator_marker_hits = 0
    for marker in creator_markers or []:
        normalized = str(marker or "").strip().lower()
        if normalized and normalized in response_text.lower():
            creator_marker_hits += 1

    score = 100
    penalties = []
    if not regurgitation.get("is_clean", True):
        reason = regurgitation.get("reason") or "regurgitation"
        penalties.append(reason)
        score -= {
            "timestamp_artifact": 35,
            "transcript_tag": 30,
            "transcript_structure_marker": 24,
            "high_trigram_overlap": 24,
            "high_word_ratio": 18,
            "mirrors_structure": 22,
            "missing_followup_question": 10,
        }.get(reason, 15)
    if len(response_words) >= 22 and creator_markers and creator_marker_hits == 0:
        penalties.append("missing_creator_markers")
        score -= 12
    substantive_reply = len(response_words) >= 12 and len(_normalize_words(question)) >= 2
    if substantive_reply and not response_tail_has_question(response_text):
        penalties.append("missing_followup_question")
        score -= 8

    score = max(0, min(100, score))
    if score >= 90:
        grade = "excellent"
    elif score >= 78:
        grade = "strong"
    elif score >= 62:
        grade = "fair"
    else:
        grade = "weak"

    return {
        "score": score,
        "grade": grade,
        "penalties": penalties,
        "creator_marker_hits": creator_marker_hits,
        "regurgitation": regurgitation,
        "has_tail_question": response_tail_has_question(response_text),
    }


def build_anti_regurgitation_block(query: str, chunks: List[Dict[str, Any]]) -> str:
    block = """
## HOW TO USE RETRIEVED CONTENT

The content below is BACKGROUND KNOWLEDGE only.
It is what you know, not what you should say verbatim.

Rules:
- Do NOT summarize the retrieved content point by point
- Do NOT repeat the structure of the source, including stages, numbered lists, headers, or section labels
- Do NOT walk through retrieved content in the order it was originally written
- If the user's question matches something you have covered before, answer from lived perspective and conviction, not by recapping the content
- Use retrieved content the way an expert uses memory, naturally, selectively, and in your own words
- Pull at most one or two specific insights from the retrieved content
- Always answer like a real conversation, not like a recap of a video or transcript
""".strip()

    if query_matches_document_title(query, chunks):
        block += """

NOTE: The user's question closely matches one of your content titles.
They likely want your sharpest personal take, not a recap.
Be especially direct, personal, and brief.
Lead with the strongest insight, not a structured summary.
""".rstrip()

    return block


def check_for_regurgitation(
    response: str,
    chunks: List[Dict[str, Any]],
    *,
    require_followup_question: bool = False,
) -> Dict[str, Any]:
    response_text = str(response or "").strip()
    source_text = " ".join(_chunk_text(chunk) for chunk in (chunks or [])).strip()
    response_markers = find_structure_markers(response_text)
    source_markers = find_structure_markers(source_text)
    response_words = _normalize_words(response_text)
    source_words = _normalize_words(source_text)
    trigram_overlap = compute_trigram_overlap_rate(source_text, response_text)
    word_ratio = (len(response_words) / max(1, len(source_words))) if source_words else 0.0
    mirrors_structure = bool(
        len(response_markers) >= 3
        and len(source_markers) >= 3
        and len(response_markers) >= max(3, int(len(source_markers) * 0.6))
    )
    has_tail_question = response_tail_has_question(response_text)

    reason = "ok"
    is_clean = True
    if _TIMESTAMP_PATTERN.search(response_text):
        is_clean = False
        reason = "timestamp_artifact"
    elif _TRANSCRIPT_TAG_PATTERN.search(response_text):
        is_clean = False
        reason = "transcript_tag"
    elif _TRANSCRIPT_LINE_PATTERN.search(response_text) or re.search(
        r"\bstage\s+(?:one|two|three|four|five|six|seven|eight|nine|ten)\b",
        response_text,
        re.IGNORECASE,
    ):
        is_clean = False
        reason = "transcript_structure_marker"
    elif trigram_overlap > 0.20:
        is_clean = False
        reason = "high_trigram_overlap"
    elif word_ratio > 0.50 and len(response_words) >= 40 and len(source_words) >= 60:
        is_clean = False
        reason = "high_word_ratio"
    elif mirrors_structure:
        is_clean = False
        reason = "mirrors_structure"
    elif require_followup_question and not has_tail_question:
        is_clean = False
        reason = "missing_followup_question"

    return {
        "is_clean": is_clean,
        "reason": reason,
        "trigram_overlap": round(trigram_overlap, 4),
        "word_ratio": round(word_ratio, 4),
        "mirrors_structure": mirrors_structure,
        "response_markers": response_markers,
        "source_markers": source_markers,
        "has_tail_question": has_tail_question,
    }
