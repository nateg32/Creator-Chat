import json
import re
from typing import Any


_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002600-\U000027BF"
    "\U0001F900-\U0001F9FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F004"
    "\U0001F0CF"
    "\U0001F170-\U0001F171"
    "\U0001F17E-\U0001F17F"
    "\U0001F18E"
    "\U00003030"
    "\U00002B50"
    "\U00002B55"
    "\U0000200D"
    "\U000020E3"
    "\U0000FE0F"
    "]+",
    flags=re.UNICODE,
)

_TIMESTAMP_PATTERN = re.compile(r"\b\d{1,2}:\d{2}\b")
_TRANSCRIPT_TAG_PATTERN = re.compile(r"\[[\w\s]{1,30}\]")
_STAGE_MARKER_PATTERN = re.compile(
    r"\bStage\s+(?:one|two|three|four|five|six|seven|eight|nine|ten)\s*,?\s*",
    flags=re.IGNORECASE,
)


def clean_response(text: str, strip_hyphens: bool = False) -> str:
    """
    Single centralised response cleaner for Creator Bot.

    Applied ONCE to the complete response after generation.
    Never applied chunk by chunk during streaming.
    """
    if not text:
        return ""

    text = _TIMESTAMP_PATTERN.sub("", text)
    text = _TRANSCRIPT_TAG_PATTERN.sub("", text)
    text = _STAGE_MARKER_PATTERN.sub("", text)
    text = _EMOJI_PATTERN.sub(" ", text)

    if strip_hyphens:
        text = re.sub(r"(?<=[a-zA-Z])-(?=[a-zA-Z])", " ", text)

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
    Minimal cleaning safe to apply to individual stream chunks.

    Only removes artifacts that cannot safely cross chunk boundaries.
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
