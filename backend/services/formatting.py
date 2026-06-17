import json
import os
import re
import sys
from typing import Any, List
from urllib.parse import urlparse


def _load_text_sanitizer():
    """Resolve ``backend.services.text_sanitizer`` even when test stubs have
    replaced the parent package with one whose ``__path__`` no longer points
    at the real ``backend/services`` directory on disk.
    """
    mod = sys.modules.get("backend.services.text_sanitizer")
    if mod is not None and getattr(mod, "finalize_generated_text", None) is not None:
        return mod
    try:
        from backend.services import text_sanitizer as mod  # type: ignore
        return mod
    except (ImportError, ModuleNotFoundError):
        pass
    import importlib.util
    real_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "text_sanitizer.py")
    spec = importlib.util.spec_from_file_location(
        "backend.services.text_sanitizer", real_path
    )
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError("backend.services.text_sanitizer")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backend.services.text_sanitizer"] = mod
    spec.loader.exec_module(mod)
    return mod


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
# Split on sentence-ending punctuation but protect common abbreviations
# like e.g., i.e., etc., vs., Dr., Mr., Mrs., St. from being treated as
# sentence boundaries.
_ABBREV_PLACEHOLDER = "\x00ABBR\x00"
_ABBREVIATIONS_RE = re.compile(
    r"\b(e\.g|i\.e|etc|vs|Dr|Mr|Mrs|Ms|St|Jr|Sr|Prof|Inc|Corp|Ltd|Vol|No|approx)\.\s",
    re.IGNORECASE,
)
_DECIMAL_PLACEHOLDER = "\x00DEC\x00"
_DECIMAL_RE = re.compile(r"(\d+\.\d+)")
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


# Inline citation markers like [1], [2][3], [12] etc. that the model is
# instructed to append after factual claims. They are stripped from the
# user-visible text and replaced by structured citation cards downstream.
_CITATION_MARKER_PATTERN = re.compile(r"\s?\[(\d{1,3})\](?:\s*\[(\d{1,3})\])*")
_BRACKETED_DOMAIN_SOURCE_PATTERN = re.compile(
    r"\s*\[\s*(?:https?://)?(?:www\.)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\]\s]*)?\s*\]",
    re.IGNORECASE,
)
_PAREN_DOMAIN_SOURCE_PATTERN = re.compile(
    r"(?<!\])\s*\(\s*(?:https?://)?(?:www\.)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\)\s]*)?\s*\)",
    re.IGNORECASE,
)
_CENSORED_WORD_SPACING_PATTERNS = (
    (re.compile(r"\b([A-Za-z]{3,})(f\*+?ck(?:ed|ing)?|f\*+?k(?:ed|ing)?|fuck(?:ed|ing)?|fuk(?:ed|ing)?)\b", re.IGNORECASE), r"\1 \2"),
    (re.compile(r"\b(get|got|gets|getting|feel|feels|felt|feeling)(f\*+?cked|f\*+?ked)\b", re.IGNORECASE), r"\1 \2"),
    (re.compile(r"\b(get|got|gets|getting|feel|feels|felt|feeling)(fucked|fuked)\b", re.IGNORECASE), r"\1 \2"),
)
_STANDALONE_CONTENT_HOOKS = (
    "if you know you know",
    "bro needs to see this",
)
_THIRD_PERSON_SELF_OPENER_RE = re.compile(
    r"(?is)^\s*"
    r"(?:[A-Z][A-Za-z0-9'&.-]*(?:\s+[A-Z][A-Za-z0-9'&.-]*){0,4})\s+"
    r"is\s+(?:here|back|in\s+the\s+(?:building|house))\b"
    r"[^.!?\n]{0,220}(?:[.!?]+|\n|$)\s*"
)
_LOW_TRUST_HANDOFF_RE = re.compile(
    r"(?is)"
    r"(?:^|(?<=[.!?])\s+|\s*)"
    r"(?:\(\s*)?"
    r"(?:you\s+can\s+)?(?:google|look\s+up|search)\s+"
    r"(?:it|that|this|them|me|my\s+name|the\s+creator)"
    r"(?:\s+(?:yourself|if\s+you\s+want|for\s+yourself))?"
    r"(?:\s*\))?"
    r"\s*[.!?]?"
)


def strip_citation_markers(text: str) -> str:
    """Remove inline [n] / [n][m] citation markers from a string."""
    if not text:
        return text
    return _CITATION_MARKER_PATTERN.sub("", text)


def strip_source_domain_markers(text: str) -> str:
    """Remove source-marker domains from prose while leaving bare domains intact."""
    if not text:
        return text
    text = _BRACKETED_DOMAIN_SOURCE_PATTERN.sub("", text)
    return _PAREN_DOMAIN_SOURCE_PATTERN.sub("", text)


def soften_transcript_hooks(text: str) -> str:
    """Remove standalone content hooks that read like pasted transcript."""
    if not text:
        return text

    cleaned = text
    hook_pattern = "|".join(re.escape(hook) for hook in _STANDALONE_CONTENT_HOOKS)
    cleaned = re.sub(rf"(?im)^\s*(?:{hook_pattern})\.?\s*$", "", cleaned)
    cleaned = re.sub(rf"(?i)([.!?])\s+(?:{hook_pattern})\.?(?=\s|$)", r"\1", cleaned)
    cleaned = re.sub(rf"(?i)^\s*(?:{hook_pattern})\.?\s+", "", cleaned)
    return cleaned


def remove_third_person_self_opener(text: str) -> str:
    """Drop canned third-person creator intros at the start of a reply."""
    if not text:
        return text
    match = _THIRD_PERSON_SELF_OPENER_RE.match(text)
    if not match:
        return text
    remainder = text[match.end():].lstrip()
    return remainder if remainder else text


def strip_low_trust_handoffs(text: str) -> str:
    """Remove low-trust handoffs like '(you can google it)' from creator replies."""
    if not text:
        return text
    cleaned = _LOW_TRUST_HANDOFF_RE.sub(" ", text)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_citation_marker_indices(text: str) -> List[int]:
    """Return ordered de-duplicated list of [n] indices that appear in text."""
    if not text:
        return []
    seen: List[int] = []
    for match in re.finditer(r"\[(\d{1,3})\]", text):
        try:
            idx = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if idx not in seen:
            seen.append(idx)
    return seen


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
    text = strip_citation_markers(text)
    text = strip_source_domain_markers(text)
    text = remove_emojis_safely(text)
    text = _load_text_sanitizer().strip_identity_disclosure(text)
    for pattern, replacement in _CENSORED_WORD_SPACING_PATTERNS:
        text = pattern.sub(replacement, text)
    text = soften_transcript_hooks(text)
    text = remove_third_person_self_opener(text)
    text = strip_low_trust_handoffs(text)

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


def _short_card_label(card: Any, *, max_chars: int = 88) -> str:
    label = _display_label_for_card(card)
    label = re.sub(r"\s+", " ", label).strip()
    label = re.sub(
        r"\s*(?:[-|–—]\s*)?(?:YouTube|Apple Podcasts|Spotify|Instagram|TikTok)\s*$",
        "",
        label,
        flags=re.IGNORECASE,
    ).strip()
    if len(label) <= max_chars:
        return label
    trimmed = label[: max_chars - 1].rstrip(" ,.;:-")
    return f"{trimmed}..."


def _strip_platform_suffix(label: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(label or "")).strip()
    cleaned = re.sub(
        r"\s*(?:[-|â€“â€”]\s*)?(?:YouTube|Apple Podcasts|Spotify|Instagram|TikTok|LinkedIn|Audible(?:\.com)?)\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned


_TITLE_NAME_RE = r"[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){0,4}"


def _guest_from_title(title: str) -> str:
    title = _strip_platform_suffix(title)
    matches = list(re.finditer(
        rf"\bwith\s+({_TITLE_NAME_RE})\b",
        title,
        flags=re.IGNORECASE,
    ))
    if not matches:
        return ""
    guest = matches[-1].group(1).strip()
    return re.sub(r"\s+(?:podcast|show|interview|episode)$", "", guest, flags=re.IGNORECASE).strip()


def _speech_case_title_fragment(value: str) -> str:
    """Convert title-case metadata into a natural sentence fragment."""
    cleaned = _strip_platform_suffix(value)
    cleaned = re.sub(r"\s*\([^)]*\b(?:interview|podcast|episode|video)\b[^)]*\)\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*(?:\||[-–—])\s*[^|–—-]*\b(?:interview|podcast|episode|video)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("&", "and")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:-")
    if not cleaned:
        return ""

    words = []
    for token in cleaned.split(" "):
        prefix = re.match(r"^[^A-Za-z0-9$]*", token).group(0)
        suffix = re.search(r"[^A-Za-z0-9.']*$", token).group(0)
        core = token[len(prefix): len(token) - len(suffix) if suffix else len(token)]
        lowered_core = core
        if core in {"I", "I'm", "I'd", "I've", "I'll"}:
            lowered_core = core
        elif "." in core:
            lowered_core = core
        elif re.search(r"\d|\$", core):
            lowered_core = core.lower()
        elif core.isupper() and len(core) <= 4:
            lowered_core = core
        else:
            lowered_core = core.lower()
        words.append(f"{prefix}{lowered_core}{suffix}")

    fragment = " ".join(words)
    fragment = re.sub(r"\bi\b", "I", fragment)
    fragment = re.sub(r"\bi('(?:m|d|ve|ll))\b", lambda m: f"I{m.group(1)}", fragment, flags=re.IGNORECASE)
    return fragment.strip()


def _first_person_title_label(title: str) -> str:
    """Translate creator-owned resource titles into how the creator would say them."""
    label = _speech_case_title_fragment(title)
    if not label:
        return ""

    match = re.match(r"(?i)^from\s+(.+?)\s+to\s+(.+)$", label)
    if match:
        start = match.group(1).strip()
        finish = match.group(2).strip()
        return f"how I went from {start} to {finish}"

    match = re.match(r"(?i)^how\s+I\s+(.+)$", label)
    if match:
        return f"how I {match.group(1).strip()}"

    match = re.match(r"(?i)^why\s+I\s+(.+)$", label)
    if match:
        return f"why I {match.group(1).strip()}"

    match = re.match(r"(?i)^what\s+is\s+(.+?)\s+and\s+why\s+I\s+started\s+it$", label)
    if match:
        subject = match.group(1).strip()
        return f"my breakdown of what {subject} is and why I started it"

    match = re.match(r"(?i)^I\s+(.+?)\s+(?:then|and)\s+(.+)$", label)
    if match:
        return f"the story of how I {match.group(1).strip()} and {match.group(2).strip()}"

    if re.match(r"(?i)^I\s+", label):
        return f"my story about {label}"

    return ""


def _natural_card_label(card: Any) -> str:
    """Turn source-card titles into first-person phrases for creator chat prose."""
    label = _strip_platform_suffix(_display_label_for_card(card))
    if not label:
        return ""

    paid_day_pattern = re.compile(
        rf"^I\s+Paid\s+{_TITLE_NAME_RE}\s+.*?\bSpend\s+A\s+Day\s+With\s+Him\b",
        flags=re.IGNORECASE,
    )
    if paid_day_pattern.search(label):
        return "the episode where someone paid to spend a day with me"

    creator_prefix = re.match(rf"^{_TITLE_NAME_RE}\s*:\s*(.+)$", label)
    if creator_prefix:
        remainder = creator_prefix.group(1).strip()
        guest = _guest_from_title(remainder)
        if guest:
            return f"the interview I did with {guest}"
        first_person = _first_person_title_label(remainder)
        return first_person or remainder

    guest = _guest_from_title(label)
    if guest and re.search(r"\b(interview|podcast|episode|show)\b", label, flags=re.IGNORECASE):
        return f"the interview I did with {guest}"

    first_person = _first_person_title_label(label)
    if first_person:
        return first_person

    return label


def _capitalize_sentence_start(value: str) -> str:
    value = str(value or "")
    return value[:1].upper() + value[1:] if value else value


def _episode_ref_label(label: str) -> str:
    match = re.search(r"\bEp(?:isode)?\.?\s*#?\s*(\d{1,5})\b", str(label or ""), flags=re.IGNORECASE)
    if match:
        return f"Ep {match.group(1)}"
    return ""


def _loose_label_pattern(label: str) -> str:
    parts = [part for part in re.split(r"\s+", str(label or "").strip()) if part]
    if not parts:
        return ""
    return r"\s+".join(re.escape(part) for part in parts)


def _remove_duplicate_title_handoff_tail(text: str, raw_label: str) -> str:
    label_pattern = _loose_label_pattern(raw_label)
    if not text or not label_pattern:
        return text

    reference_patterns = [label_pattern]
    episode_ref = _episode_ref_label(raw_label)
    if episode_ref:
        reference_patterns.append(_loose_label_pattern(episode_ref))

    has_handoff = any(
        re.search(
            rf"(?is)\b(?:attached|included|linked|watch|listen\s+to|check\s+out)\b[^.!?]{{0,260}}"
            rf"(?:\"{pattern}\"|{pattern})",
            text,
        )
        for pattern in reference_patterns
        if pattern
    )
    if not has_handoff:
        return text

    cleaned, duplicate_count = re.subn(
        rf"(?is)(?:^|(?<=[.!?])\s+)\"?{label_pattern}\"?\s+"
        rf"(?:if\s+you\s+want|if\s+you'd\s+like|if\s+you\s+want\s+to)\b[^.!?]{{0,180}}[.!?]",
        " ",
        text,
    )
    if duplicate_count:
        cleaned = re.sub(
            r"(?is)(?:^|(?<=[.!?])\s+)if\s+you\s+want\s+to\s+"
            r"(?:listen|watch|read|see|hear|check(?:\s+out)?|dig\s+into)\b[^.!?]{0,180}[.!?]",
            " ",
            cleaned,
        )
        return clean_response(cleaned)
    return text


def _naturalize_source_title_artifacts(text: str, cards: Any) -> str:
    if not text:
        return text

    cleaned = str(text)
    cleaned = re.sub(
        rf"\bIn\s+the\s+interview\s+{_TITLE_NAME_RE}\s*:\s*[^,\n]{{0,180}}?\bwith\s+({_TITLE_NAME_RE})\s*,\s*I\b",
        lambda match: f"In the interview I did with {match.group(1).strip()}, I",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        rf"\bI\s+Paid\s+{_TITLE_NAME_RE}\s+.*?\bSpend\s+A\s+Day\s+With\s+Him\b",
        "The episode where someone paid to spend a day with me",
        cleaned,
        flags=re.IGNORECASE,
    )

    for card in cards or []:
        natural = _natural_card_label(card)
        if not natural:
            continue
        raw_labels = {
            _display_label_for_card(card),
            _short_card_label(card, max_chars=180),
            _strip_platform_suffix(_display_label_for_card(card)),
            _strip_platform_suffix(_short_card_label(card, max_chars=180)),
        }
        raw_labels = {
            re.sub(r"\s+", " ", label).strip()
            for label in raw_labels
            if label and label.strip()
        }
        for raw_label in sorted(raw_labels, key=len, reverse=True):
            escaped = re.escape(raw_label)
            cleaned = re.sub(
                rf"\bIn\s+the\s+interview\s+{escaped}\s*,\s*I\b",
                f"In {natural}, I",
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(
                rf"\bIn\s+the\s+(?:video|episode|podcast)\s+{escaped}\s*,",
                f"In {natural},",
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(
                rf"\b{escaped}\s+(shows|covers|breaks down|is where|walks through)\b",
                lambda match, label=natural: f"{_capitalize_sentence_start(label)} {match.group(1)}",
                cleaned,
                flags=re.IGNORECASE,
            )
            handoff = _source_handoff_sentence([card])
            cleaned = re.sub(
                rf"\bI\s+attached\s+(?:the\s+)?(?:video|episode|podcast|post|article|source)\s+\"{escaped}\""
                rf"(?:\s+if\s+you\s+want[^.!?]*)?(?:[.!?]\s*where\s+I\s+talk\s+about[^.!?]*)?[.!?]?",
                handoff,
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = _remove_duplicate_title_handoff_tail(cleaned, raw_label)
            cleaned = re.sub(
                rf"(?m)^([\-*]\s+){escaped}\b",
                lambda match, label=natural: f"{match.group(1)}{_capitalize_sentence_start(label)}",
                cleaned,
                flags=re.IGNORECASE,
            )

    return cleaned


def _card_resource_kind(card: Any) -> str:
    normalized_url = _normalize_reference_url((card or {}).get("url") or "")
    parsed = urlparse(normalized_url)
    host = (parsed.netloc or "").lower().replace("www.", "")
    path = (parsed.path or "").lower()
    title = str((card or {}).get("title") or "").lower()
    blob = f"{host} {path} {title}"

    if any(marker in blob for marker in ("youtube.com", "youtu.be", "vimeo.com", "video", "watch")):
        return "video"
    if any(marker in blob for marker in ("podcast", "spotify.com", "podcasts.apple.com", "libsyn.com")):
        return "episode"
    if any(marker in blob for marker in ("instagram.com", "tiktok.com", "shorts", "reel")):
        return "post"
    if any(marker in blob for marker in ("article", "blog", "substack.com", "medium.com")):
        return "article"
    return "source"


def _source_handoff_sentence(cards: Any) -> str:
    visible_cards = [card for card in (cards or []) if (card or {}).get("url") or (card or {}).get("title")]
    if not visible_cards:
        return ""

    if len(visible_cards) > 1:
        return "I attached a few of my sources if you want to dig deeper."

    card = visible_cards[0]
    natural_label = _natural_card_label(card)
    label = natural_label or _short_card_label(card)
    kind = _card_resource_kind(card)
    if natural_label and re.match(r"(?i)^(?:how|why|when|where|what)\s+I\b", natural_label):
        action = "Listen to" if kind == "episode" else "Watch" if kind == "video" else "Check out"
        return f"{action} {label} if you want the deeper context."

    if natural_label and re.match(r"(?i)^(?:the|my)\s+", natural_label):
        return f"I attached {label} if you want the deeper context."

    if kind == "video":
        return f'I attached the video "{label}" if you want the deeper breakdown.'
    if kind == "episode":
        episode_ref = _episode_ref_label(label)
        if episode_ref:
            return f"I attached {episode_ref} if you want the longer version."
        return f'I attached the episode "{label}" if you want the longer version.'
    if kind == "post":
        return f'I attached the post "{label}" if you want the original context.'
    if kind == "article":
        return f'I attached the article "{label}" if you want the deeper read.'
    return f'I attached "{label}" if you want to dig into the source.'


_GENERIC_ATTACHMENT_SENTENCE_PATTERN = re.compile(
    r"(?i)(?:^|(?<=[.!?])\s+)"
    r"(?:"
    r"(?:i(?:'|’)ve|i have|i)\s+(?:also\s+)?(?:attached|included|linked)\s+"
    r"(?:(?:the|this|these|a|an)\s+)?(?:link|links|card|cards|source|sources|video|episode|article|post)"
    r"(?:\s+\"[^\"]{1,240}\")?"
    r"(?:\s+if\s+you\s+want[^.!?]{0,180})?"
    r"(?:\s+(?:below|above|here))?"
    r"|"
    r"(?:(?:the|this|these|a|an)\s+)?(?:link|links|card|cards|source|sources|video|episode|article|post)\s+"
    r"(?:is|are)\s+(?:attached|included|linked)(?:\s+(?:below|above|here))?"
    r")"
    r"[.!?]?"
)
_ORPHAN_ATTACHMENT_TAIL_PATTERN = re.compile(
    r"(?i)(?:^|(?<=[.!?])\s+)where\s+I\s+talk\s+about[^.!?]{0,220}\b(?:below|here)\.?"
)


def _replace_generic_attachment_copy(text: str, cards: Any) -> str:
    if not text or not cards:
        return text
    handoff = _source_handoff_sentence(cards)
    if not handoff:
        return text
    rewritten, count = _GENERIC_ATTACHMENT_SENTENCE_PATTERN.subn(lambda _match: f" {handoff}", text)
    if count:
        rewritten = _ORPHAN_ATTACHMENT_TAIL_PATTERN.sub(" ", rewritten)
        return clean_response(rewritten)
    return text


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
    rewritten = _replace_generic_attachment_copy(rewritten, cards)

    if reference_replaced:
        attached_pattern = re.compile(r"\b(attached|included|linked|source|sources|video|episode|article|post)\b", re.IGNORECASE)
        if not attached_pattern.search(rewritten):
            suffix = _source_handoff_sentence(cards)
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

        # Protect abbreviations like e.g., i.e., etc. from sentence splitting
        abbrev_map = {}
        def _protect_abbrev(m):
            key = f"{_ABBREV_PLACEHOLDER}{len(abbrev_map)}{_ABBREV_PLACEHOLDER}"
            abbrev_map[key] = m.group(0)
            return key
        protected_prose = _ABBREVIATIONS_RE.sub(_protect_abbrev, prose)

        # Protect decimal numbers like 0.5, 1.2, 100.00 from sentence splitting
        decimal_map = {}
        def _protect_decimal(m):
            key = f"{_DECIMAL_PLACEHOLDER}{len(decimal_map)}{_DECIMAL_PLACEHOLDER}"
            decimal_map[key] = m.group(0)
            return key
        protected_prose = _DECIMAL_RE.sub(_protect_decimal, protected_prose)

        sentences = [segment.strip() for segment in _SENTENCE_SPLIT_PATTERN.split(protected_prose) if segment.strip()]
        # Restore abbreviations and decimals
        if abbrev_map or decimal_map:
            restored = []
            for s in sentences:
                for k, v in abbrev_map.items():
                    s = s.replace(k, v)
                for k, v in decimal_map.items():
                    s = s.replace(k, v)
                restored.append(s)
            sentences = restored

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
    cleaned = _naturalize_source_title_artifacts(cleaned, cards)
    cleaned = _normalize_list_lines(cleaned)
    cleaned = _paragraphize_prose(cleaned)

    # Imported lazily but with a robust fallback: when test stubs leave
    # ``backend.services`` with an empty ``__path__`` the deferred import path
    # would otherwise raise ``ModuleNotFoundError`` even though the real
    # module exists on disk.
    _text_sanitizer = _load_text_sanitizer()

    if cards:
        cleaned = _text_sanitizer.strip_card_attachment_artifacts(cleaned, cards)
        cleaned = clean_response(cleaned, strip_hyphens=strip_hyphens)
        cleaned = _naturalize_source_title_artifacts(cleaned, cards)
        cleaned = _normalize_list_lines(cleaned)
        cleaned = _paragraphize_prose(cleaned)

    cleaned = _text_sanitizer.finalize_generated_text(cleaned, allow_model_cleanup=allow_model_cleanup)
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
    chunk = _load_text_sanitizer().strip_identity_disclosure(chunk)
    for pattern, replacement in _CENSORED_WORD_SPACING_PATTERNS:
        chunk = pattern.sub(replacement, chunk)
    chunk = soften_transcript_hooks(chunk)
    chunk = strip_low_trust_handoffs(chunk)
    # Inline citation markers like [1] or [2][3] are short and almost always
    # emitted as a single token by GPT-class models, so per-chunk stripping is
    # safe in practice. Any partial that splits across chunks (e.g. "[" then
    # "12]") will be left to the final clean_response pass to remove.
    chunk = strip_citation_markers(chunk)
    chunk = strip_source_domain_markers(chunk)
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
