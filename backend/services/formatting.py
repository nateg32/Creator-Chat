import json
import re
from typing import Any


_TIMESTAMP_PATTERN = re.compile(r"\b\d{1,2}:\d{2}\b")
_TRANSCRIPT_TAG_PATTERN = re.compile(r"\[[\w\s]{1,30}\]")
_STAGE_MARKER_PATTERN = re.compile(
    r"\bStage\s+(?:one|two|three|four|five|six|seven|eight|nine|ten)\s*,?\s*",
    flags=re.IGNORECASE,
)

# Emoji handling is deliberately split into precise passes so variation selectors
# and joiners do not inject spaces into adjacent words like "A\uFE0Fmazon".
_KEYCAP_PATTERN = re.compile(r"(?:[#*0-9]\uFE0F?\u20E3)", flags=re.UNICODE)
_FLAG_PATTERN = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}", flags=re.UNICODE)
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F004"
    "\U0001F0CF"
    "\U0001F170-\U0001F171"
    "\U0001F17E-\U0001F17F"
    "\U0001F18E"
    "\U00003030"
    "\U00002B50"
    "\U00002B55"
    "]{1}",
    flags=re.UNICODE,
)
_VARIATION_SELECTOR_PATTERN = re.compile(r"\uFE0F", flags=re.UNICODE)
_ZERO_WIDTH_JOINER_PATTERN = re.compile(r"\u200D", flags=re.UNICODE)


def remove_emojis_safely(text: str) -> str:
    """
    Remove emoji without consuming adjacent text.

    Actual emoji glyphs are replaced with a space so neighboring words do not
    fuse together. Formatting characters like variation selectors and joiners
    are removed without a space so they cannot split a word.
    """
    if not text:
        return ""

    text = _KEYCAP_PATTERN.sub(" ", text)
    text = _FLAG_PATTERN.sub(" ", text)
    text = _EMOJI_PATTERN.sub(" ", text)
    text = _ZERO_WIDTH_JOINER_PATTERN.sub("", text)
    text = _VARIATION_SELECTOR_PATTERN.sub("", text)
    return text


def clean_response(text: str, strip_hyphens: bool = False) -> str:
    """
    Single centralised cleaner. Called once on complete response text.
    Never called on individual stream chunks.
    """
    if not text:
        return ""

    text = _TIMESTAMP_PATTERN.sub("", text)
    text = _TRANSCRIPT_TAG_PATTERN.sub("", text)
    text = _STAGE_MARKER_PATTERN.sub("", text)
    text = remove_emojis_safely(text)

    if strip_hyphens:
        text = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text)

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,\.!?;:])", r"\1", text)
    text = re.sub(r"([\(\[]) +", r"\1", text)
    text = re.sub(r" +([\)\]])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)

    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\[\s*\]", "", text)
    text = re.sub(r"[-–—]\s*[-–—]", "—", text)
    text = re.sub(r"\.\s*,", ".", text)
    text = re.sub(r",\s*\.", ".", text)

    return text.strip()


def clean_for_stream_chunk(chunk: str) -> str:
    """
    Minimal safe cleaning for individual stream chunks.

    Only transcript artifacts are removed here because they cannot safely span
    chunk boundaries. Emoji removal, hyphen stripping, and whitespace collapse
    only run on the fully assembled response.
    """
    if not chunk:
        return chunk
    chunk = _TIMESTAMP_PATTERN.sub("", chunk)
    chunk = _TRANSCRIPT_TAG_PATTERN.sub("", chunk)
    return chunk


def should_strip_hyphens(creator: Any) -> bool:
    """
    Hyphen stripping is off by default and only enabled when the creator's
    persisted voice patterns explicitly request it.
    """
    if not creator:
        return False

    payload = creator if isinstance(creator, dict) else {}
    voice_patterns = payload.get("voice_patterns") or {}
    if isinstance(voice_patterns, str):
        try:
            voice_patterns = json.loads(voice_patterns)
        except Exception:
            voice_patterns = {}

    if not isinstance(voice_patterns, dict):
        return False

    rhythm = voice_patterns.get("rhythm", {})
    if not isinstance(rhythm, dict):
        return False

    return bool(rhythm.get("strip_hyphens", False))


def _formatting_smoke_test() -> None:
    """
    Catch regex regressions immediately at import time.
    """
    cases = [
        ("Amazon", "Amazon"),
        ("Audible", "Audible"),
        ("\U0001f4a1Amazon", "Amazon"),
        ("A\ufe0fmazon", "Amazon"),
        ("Here's the thing \U0001f525 invest", "invest"),
        ("don't", "don't"),
        ("non-negotiable", "non"),
    ]
    for input_text, must_contain in cases:
        result = clean_response(input_text)
        assert must_contain in result, (
            "FORMATTING SMOKE TEST FAILED\n"
            f"Input:        {input_text!r}\n"
            f"Output:       {result!r}\n"
            f"Must contain: {must_contain!r}\n"
            "Fix backend/services/formatting.py before deploying."
        )


_formatting_smoke_test()
