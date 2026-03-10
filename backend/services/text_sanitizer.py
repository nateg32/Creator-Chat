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


def strip_mid_sentence_hyphens(text: str) -> str:
    """Remove inline dash punctuation from generated prose while preserving links and bullets."""
    if not text:
        return text

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
    cleaned = MULTISPACE_RE.sub(" ", cleaned)

    lines = [line.strip() for line in cleaned.splitlines()]
    cleaned = "\n".join(lines).strip()
    return _restore_spans(cleaned, protected)


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
        if emit_upto <= 0 and len(self._buffer) > self._tail_size:
            emit_upto = len(self._buffer) - self._tail_size

        if emit_upto <= 0:
            return ""

        safe_chunk = self._buffer[:emit_upto]
        self._buffer = self._buffer[emit_upto:]
        return strip_mid_sentence_hyphens(safe_chunk)

    def flush(self) -> str:
        if not self._buffer:
            return ""
        safe_chunk = strip_mid_sentence_hyphens(self._buffer)
        self._buffer = ""
        return safe_chunk

    def _find_emit_boundary(self) -> int:
        last_match = None
        for match in STREAM_BOUNDARY_RE.finditer(self._buffer):
            last_match = match
        return last_match.end() if last_match else 0
