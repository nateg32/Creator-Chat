import re

DASH_CHARS = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
DASH_CLASS = re.escape(DASH_CHARS)
MOJIBAKE_DASHES = ("\u00e2\u20ac\u201d", "\u00e2\u20ac\u201c")

CLAUSE_DASH_RE = re.compile(
    rf"(?<=\S)(?:\s+(?:--+|[{DASH_CLASS}]+)\s*|\s*(?:--+|[{DASH_CLASS}]+)\s+)(?=\S)"
)
WORD_DASH_RE = re.compile(rf"(?<=\w)(?:--+|[{DASH_CLASS}])(?=\w)")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")
REPEATED_COMMA_RE = re.compile(r",\s*,+")
COMMA_BEFORE_END_PUNCT_RE = re.compile(r",\s*([.!?])")
MULTISPACE_RE = re.compile(r"[ \t]{2,}")


def strip_mid_sentence_hyphens(text: str) -> str:
    """Remove inline hyphen and dash punctuation from generated replies."""
    if not text:
        return text

    cleaned = text
    for token in MOJIBAKE_DASHES:
        cleaned = cleaned.replace(token, " ")

    cleaned = CLAUSE_DASH_RE.sub(", ", cleaned)
    cleaned = WORD_DASH_RE.sub(" ", cleaned)
    cleaned = REPEATED_COMMA_RE.sub(", ", cleaned)
    cleaned = COMMA_BEFORE_END_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = SPACE_BEFORE_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = MULTISPACE_RE.sub(" ", cleaned)

    lines = [line.strip() for line in cleaned.splitlines()]
    return "\n".join(lines).strip()
