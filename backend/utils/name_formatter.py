from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional, Dict, Any

PARTICLES = {
    "van", "von", "de", "del", "da", "di", "la", "le", "du", "der", "den",
    "ten", "ter", "bin", "ibn", "al"
}

SUFFIXES = {
    "jr": "Jr.",
    "sr": "Sr.",
    "ii": "II",
    "iii": "III",
    "iv": "IV",
    "v": "V",
}

_CONTROL_RE = re.compile(r"[\u0000-\u001F\u007F]")
_WHITESPACE_RE = re.compile(r"\s+")
_HTMLISH_RE = re.compile(r"[<>]")
_ALLOWED_RE = re.compile(r"^[\w\s\-\.'’&]+$", re.UNICODE)

def _has_letter(s: str) -> bool:
    for ch in s:
        if unicodedata.category(ch).startswith("L"):
            return True
    return False

def _punct_ratio(s: str) -> float:
    if not s:
        return 1.0
    punct = 0
    for ch in s:
        cat = unicodedata.category(ch)
        if cat.startswith("P") or cat.startswith("S"):
            punct += 1
    return punct / len(s)

def _is_all_caps_acronym(token: str) -> bool:
    if not (2 <= len(token) <= 10):
        return False
    if not re.fullmatch(r"[A-Z0-9]+", token):
        return False
    return any("A" <= c <= "Z" for c in token)

def _is_mixed_case(token: str) -> bool:
    return any(c.islower() for c in token) and any(c.isupper() for c in token)

def _titlecase_word(word: str) -> str:
    if not word:
        return word
    word = word.replace("’", "'")
    parts = re.split(r"([-'])", word)
    out = []
    for p in parts:
        if p in ("-", "'"):
            out.append(p)
        else:
            if _is_all_caps_acronym(p):
                out.append(p)
            else:
                out.append(p[:1].upper() + p[1:].lower() if p else p)
    return "".join(out)

@dataclass
class NormalizeResult:
    normalized: Optional[str]
    is_valid: bool
    error: Optional[str]
    suggested: Optional[str]
    flags: Dict[str, Any]

def normalize_creator_name(raw_name: str) -> NormalizeResult:
    if raw_name is None:
        return NormalizeResult(None, False, "Enter a creator name.", None, {"changed": False})

    s = unicodedata.normalize("NFKC", str(raw_name))
    s = _CONTROL_RE.sub("", s)
    s = s.strip()
    s = _WHITESPACE_RE.sub(" ", s)

    if not s:
        return NormalizeResult(None, False, "Enter a creator name.", None, {"changed": False})
    if len(s) < 2:
        return NormalizeResult(None, False, "Name is too short.", None, {"changed": False})
    if len(s) > 80:
        return NormalizeResult(None, False, "Name is too long.", None, {"changed": False})
    if _HTMLISH_RE.search(s):
        return NormalizeResult(None, False, "Name contains invalid characters.", None, {"changed": False})
    if not _has_letter(s):
        return NormalizeResult(None, False, "Name must include letters.", None, {"changed": False})
    if not _ALLOWED_RE.fullmatch(s):
        return NormalizeResult(None, False, "Name contains invalid characters.", None, {"changed": False})
    if _punct_ratio(s) > 0.25:
        return NormalizeResult(None, False, "Name contains too much punctuation.", None, {"changed": False})

    original = s
    tokens = s.split(" ")
    flags: Dict[str, Any] = {"changed": False, "likely_acronym": False}

    if len(tokens) == 1:
        t = tokens[0].replace("’", "'")

        if _is_mixed_case(t) or _is_all_caps_acronym(t):
            normalized = t
            flags["changed"] = (normalized != original)
            return NormalizeResult(normalized, True, None, None, flags)

        if t.islower() and re.fullmatch(r"[a-z0-9]{2,10}", t):
            suggested = t.upper()
            normalized = _titlecase_word(t)
            flags["likely_acronym"] = True
            flags["changed"] = (normalized != original)
            return NormalizeResult(normalized, True, None, suggested, flags)

        normalized = _titlecase_word(t)
        flags["changed"] = (normalized != original)
        return NormalizeResult(normalized, True, None, None, flags)

    out = []
    for i, token in enumerate(tokens):
        clean = token.replace("’", "'")

        if i == len(tokens) - 1:
            k = re.sub(r"\.", "", clean).lower()
            if k in SUFFIXES:
                out.append(SUFFIXES[k])
                continue

        if _is_mixed_case(clean) or _is_all_caps_acronym(clean):
            out.append(clean)
            continue

        low = clean.lower()
        if i != 0 and low in PARTICLES:
            out.append(low)
            continue

        out.append(_titlecase_word(clean))

    normalized = " ".join(out)
    flags["changed"] = (normalized != original)
    return NormalizeResult(normalized, True, None, None, flags)
