import json
import re
from typing import Any
from urllib.parse import urlparse


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
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", re.IGNORECASE)
_HTTP_URL_PATTERN = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
_BARE_DOMAIN_PATTERN = re.compile(
    r"(?<![@/\w])(?:www\.)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s)\]>\"']*)?",
    re.IGNORECASE,
)
_LIST_LINE_PATTERN = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*", re.MULTILINE)
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")
_GENERIC_CARD_LABELS = {
    "external resource",
    "youtube video",
    "youtube short",
    "instagram reel",
    "instagram post",
    "tiktok video",
    "facebook video",
    "tweet",
    "video",
    "article",
    "source",
    "resource",
}


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

    # Strip empty/placeholder URL artifacts the model may hallucinate (e.g. "" or ""/books)
    text = re.sub(r'(?:(?:at|to|is|visit|on)\s+)?""(?:/\S*)?', '', text)
    text = re.sub(r"(?:(?:at|to|is|visit|on)\s+)?''(?:/\S*)?", '', text)
    # Clean up leftover double spaces from removals
    text = re.sub(r"[ \t]{2,}", " ", text)

    return text.strip()


def _trim_reference(value: str) -> str:
    cleaned = str(value or "").strip()
    while cleaned and cleaned[-1] in '.,!?;:)]':
        cleaned = cleaned[:-1]
    return cleaned


def _normalize_reference_url(value: str) -> str:
    cleaned = _trim_reference(value)
    if not cleaned:
        return ""
    if not cleaned.lower().startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"
    return cleaned


def _display_label_for_card(card: Any) -> str:
    url = _normalize_reference_url((card or {}).get("url") or "")
    title = re.sub(r"\s+", " ", str((card or {}).get("title") or "").strip())
    lowered_title = title.lower()
    if title and lowered_title not in _GENERIC_CARD_LABELS:
        return title
    host = (urlparse(url).netloc or "").replace("www.", "")
    return host or title or "this link"


def _rewrite_card_references(text: str, cards: Any) -> str:
    if not text or not cards:
        return text

    rewritten = str(text)
    reference_replaced = False

    for card in cards or []:
        label = _display_label_for_card(card)
        quoted_label = f'"{label}"'
        normalized_url = _normalize_reference_url((card or {}).get("url") or "")
        if not normalized_url:
            continue

        domain = (urlparse(normalized_url).netloc or "").replace("www.", "")
        variants = {
            normalized_url,
            normalized_url.rstrip("/"),
            normalized_url.replace("https://", ""),
            normalized_url.replace("http://", ""),
            domain,
            f"www.{domain}" if domain and not domain.startswith("www.") else "",
        }
        variants = {variant for variant in variants if variant}

        for variant in sorted(variants, key=len, reverse=True):
            pattern = re.compile(rf"(?<![\w@/]){re.escape(variant)}(?![\w])", re.IGNORECASE)
            rewritten, count = pattern.subn(quoted_label, rewritten)
            if count:
                reference_replaced = True

        markdown_pattern = re.compile(
            rf"\[[^\]]+\]\({re.escape(normalized_url)}\)",
            re.IGNORECASE,
        )
        rewritten, markdown_count = markdown_pattern.subn(quoted_label, rewritten)
        if markdown_count:
            reference_replaced = True

    rewritten = _MARKDOWN_LINK_PATTERN.sub(lambda match: f'"{match.group(1).strip()}"', rewritten)
    rewritten = clean_response(rewritten)

    if reference_replaced:
        attached_pattern = re.compile(r"\b(attached|below|link below|links below|card below|cards below)\b", re.IGNORECASE)
        if not attached_pattern.search(rewritten):
            suffix = "I've attached the link below." if len(cards or []) == 1 else "I've attached the links below."
            rewritten = f"{rewritten} {suffix}".strip()
            rewritten = clean_response(rewritten)

    return rewritten


def _normalize_list_lines(text: str) -> str:
    if not text:
        return ""
    normalized_lines = []
    for raw_line in str(text).splitlines():
        line = raw_line.rstrip()
        bullet_match = re.match(r"^\s*([-*•])\s*(.*)$", line)
        number_match = re.match(r"^\s*(\d+[.)])\s*(.*)$", line)
        if bullet_match:
            normalized_lines.append(f"{bullet_match.group(1)} {bullet_match.group(2).strip()}".rstrip())
        elif number_match:
            normalized_lines.append(f"{number_match.group(1)} {number_match.group(2).strip()}".rstrip())
        else:
            normalized_lines.append(line.strip())
    cleaned = "\n".join(normalized_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _paragraphize_prose(text: str) -> str:
    if not text:
        return ""

    blocks = re.split(r"\n{2,}", str(text).strip())
    rebuilt_blocks: list[str] = []

    for block in blocks:
        stripped_block = block.strip()
        if not stripped_block:
            continue

        lines = [line.strip() for line in stripped_block.splitlines() if line.strip()]
        if len(lines) > 1 and any(_LIST_LINE_PATTERN.match(line) for line in lines):
            rebuilt_blocks.append("\n".join(lines))
            continue

        prose = " ".join(lines)
        prose = re.sub(r"\s+", " ", prose).strip()
        sentences = [segment.strip() for segment in _SENTENCE_SPLIT_PATTERN.split(prose) if segment.strip()]

        if len(sentences) <= 2 and len(prose) <= 260:
            rebuilt_blocks.append(prose)
            continue

        if len(sentences) >= 3 and sentences[-1].endswith("?"):
            question = sentences.pop()
        else:
            question = ""

        paragraph_parts: list[str] = []
        current: list[str] = []
        current_len = 0
        target_chars = 210

        for sentence in sentences:
            proposed_len = current_len + len(sentence) + (1 if current else 0)
            if current and (len(current) >= 2 or proposed_len > target_chars):
                paragraph_parts.append(" ".join(current).strip())
                current = [sentence]
                current_len = len(sentence)
            else:
                current.append(sentence)
                current_len = proposed_len

        if current:
            paragraph_parts.append(" ".join(current).strip())
        if question:
            paragraph_parts.append(question)

        rebuilt_blocks.append("\n\n".join(part for part in paragraph_parts if part))

    return "\n\n".join(rebuilt_blocks).strip()


def prepare_chat_response(
    text: str,
    *,
    cards: Any = None,
    strip_hyphens: bool = False,
    allow_model_cleanup: bool = True,
) -> str:
    """
    Final user-visible presentation cleanup for chat responses.

    This is the single place that should shape prose for display:
    - deterministic cleanup
    - card-aware link rewriting (no raw URLs in text)
    - guarded final spacing/fragment repair
    """
    cleaned = clean_response(text, strip_hyphens=strip_hyphens)
    cleaned = _rewrite_card_references(cleaned, cards)
    cleaned = _normalize_list_lines(cleaned)
    cleaned = _paragraphize_prose(cleaned)

    if cards:
        from backend.services.text_sanitizer import strip_card_attachment_artifacts

        cleaned = strip_card_attachment_artifacts(cleaned, cards)
        cleaned = clean_response(cleaned, strip_hyphens=strip_hyphens)
        cleaned = _normalize_list_lines(cleaned)
        cleaned = _paragraphize_prose(cleaned)

    from backend.services.text_sanitizer import finalize_generated_text

    cleaned = finalize_generated_text(cleaned, allow_model_cleanup=allow_model_cleanup)
    cleaned = clean_response(cleaned, strip_hyphens=strip_hyphens)
    cleaned = _normalize_list_lines(cleaned)
    cleaned = _paragraphize_prose(cleaned)
    return cleaned


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
