import re
from typing import Dict, Tuple

DASH_CHARS = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
WORD_BREAK_DASH_CHARS = "-\u2010\u2011\u2012\u2212"
CLAUSE_BREAK_DASH_CHARS = "\u2013\u2014\u2015"
DASH_CLASS = re.escape(DASH_CHARS)
WORD_BREAK_DASH_CLASS = re.escape(WORD_BREAK_DASH_CHARS)
CLAUSE_BREAK_DASH_CLASS = re.escape(CLAUSE_BREAK_DASH_CHARS)
MOJIBAKE_DASHES = ("\u00e2\u20ac\u201d", "\u00e2\u20ac\u201c")
PROTECTED_SPAN_RE = re.compile(r"\[[^\]]+\]\(https?://[^\s)]+\)|https?://[^\s)]+")
CLAUSE_DASH_RE = re.compile(
    rf"(?<=\S)(?:\s+(?:--+|[{DASH_CLASS}]+)\s*|\s*(?:--+|[{DASH_CLASS}]+)\s+)(?=\S)"
)
WORD_BREAK_DASH_RE = re.compile(rf"(?<=\w)(?:[{WORD_BREAK_DASH_CLASS}])(?=\w)")
WORD_CLAUSE_DASH_RE = re.compile(rf"(?<=\w)(?:--+|[{CLAUSE_BREAK_DASH_CLASS}]+)(?=\w)")
INLINE_TIGHT_DASH_RE = re.compile(rf"(?<=\S)(?:--+|[{DASH_CLASS}]+)(?=\S)")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")
REPEATED_COMMA_RE = re.compile(r",\s*,+")
COMMA_BEFORE_END_PUNCT_RE = re.compile(r",\s*([.!?])")
MULTISPACE_RE = re.compile(r"[ \t]{2,}")
LIST_NUMBER_SPACE_RE = re.compile(r"(?m)^(\s*\d+[.)])(?=\S)")
BIBLE_VERSE_BOUNDARY_RE = re.compile(r"(?<=[A-Za-z])(?=(?:[1-3]?\d{1,3}:\d{1,3}(?:-\d{1,3})?))")
DOMAIN_BOUNDARY_RE = re.compile(r"(?<=[A-Za-z])(?=(?:www\.)?(?:\d|[A-Z])[A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)+(?:/[^\s]*)?)")
STREAM_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|\n")


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
    cleaned = LIST_NUMBER_SPACE_RE.sub(r"\1 ", cleaned)
    cleaned = BIBLE_VERSE_BOUNDARY_RE.sub(" ", cleaned)
    cleaned = DOMAIN_BOUNDARY_RE.sub(" ", cleaned)
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

        self._buffer += text
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
