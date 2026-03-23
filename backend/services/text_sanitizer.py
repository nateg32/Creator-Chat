import difflib
import logging
import re
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

DASH_CHARS = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
WORD_BREAK_DASH_CHARS = "-\u2010\u2011\u2012\u2212"
CLAUSE_BREAK_DASH_CHARS = "\u2013\u2014\u2015"
DASH_CLASS = re.escape(DASH_CHARS)
WORD_BREAK_DASH_CLASS = re.escape(WORD_BREAK_DASH_CHARS)
CLAUSE_BREAK_DASH_CLASS = re.escape(CLAUSE_BREAK_DASH_CHARS)
MOJIBAKE_DASHES = ("\u00e2\u20ac\u201d", "\u00e2\u20ac\u201c")
PROTECTED_SPAN_RE = re.compile(r"\[[^\]]+\]\(https?://[^\s)]+\)|https?://[^\s)]+")
CLAUSE_DASH_RE = re.compile(
    rf"(?<=\S)(?:[ \t]+(?:--+|[{DASH_CLASS}]+)[ \t]*|[ \t]*(?:--+|[{DASH_CLASS}]+)[ \t]+)(?=\S)"
)
WORD_BREAK_DASH_RE = re.compile(rf"(?<=\w)(?:[{WORD_BREAK_DASH_CLASS}])(?=\w)")
WORD_CLAUSE_DASH_RE = re.compile(rf"(?<=\w)(?:--+|[{CLAUSE_BREAK_DASH_CLASS}]+)(?=\w)")
INLINE_TIGHT_DASH_RE = re.compile(rf"(?<=\S)(?:--+|[{DASH_CLASS}]+)(?=\S)")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")
LETTER_END_PUNCT_BOUNDARY_RE = re.compile(r"([A-Za-z][.!?])(?=[A-Z0-9])")
REPEATED_COMMA_RE = re.compile(r",\s*,+")
COMMA_BEFORE_END_PUNCT_RE = re.compile(r",\s*([.!?])")
MULTISPACE_RE = re.compile(r"[ \t]{2,}")
LIST_NUMBER_SPACE_RE = re.compile(r"(?m)^(\s*\d+[.)])(?=\S)")
BIBLE_VERSE_BOUNDARY_RE = re.compile(r"(?<=[A-Za-z])(?=(?:[1-3]?\d{1,3}:\d{1,3}(?:-\d{1,3})?))")
WORD_TO_NUMBER_BOUNDARY_RE = re.compile(r"(?<=[A-Za-z])(?=\d{1,4}(?=(?:\s|[,.;:!?)]|$)))")
WORD_TO_NUMBER_SUFFIX_BOUNDARY_RE = re.compile(
    r"(?<=[A-Za-z])(?=\d{1,4}(?:s|x|st|nd|rd|th)(?=(?:\s|[,.;:!?)]|$)))",
    re.IGNORECASE,
)
NUMBER_TO_WORD_BOUNDARY_RE = re.compile(r"(?<=\d)(?=[A-Za-z]{2,}(?=(?:\s|[,;:!?)]|$)))")
DOMAIN_BOUNDARY_RE = re.compile(r"(?<=[A-Za-z])(?=(?:www\.)?(?:\d|[A-Z])[A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)+(?:/[^\s]*)?)")
STREAM_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|\n")
SPLIT_HEAD_RE = re.compile(r"(^|[\n([{\"])([A-Za-z])\s+([a-z]{3,})(?=\b)", re.MULTILINE)
SPLIT_MIDDLE_RE = re.compile(
    r"\b([A-Za-z]{2,})\s+([aeiou])\b(?=\s+[A-Za-z]{2,}\s+[bcdfghjklmnpqrstvwxyz]\b)",
    re.IGNORECASE,
)
SPLIT_TAIL_RE = re.compile(r"\b([A-Za-z]{2,})\s+([bcdfghjklmnpqrstvwxyz])\b", re.IGNORECASE)
SPLIT_SUFFIX_RE = re.compile(
    r"\b([A-Za-z]{3,})\s+"
    r"(ify|ifies|ified|ifying|ise|ises|ised|ising|ize|izes|ized|izing|"
    r"ation|ations|ment|ments|ness|less|able|ably|ible|ibly|ally|fully|ously|ship|ships|ward|wards)\b",
    re.IGNORECASE,
)
MERGED_SINGLE_HEAD_RE = re.compile(r"\b([AI])([a-z]{3,})\b")
MERGED_COMMON_HEAD_RE = re.compile(r"\b(My|Your|Our|Their|This|That|These|Those|We|You)([a-z]{4,})\b")
MERGED_TRAILING_COMMON_RE = re.compile(
    r"\b([A-Za-z]{5,})(are|will|were|with|your|this|that|what|when|where|which|have|them|they)\b"
    r"(?=\s+(?:you|your|the|that|this|it|we|they|he|she|who|what|when|where|why|and|or|but|just|to|for|if|because|so|then)\b)",
    re.IGNORECASE,
)
CONTRACTION_BOUNDARY_RE = re.compile(
    r"((?:'s|'re|'ve|'ll|'d|'m))(?=(?:you|your|the|that|this|it|we|they|he|she|who|what|when|where|why)\b)",
    re.IGNORECASE,
)
TRAILING_ALPHA_RE = re.compile(r"([A-Za-z]+)$")
LEADING_ALPHA_RE = re.compile(r"^([A-Za-z]+)")
MERGED_COMMON_TOKEN_RE = re.compile(r"\b[A-Za-z]{4,24}\b")
COMMON_SHORT_WORDS = {
    "a", "i", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in", "is",
    "it", "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we",
    "for", "and", "but", "not", "the", "you", "your",
}
MERGEABLE_COMMON_WORDS = COMMON_SHORT_WORDS | {
    "are", "been", "before", "being", "because", "between", "can", "could", "did",
    "does", "every", "from", "have", "here", "how", "into", "just", "more", "much",
    "must", "never", "now", "onto", "only", "over", "right", "should", "since",
    "still", "than", "that", "their", "them", "then", "there", "these", "they",
    "this", "those", "through", "under", "until", "very", "was", "were", "what",
    "when", "where", "which", "while", "who", "why", "will", "with", "without",
    "would",
}
MERGEABLE_CONNECTOR_SUFFIXES = ("and",)
MERGED_TOKEN_BLOCKLIST = {
    "command", "commands", "demand", "demands", "expand", "expands", "grand", "brand",
    "island", "remand", "remands", "strand", "strands",
}
MERGED_TRAILING_BLOCKLIST = {
    "software", "hardware", "aware", "beware", "elsewhere", "somewhere", "anywhere", "nowhere",
}
FINAL_CLEANUP_MAX_CHARS = 2400
ALWAYS_MODEL_CLEANUP_MAX_CHARS = 1200
FRAGMENT_LINE_RE = re.compile(r"(?m)^[A-Za-z]{1,4}(?:\s+[A-Za-z]{1,4}){1,3}$")
GENERIC_SPLIT_FRAGMENT_RE = re.compile(r"\b([A-Za-z]{4,})\s+([a-z]{4,})\b")
SUSPICIOUS_FRAGMENT_STARTS = (
    "ation", "ational", "ations", "ative", "atively", "ality", "alities",
    "ment", "ments", "ness", "lessly", "less", "able", "ably", "ible", "ibly",
    "fully", "ously", "ology", "ologies", "tion", "tions", "sion", "sions",
    "ician", "icians", "preneur", "preneurs", "preneurial", "preneurship",
)

logger = logging.getLogger(__name__)


def _protect_spans(text: str) -> Tuple[str, Dict[str, str]]:
    protected: Dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        token = f"__CB_PROTECTED_{len(protected)}__"
        protected[token] = match.group(0)
        return token

    return PROTECTED_SPAN_RE.sub(_replace, text), protected


def _restore_spans(text: str, protected: Dict[str, str]) -> str:
    restored = text
    for token, value in protected.items():
        restored = restored.replace(token, value)
    return restored


def _repair_split_word_fragments(text: str) -> str:
    repaired = text

    repaired = SPLIT_HEAD_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}", repaired)

    while True:
        next_repaired = SPLIT_MIDDLE_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}"
            if m.group(1).lower() not in COMMON_SHORT_WORDS
            else m.group(0),
            repaired,
        )
        next_repaired = SPLIT_TAIL_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}"
            if m.group(1).lower() not in COMMON_SHORT_WORDS
            else m.group(0),
            next_repaired,
        )
        next_repaired = SPLIT_SUFFIX_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}"
            if m.group(1).lower() not in COMMON_SHORT_WORDS
            else m.group(0),
            next_repaired,
        )
        if next_repaired == repaired:
            break
        repaired = next_repaired

    return repaired


def _repair_merged_common_word_pairs(text: str) -> str:
    repaired = MERGED_SINGLE_HEAD_RE.sub(lambda m: f"{m.group(1)} {m.group(2)}", text)
    repaired = MERGED_COMMON_HEAD_RE.sub(lambda m: f"{m.group(1)} {m.group(2)}", repaired)
    repaired = MERGED_TRAILING_COMMON_RE.sub(
        lambda m: m.group(0)
        if m.group(0).lower() in MERGED_TRAILING_BLOCKLIST
        else f"{m.group(1)} {m.group(2)}",
        repaired,
    )

    def _split_token(match: re.Match[str]) -> str:
        token = match.group(0)
        lower = token.lower()
        if lower in MERGEABLE_COMMON_WORDS:
            return token

        for index in range(2, len(token) - 1):
            left = lower[:index]
            right = lower[index:]
            if left in MERGEABLE_COMMON_WORDS and right in MERGEABLE_COMMON_WORDS:
                return f"{token[:index]} {token[index:]}"

        if lower not in MERGED_TOKEN_BLOCKLIST:
            for suffix in MERGEABLE_CONNECTOR_SUFFIXES:
                if lower.endswith(suffix):
                    left = lower[: -len(suffix)]
                    if len(left) >= 4 and re.search(r"[aeiou]", left, re.IGNORECASE):
                        return f"{token[:len(left)]} {token[len(left):]}"
        return token

    return MERGED_COMMON_TOKEN_RE.sub(_split_token, repaired)


def _should_insert_boundary_space(left: str, right: str) -> bool:
    if not left or not right or left[-1].isspace() or right[0].isspace():
        return False
    if not left[-1].isalnum() or not right[0].isalnum():
        return False

    left_match = TRAILING_ALPHA_RE.search(left)
    right_match = LEADING_ALPHA_RE.search(right)
    if not left_match or not right_match:
        return False

    left_word = left_match.group(1)
    right_word = right_match.group(1)
    if not left_word or not right_word:
        return False

    if left_word.lower() in MERGEABLE_COMMON_WORDS and right_word.lower() in MERGEABLE_COMMON_WORDS:
        return True
    if left_word[-1].islower() and right_word[0].isupper():
        return True
    return False


def append_stream_text(existing: str, chunk: str) -> str:
    if not existing:
        return chunk
    if not chunk:
        return existing
    if _should_insert_boundary_space(existing, chunk):
        return f"{existing} {chunk}"
    return existing + chunk


def _sanitize_core(text: str, trim_line_edges: bool) -> str:
    cleaned, protected = _protect_spans(text)
    for token in MOJIBAKE_DASHES:
        cleaned = cleaned.replace(token, ", ")

    cleaned = CLAUSE_DASH_RE.sub(", ", cleaned)
    cleaned = WORD_BREAK_DASH_RE.sub(" ", cleaned)
    cleaned = WORD_CLAUSE_DASH_RE.sub(", ", cleaned)
    cleaned = INLINE_TIGHT_DASH_RE.sub(", ", cleaned)
    cleaned = REPEATED_COMMA_RE.sub(", ", cleaned)
    cleaned = COMMA_BEFORE_END_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = SPACE_BEFORE_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = LETTER_END_PUNCT_BOUNDARY_RE.sub(r"\1 ", cleaned)
    cleaned = LIST_NUMBER_SPACE_RE.sub(r"\1 ", cleaned)
    cleaned = BIBLE_VERSE_BOUNDARY_RE.sub(" ", cleaned)
    cleaned = WORD_TO_NUMBER_BOUNDARY_RE.sub(" ", cleaned)
    cleaned = WORD_TO_NUMBER_SUFFIX_BOUNDARY_RE.sub(" ", cleaned)
    cleaned = NUMBER_TO_WORD_BOUNDARY_RE.sub(" ", cleaned)
    cleaned = DOMAIN_BOUNDARY_RE.sub(" ", cleaned)
    cleaned = CONTRACTION_BOUNDARY_RE.sub(r"\1 ", cleaned)
    cleaned = _repair_split_word_fragments(cleaned)
    cleaned = _repair_merged_common_word_pairs(cleaned)
    cleaned = MULTISPACE_RE.sub(" ", cleaned)

    if trim_line_edges:
        lines = [line.strip() for line in cleaned.splitlines()]
        cleaned = "\n".join(lines).strip()

    return _restore_spans(cleaned, protected)


def strip_mid_sentence_hyphens(text: str) -> str:
    """Remove inline dash punctuation from generated prose while preserving links and bullets."""
    if not text:
        return text
    return _sanitize_core(text, trim_line_edges=True).strip()


def _alnum_skeleton(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", text or "").lower()


def _is_safe_cleanup_candidate(original: str, candidate: str) -> bool:
    if not candidate or not candidate.strip():
        return False

    original_skeleton = _alnum_skeleton(original)
    candidate_skeleton = _alnum_skeleton(candidate)
    if not original_skeleton or not candidate_skeleton:
        return False

    similarity = difflib.SequenceMatcher(None, original_skeleton, candidate_skeleton).ratio()
    if similarity < 0.995:
        return False

    allowed_delta = max(24, int(len(original) * 0.2))
    return abs(len(candidate) - len(original)) <= allowed_delta


def _run_final_spacing_cleanup_model(text: str) -> Optional[str]:
    try:
        from backend.rag import generate_chat_completion
        from backend.settings import settings

        prompt = (
            "Fix only formatting corruption in this message. "
            "This includes merged words, split words, missing spaces after punctuation, broken numbered lists, and paragraph spacing. "
            "Do not rewrite, summarize, add, remove, or change wording. "
            "Preserve the exact tone, sentence order, paragraph breaks, numbering, and punctuation unless a spacing fix requires a tiny punctuation adjustment. "
            "Return only the corrected message."
        )

        return generate_chat_completion(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            model=settings.MODEL_FALLBACK_SMART,
            temperature=0.0,
            max_tokens=min(600, max(120, len(text) // 2)),
        ).strip()
    except Exception as exc:
        logger.warning("Final spacing cleanup model pass failed: %s", exc)
        return None


def _youtube_video_id(url: str) -> str:
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
    if "youtu.be" in host:
        return (parsed.path or "").strip("/").split("/")[0]
    if "youtube.com" in host:
        query_id = parse_qs(parsed.query or "").get("v", [""])[0]
        if query_id:
            return query_id
        path = (parsed.path or "").strip("/")
        parts = path.split("/")
        if len(parts) >= 2 and parts[0].lower() == "shorts":
            return parts[1]
    return ""


def _alnum_lower(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", text or "").lower()


def strip_card_attachment_artifacts(text: str, cards) -> str:
    """
    Remove raw link/video-id fragments from prose when the same resources already
    exist as preview cards below the message.
    """
    if not text or not cards:
        return text

    cleaned = text
    card_video_ids = []
    for card in cards or []:
        url = (card or {}).get("url") or ""
        if not url:
            continue

        exact_variants = {
            url,
            url.rstrip("/"),
            url.replace("https://", ""),
            url.replace("http://", ""),
        }
        for variant in exact_variants:
            if variant:
                cleaned = cleaned.replace(variant, "")

        video_id = _youtube_video_id(url)
        if video_id and len(video_id) >= 8:
            card_video_ids.append(video_id)
            spaced_pattern = r"\b" + r"\s*".join(map(re.escape, video_id)) + r"\b"
            cleaned = re.sub(spaced_pattern, "", cleaned)
            cleaned = re.sub(rf"\b{re.escape(video_id)}\b", "", cleaned)

    if card_video_ids:
        lines = cleaned.splitlines()
        drop_indexes = set()
        normalized_ids = {_alnum_lower(video_id) for video_id in card_video_ids if video_id}
        for idx, line in enumerate(lines):
            line_key = _alnum_lower(line)
            if line_key and line_key in normalized_ids:
                drop_indexes.add(idx)
                continue
            if idx + 1 < len(lines):
                pair_key = _alnum_lower(f"{line}{lines[idx + 1]}")
                if pair_key and pair_key in normalized_ids:
                    drop_indexes.add(idx)
                    drop_indexes.add(idx + 1)
        if drop_indexes:
            cleaned = "\n".join(
                line for idx, line in enumerate(lines)
                if idx not in drop_indexes
            )

    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _has_suspicious_formatting(text: str) -> bool:
    if not text:
        return False
    if LETTER_END_PUNCT_BOUNDARY_RE.search(text):
        return True
    if CONTRACTION_BOUNDARY_RE.search(text):
        return True
    if SPLIT_HEAD_RE.search(text) or SPLIT_MIDDLE_RE.search(text) or SPLIT_TAIL_RE.search(text) or SPLIT_SUFFIX_RE.search(text):
        return True
    if MERGED_SINGLE_HEAD_RE.search(text) or MERGED_COMMON_HEAD_RE.search(text) or MERGED_TRAILING_COMMON_RE.search(text):
        return True
    if WORD_TO_NUMBER_BOUNDARY_RE.search(text) or WORD_TO_NUMBER_SUFFIX_BOUNDARY_RE.search(text) or NUMBER_TO_WORD_BOUNDARY_RE.search(text):
        return True
    if FRAGMENT_LINE_RE.search(text):
        return True
    for match in GENERIC_SPLIT_FRAGMENT_RE.finditer(text):
        left = match.group(1).lower()
        right = match.group(2).lower()
        if left in COMMON_SHORT_WORDS or right in COMMON_SHORT_WORDS:
            continue
        if any(right.startswith(prefix) for prefix in SUSPICIOUS_FRAGMENT_STARTS):
            return True
    for match in MERGED_COMMON_TOKEN_RE.finditer(text):
        token = match.group(0)
        if _repair_merged_common_word_pairs(token) != token:
            return True
    return False


def finalize_generated_text(text: str, allow_model_cleanup: bool = True) -> str:
    """
    Final answer normalization for user-visible model output.
    Runs deterministic cleanup first, then a cheap guarded model pass so arbitrary
    split/merged words can be corrected without hard-coding specific tokens.
    """
    base = strip_mid_sentence_hyphens(text)
    if not base or not allow_model_cleanup:
        return base
    if len(base) > FINAL_CLEANUP_MAX_CHARS:
        return base

    raw = (text or "").strip()
    if raw == base and len(base) > ALWAYS_MODEL_CLEANUP_MAX_CHARS and not _has_suspicious_formatting(base):
        return base

    candidate = _run_final_spacing_cleanup_model(base)
    if not candidate:
        return base

    candidate = strip_mid_sentence_hyphens(candidate)
    if not _is_safe_cleanup_candidate(base, candidate):
        return base
    return candidate


def sanitize_stream_fragment(text: str) -> str:
    """Sanitize streamed prose without trimming chunk edge whitespace."""
    if not text:
        return text

    leading_match = re.match(r"^\s*", text)
    trailing_match = re.search(r"\s*$", text)
    leading_ws = leading_match.group(0) if leading_match else ""
    trailing_ws = trailing_match.group(0) if trailing_match else ""
    start = len(leading_ws)
    end = len(text) - len(trailing_ws) if trailing_ws else len(text)
    middle = text[start:end]

    if not middle:
        return text

    cleaned_middle = _sanitize_core(middle, trim_line_edges=False)
    return f"{leading_ws}{cleaned_middle}{trailing_ws}"


class StreamingTextSanitizer:
    """Buffers streamed text so inline dashes can be cleaned before the user sees them."""

    def __init__(self, tail_size: int = 32):
        self._buffer = ""
        self._tail_size = max(8, tail_size)

    def feed(self, text: str) -> str:
        if not text:
            return ""

        self._buffer = append_stream_text(self._buffer, text)
        emit_upto = self._find_emit_boundary()
        if emit_upto <= 0:
            return ""

        safe_chunk = self._buffer[:emit_upto]
        self._buffer = self._buffer[emit_upto:]
        return sanitize_stream_fragment(safe_chunk)

    def flush(self) -> str:
        if not self._buffer:
            return ""
        safe_chunk = sanitize_stream_fragment(self._buffer)
        self._buffer = ""
        return safe_chunk

    def _find_emit_boundary(self) -> int:
        last_match = None
        for match in STREAM_BOUNDARY_RE.finditer(self._buffer):
            last_match = match
        if last_match:
            return last_match.end()

        if len(self._buffer) <= self._tail_size:
            return 0

        limit = len(self._buffer) - self._tail_size
        soft_break = max(
            self._buffer.rfind(" ", 0, limit),
            self._buffer.rfind("\t", 0, limit),
            self._buffer.rfind("\n", 0, limit),
        )
        if soft_break >= 0:
            return soft_break + 1
        return limit
