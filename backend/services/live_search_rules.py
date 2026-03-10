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

    return False


def build_live_search_query(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Enrich short follow up questions with recent user context for web search."""
    query = (question or "").strip()
    if not query:
        return query

    user_turns = _recent_user_turns(history)
    if not user_turns:
        return query

    words = re.findall(r"[a-z0-9']+", query.lower())
    is_short_follow_up = len(words) <= 5
    if not is_short_follow_up:
        return query

    prior_turns = [turn for turn in user_turns[:-1] if turn.strip()]
    if not prior_turns:
        return query

    context = " ".join(prior_turns[-2:]).strip()
    if not context:
        return query

    return f"{context} {query}".strip()
