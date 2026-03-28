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
