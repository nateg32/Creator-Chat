import re
from typing import Dict, List, Optional


TIME_SENSITIVE_PHRASES = (
    "when",
    "next",
    "upcoming",
    "latest",
    "current",
    "currently",
    "today",
    "tonight",
    "tomorrow",
    "this week",
    "this weekend",
    "date",
    "time",
    "schedule",
    "calendar",
    "register",
    "registration",
    "tickets",
    "ticket",
    "venue",
    "where",
    "livestream",
    "live stream",
    "doors",
    "starts",
    "start time",
)

EVENT_PHRASES = (
    "event",
    "gathering",
    "conference",
    "summit",
    "tour",
    "service",
    "prayer",
    "revival",
    "meeting",
    "night",
    "access",
)

PLATFORM_PATTERNS = {
    "youtube": (r"\byoutube\b", r"\byt\b", r"\bchannel\b"),
    "instagram": (r"\binstagram\b", r"\binsta\b", r"\big\b", r"\breel\b", r"\breels\b"),
    "tiktok": (r"\btiktok\b",),
    "facebook": (r"\bfacebook\b", r"\bfb\b"),
    "twitter": (r"\btwitter\b", r"\bx\b"),
}

VIDEO_TERMS = (
    "video",
    "videos",
    "watch",
    "clip",
    "clips",
    "reel",
    "reels",
    "short",
    "shorts",
    "episode",
    "episodes",
    "podcast",
)


def _recent_user_turns(history: Optional[List[Dict[str, str]]], limit: int = 3) -> List[str]:
    if not history:
        return []
    turns: List[str] = []
    for message in history:
        if (message.get("role") or "").lower() != "user":
            continue
        content = (message.get("content") or message.get("text") or "").strip()
        if content:
            turns.append(content)
    return turns[-limit:]


def extract_requested_platforms(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> List[str]:
    text = (question or "").strip().lower()
    requested: List[str] = []

    for platform, patterns in PLATFORM_PATTERNS.items():
        if any(re.search(pattern, text) for pattern in patterns):
            requested.append(platform)

    if requested:
        return requested

    words = re.findall(r"[a-z0-9']+", text)
    if len(words) > 7:
        return []

    recent_turns = _recent_user_turns(history, limit=2)
    if not recent_turns:
        return []

    combined = " ".join(recent_turns).lower()
    for platform, patterns in PLATFORM_PATTERNS.items():
        if any(re.search(pattern, combined) for pattern in patterns):
            requested.append(platform)

    return requested


def needs_fresh_public_web_search(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> bool:
    """Detect questions that should escalate to live web search for current public facts."""
    current = (question or "").strip().lower()
    if not current:
        return False

    user_turns = _recent_user_turns(history)
    combined = " ".join(user_turns + [current]).lower()
    words = re.findall(r"[a-z0-9']+", current)

    has_time_signal = any(phrase in combined for phrase in TIME_SENSITIVE_PHRASES)
    has_event_signal = any(phrase in combined for phrase in EVENT_PHRASES)
    if has_time_signal and has_event_signal:
        return True

    if len(words) <= 4 and any(phrase in current for phrase in EVENT_PHRASES):
        prior_text = " ".join(user_turns[-2:]).lower()
        if any(phrase in prior_text for phrase in TIME_SENSITIVE_PHRASES):
            return True

    # Creator public-fact / timeline questions that should trigger web search
    # e.g. "when did you start content creation", "how long ago u start content",
    # "when did u get married", "how old are you", "where are you from"
    CREATOR_FACT_PATTERNS = (
        r"\bwhen\s+did\s+(?:you|u|he|she|they)\b",
        r"\bhow\s+long\s+(?:ago|have)\b",
        r"\bhow\s+old\s+(?:are|is|r)\b",
        r"\bwhere\s+(?:are|is|r)\s+(?:you|u|he|she|they)\s+from\b",
        r"\bwhen\s+(?:did|was|were)\s+.+\s*(?:start|begin|launch|found|creat|born|marr|mov|first)\b",
        r"\bhow\s+long\s+.+(?:been|doing|creating|making|running)\b",
        r"\bwhat\s+year\b",
        r"\bwhat\s+age\b",
    )
    if any(re.search(pat, current) for pat in CREATOR_FACT_PATTERNS):
        return True

    return False


def build_live_search_query(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    creator_name: Optional[str] = None,
    preferred_platforms: Optional[List[str]] = None,
    require_video: bool = False,
) -> str:
    """Enrich short follow up questions with recent user context for web search."""
    query = (question or "").strip()
    if not query:
        return query

    user_turns = _recent_user_turns(history)
    if user_turns:
        words = re.findall(r"[a-z0-9']+", query.lower())
        is_short_follow_up = len(words) <= 5
        if is_short_follow_up:
            prior_turns = [turn for turn in user_turns[:-1] if turn.strip()]
            if prior_turns:
                context = " ".join(prior_turns[-2:]).strip()
                if context:
                    query = f"{context} {query}".strip()

    query_lower = query.lower()

    if creator_name:
        creator_lower = creator_name.lower().strip()
        if creator_lower and creator_lower not in query_lower:
            query = f"{creator_name} {query}".strip()
            query_lower = query.lower()

    platforms = [platform for platform in (preferred_platforms or []) if platform]
    for platform in platforms:
        if platform not in query_lower:
            query = f"{query} {platform}".strip()
            query_lower = query.lower()

    if require_video and not any(term in query_lower for term in VIDEO_TERMS):
        query = f"{query} video".strip()

    return re.sub(r"\s+", " ", query).strip()
