"""Utilities for keeping persona voice as patterns, not pasted transcript hooks."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List


_BROADCAST_OR_METADATA_RE = re.compile(
    r"\b("
    r"welcome back|my channel|this channel|subscribe|like and subscribe|hit the bell|"
    r"link in bio|link below|click below|follow for|thanks for watching|"
    r"in today'?s video|in this video|today'?s episode|part \d+|day \d+|"
    r"channel:|length:|views:|uploaded|watch below|watch this|"
    r"business owners:|creators:|founders:|entrepreneurs:|students:|traders:"
    r")\b",
    re.IGNORECASE,
)

_CONTENT_HOOK_RE = re.compile(
    r"^\s*(?:"
    r"(?:[a-z][a-z0-9 &'/-]{2,42}):\s*(?:want|how|why|what|if|watch|listen|stop|here|this|the)\b|"
    r"(?:if|when)\s+you\b.{8,}|"
    r"(?:how|why|what)\s+(?:i|we|to|you)\b.{8,}|"
    r"(?:want|need)\s+to\s+\w+.{8,}|"
    r"pov\b.{4,}|"
    r"stop\s+scrolling\b.*|"
    r"hot\s+take\b.*"
    r")",
    re.IGNORECASE,
)

_STYLE_DESCRIPTOR_RE = re.compile(
    r"\b("
    r"opens by|opening by|uses|prefers|tends to|often|usually|"
    r"short bursts|balanced cadence|direct|warm|challenging|measured|"
    r"story-led|framework|analogy|questions|pushes back|validates"
    r")\b",
    re.IGNORECASE,
)

_PHRASE_FIELD_NAMES = {
    "signature_phrases",
    "repeated_phrases",
    "example_quotes",
    "opening_hooks",
    "signature_openings",
    "signature_landings",
    "golden_examples",
    "golden_replies",
}


def _clean_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.strip('"').strip("'").strip()


def looks_like_raw_content_hook(value: Any) -> bool:
    """Detect broadcast hooks, video titles, and metadata fragments misread as voice.

    The point is not to censor creator vocabulary. It is to stop phrases like
    "Business owners: Want to scale faster?" from becoming reusable DM openers.
    """

    text = _clean_text(value)
    if not text:
        return False
    lower = text.lower()
    words = text.split()
    if _BROADCAST_OR_METADATA_RE.search(lower):
        return True
    if _CONTENT_HOOK_RE.search(text):
        return True
    if "|" in text and len(words) >= 4:
        return True
    if re.search(r"\bep(?:isode)?\.?\s*\d+\b", lower) and len(words) >= 4:
        return True
    if text.count(":") >= 1 and "?" in text and len(words) >= 4:
        return True
    if len(words) > 12 and not _STYLE_DESCRIPTOR_RE.search(text):
        return True
    return False


def _dedupe(values: Iterable[Any], *, limit: int | None = None) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        text = _clean_text(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if limit and len(result) >= limit:
            break
    return result


def clean_style_phrase_list(
    values: Iterable[Any],
    *,
    limit: int | None = None,
    allow_pattern_descriptions: bool = True,
) -> List[str]:
    cleaned: List[str] = []
    for text in _dedupe(values):
        if looks_like_raw_content_hook(text):
            continue
        if len(text) > 140 and not (allow_pattern_descriptions and _STYLE_DESCRIPTOR_RE.search(text)):
            continue
        cleaned.append(text)
        if limit and len(cleaned) >= limit:
            break
    return cleaned


def _sanitize_dict_lists(node: Any, *, runtime: bool = False, key_name: str = "") -> Any:
    if isinstance(node, list):
        if key_name in _PHRASE_FIELD_NAMES:
            if runtime and key_name in {"example_quotes", "golden_examples", "golden_replies"}:
                return []
            return clean_style_phrase_list(node, limit=8)
        return [_sanitize_dict_lists(item, runtime=runtime) for item in node]
    if isinstance(node, dict):
        if key_name in {"golden_examples", "golden_replies"}:
            if runtime:
                return {}
            return {
                str(key): clean_style_phrase_list(value, limit=6)
                if isinstance(value, list)
                else _sanitize_dict_lists(value, runtime=runtime, key_name=key_name)
                for key, value in node.items()
            }
        out: Dict[str, Any] = {}
        for key, value in node.items():
            if runtime and key in {"example_quotes", "golden_examples", "golden_replies"}:
                out[key] = [] if isinstance(value, list) else {}
                continue
            out[key] = _sanitize_dict_lists(value, runtime=runtime, key_name=key)
        return out
    if key_name in {"opening_move", "question_style"} and looks_like_raw_content_hook(node):
        return ""
    return node


def sanitize_style_fingerprint_for_storage(style_fingerprint: Dict[str, Any]) -> Dict[str, Any]:
    style = _sanitize_dict_lists(copy.deepcopy(style_fingerprint or {}), runtime=False)
    style["voice_source_policy"] = (
        "Use approved content to infer behavioral patterns, cadence, values, and reasoning. "
        "Do not reuse transcript hooks or source titles as chat wording."
    )
    return style


def sanitize_style_fingerprint_for_runtime(style_fingerprint: Dict[str, Any]) -> Dict[str, Any]:
    style = _sanitize_dict_lists(copy.deepcopy(style_fingerprint or {}), runtime=True)
    lexical = style.get("lexical_rules") if isinstance(style.get("lexical_rules"), dict) else {}
    lexical["signature_phrases"] = []
    style["signature_phrases"] = []
    style["lexical_rules"] = lexical
    speech = style.get("speech_mechanics") if isinstance(style.get("speech_mechanics"), dict) else {}
    speech["signature_openings"] = clean_style_phrase_list(
        speech.get("signature_openings") or [],
        limit=4,
        allow_pattern_descriptions=True,
    )
    style["speech_mechanics"] = speech
    style["runtime_voice_rule"] = (
        "Infer the creator's cadence, pressure, worldview, and social behavior from the profile. "
        "Never paste transcript hooks, source titles, or example quotes as the answer."
    )
    return style


def sanitize_creator_persona_for_runtime(persona: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = _sanitize_dict_lists(copy.deepcopy(persona or {}), runtime=True)
    cleaned["repeated_phrases"] = []
    cleaned["example_quotes"] = []
    cleaned["voice_source_policy"] = "Use these fields as pattern conclusions, not as a phrase bank."
    return cleaned


def sanitize_voice_profile_for_runtime(voice_profile: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = copy.deepcopy(voice_profile or {})
    if isinstance(cleaned, dict):
        cleaned["signature_phrases"] = []
        for key in ("greeting_high_energy", "greeting_neutral", "greeting_short", "greetings"):
            if isinstance(cleaned.get(key), list):
                cleaned[key] = clean_style_phrase_list(cleaned[key], limit=4)
    return cleaned
