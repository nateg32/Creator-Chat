import re
from typing import Any, Dict


_BLOCKED_PATTERNS = (
    "sign in",
    "log in",
    "login required",
    "transcript unavailable",
    "captions unavailable",
    "content unavailable",
    "access denied",
    "please enable javascript",
    "captcha",
    "page not found",
)
_USABLE_SCORE_THRESHOLD = 0.4


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def assess_transcript_quality(transcript: str, caption: str = "", title: str = "") -> Dict[str, Any]:
    text = _normalize_text(transcript)
    caption_text = _normalize_text(caption)
    title_text = _normalize_text(title)
    lowered = text.lower()
    words = re.findall(r"[a-z0-9']+", lowered)
    word_count = len(words)
    char_count = len(text)
    unique_ratio = (len(set(words)) / word_count) if word_count else 0.0
    reasons = []

    if not text:
        return {
            "usable": False,
            "score": 0.0,
            "reason": "empty",
            "word_count": 0,
            "char_count": 0,
            "coverage": "missing",
        }

    if any(pattern in lowered for pattern in _BLOCKED_PATTERNS):
        reasons.append("blocked")
    if word_count < 14 or char_count < 80:
        reasons.append("too_short")
    if title_text and lowered == title_text.lower():
        reasons.append("title_only")
    if caption_text and lowered == caption_text.lower():
        reasons.append("caption_only")
    if (
        caption_text
        and caption_text.lower() in lowered
        and char_count <= max(len(caption_text) + 24, 120)
        and word_count < 28
    ):
        reasons.append("caption_mirror")
    if word_count >= 12 and unique_ratio < 0.22:
        reasons.append("too_repetitive")

    score = min(1.0, (word_count / 30.0) * 0.55 + (char_count / 240.0) * 0.45)
    if "blocked" in reasons:
        score = min(score, 0.1)
    if "too_short" in reasons:
        score -= 0.18
    if "caption_only" in reasons or "title_only" in reasons:
        score -= 0.2
    if "caption_mirror" in reasons:
        score -= 0.16
    if "too_repetitive" in reasons:
        score -= 0.12
    score = max(0.0, round(score, 4))

    if word_count >= 110 and score >= 0.68 and "blocked" not in reasons:
        coverage = "full"
    elif word_count >= 20 and score >= _USABLE_SCORE_THRESHOLD and "blocked" not in reasons:
        coverage = "partial"
    else:
        coverage = "weak"

    return {
        "usable": score >= _USABLE_SCORE_THRESHOLD and "blocked" not in reasons,
        "score": score,
        "reason": reasons[0] if reasons else "ok",
        "word_count": word_count,
        "char_count": char_count,
        "coverage": coverage,
    }


def transcript_needs_recovery(transcript: str, caption: str = "", title: str = "") -> bool:
    return not assess_transcript_quality(transcript, caption=caption, title=title).get("usable", False)
