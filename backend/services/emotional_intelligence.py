"""Fast local emotion and vibe detection for chat turns.

This deliberately avoids an LLM call. It gives the response prompt a compact
read on the user's emotional state without adding network latency.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


_EMOTION_KEYWORDS = {
    "overwhelmed": (
        "overwhelmed", "too much", "drowning", "all over the place",
        "dont know where to start", "don't know where to start",
    ),
    "frustrated": (
        "frustrated", "annoyed", "angry", "mad", "pissed", "fed up",
        "still broken", "not working", "keeps failing", "wtf",
    ),
    "confused": (
        "confused", "lost", "stuck", "not sure", "dont get", "don't get",
        "huh", "wdym", "what do you mean", "makes no sense",
    ),
    "anxious": (
        "anxious", "nervous", "worried", "scared", "afraid", "panic",
        "stressing", "stressed", "stress",
    ),
    "discouraged": (
        "discouraged", "hopeless", "cant do", "can't do", "give up",
        "unmotivated", "burnt out", "burned out", "drained", "tired",
    ),
    "excited": (
        "excited", "pumped", "hyped", "keen", "lets go", "let's go",
        "love this", "sick", "fire", "amazing",
    ),
    "skeptical": (
        "really?", "are you sure", "doesnt sound", "doesn't sound",
        "i doubt", "not convinced", "cap", "prove",
    ),
    "playful": (
        "lol", "haha", "lmao", "bruh", "bro", "my g", "ma g",
        "banter", "joking",
    ),
}

_INTENSIFIERS = {
    "really", "very", "so", "super", "extremely", "honestly", "literally",
    "proper", "lowkey", "highkey", "freaking", "fucking", "f*cking",
}

_DIRECTNESS_SIGNALS = (
    "just tell me", "be honest", "straight up", "no fluff", "quickly",
    "short answer", "simple answer", "dont sugarcoat", "don't sugarcoat",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _contains_phrase(text: str, phrase: str) -> bool:
    if " " in phrase or "'" in phrase or "?" in phrase:
        return phrase in text
    return bool(re.search(rf"\b{re.escape(phrase)}\b", text))


def detect_message_vibe(message: str, history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Return a compact, CPU-only emotional read for a user message."""
    text = _normalize(message)
    raw = str(message or "")
    words = re.findall(r"[a-z0-9']+", text)
    scores: Dict[str, int] = {key: 0 for key in _EMOTION_KEYWORDS}

    for emotion, keywords in _EMOTION_KEYWORDS.items():
        for keyword in keywords:
            if _contains_phrase(text, keyword):
                scores[emotion] += 2 if " " in keyword else 1

    if any(word in _INTENSIFIERS for word in words):
        for emotion, score in list(scores.items()):
            if score > 0:
                scores[emotion] += 1

    if "!" in raw:
        if scores["excited"] > 0:
            scores["excited"] += 1
        elif any(term in text for term in ("why", "still", "broken", "not working")):
            scores["frustrated"] += 1

    if raw.isupper() and len(raw) > 8:
        scores["frustrated"] += 1

    primary = max(scores, key=scores.get) if scores else "neutral"
    if scores.get(primary, 0) <= 0:
        primary = "neutral"

    score = scores.get(primary, 0)
    if score >= 4 or raw.count("!") >= 2:
        intensity = "high"
    elif score >= 2:
        intensity = "medium"
    elif primary != "neutral":
        intensity = "low"
    else:
        intensity = "low"

    word_count = len(words)
    directness_requested = any(signal in text for signal in _DIRECTNESS_SIGNALS)
    has_question = "?" in raw or bool(re.match(r"^(what|how|why|when|where|can|should|do|does)\b", text))
    casual_markers = {"yo", "yoo", "hey", "bro", "mate", "my", "ma", "g", "lol", "haha"}
    is_casual = bool(set(words) & casual_markers) or word_count <= 4

    if primary in {"overwhelmed", "anxious", "discouraged"}:
        vibe = "vulnerable"
        user_need = "acknowledgement before advice"
    elif primary == "frustrated":
        vibe = "frustrated"
        user_need = "own the friction, then give a clear next move"
    elif primary == "confused":
        vibe = "confused"
        user_need = "slow down and make it simple"
    elif primary == "excited":
        vibe = "energized"
        user_need = "match momentum, then focus it"
    elif primary == "skeptical":
        vibe = "skeptical"
        user_need = "be concrete and avoid hype"
    elif primary == "playful" or is_casual:
        vibe = "casual"
        user_need = "keep it relaxed and human"
    elif directness_requested:
        vibe = "direct"
        user_need = "answer without preamble"
    elif has_question:
        vibe = "focused"
        user_need = "answer the actual question"
    else:
        vibe = "neutral"
        user_need = "respond naturally"

    return {
        "primary": primary,
        "intensity": intensity,
        "vibe": vibe,
        "user_need": user_need,
        "word_count": word_count,
        "directness_requested": directness_requested,
        "is_casual": is_casual,
    }


def format_vibe_prompt_block(vibe: Optional[Dict[str, Any]]) -> str:
    """Format the local vibe read for prompt injection."""
    vibe = vibe or {}
    primary = vibe.get("primary") or "neutral"
    intensity = vibe.get("intensity") or "low"
    user_vibe = vibe.get("vibe") or "neutral"
    user_need = vibe.get("user_need") or "respond naturally"

    if primary == "neutral":
        adaptation = (
            "No strong emotion detected. Answer naturally and stay in persona. "
            "Do not add fake empathy."
        )
    elif primary in {"overwhelmed", "anxious", "discouraged"}:
        adaptation = (
            "Acknowledge the feeling in one short clause, then make the next step feel manageable. "
            "Do not lecture or overwhelm them with options."
        )
    elif primary == "frustrated":
        adaptation = (
            "Respect the frustration first, then be crisp and useful. "
            "Do not sound defensive or dismissive."
        )
    elif primary == "confused":
        adaptation = (
            "Slow the explanation down and use plain language. "
            "Prefer one clear next step over a big framework."
        )
    elif primary == "excited":
        adaptation = (
            "Match the momentum without becoming hypey. Channel the energy into a concrete move."
        )
    elif primary == "skeptical":
        adaptation = (
            "Stay grounded and specific. Avoid overclaiming or motivational filler."
        )
    else:
        adaptation = "Mirror the user's energy lightly while preserving the creator's stable voice."

    return (
        "USER VIBE READ (fast local signal, do not mention this):\n"
        f"- emotion: {primary} ({intensity})\n"
        f"- vibe: {user_vibe}\n"
        f"- likely need: {user_need}\n"
        f"- adaptation: {adaptation}\n"
        "Use this only to tune warmth, directness, pacing, and question choice. "
        "Never let it override facts, safety, or the creator persona."
    )
