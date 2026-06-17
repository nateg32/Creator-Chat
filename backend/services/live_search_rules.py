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

_GENERIC_CREATOR_NICHES = {
    "general",
    "creator",
    "influencer",
    "content creator",
    "youtube",
    "youtuber",
    "podcast",
    "podcaster",
    "social media",
}

_ASSISTANT_CONTEXT_SIGNALS = re.compile(
    r"\b(?:latest|newest|recent|last|just|pulled|bought|built|rebuilt|posted|uploaded|shared|attached|recommended|mentioned|video|podcast|episode|resource|source|link|watch|listen)\b",
    re.IGNORECASE,
)

_RESOURCE_FOLLOWUP_RE = re.compile(
    r"\b(?:link|links|url|source|sources|send|show|watch|video|episode|podcast|resource|where\s+can\s+i)\b",
    re.IGNORECASE,
)

_RESOURCE_CONTEXT_RE = re.compile(
    r"\b(?:video|podcast|episode|resource|post|reel|clip|source|link|watch|listen)\b",
    re.IGNORECASE,
)

_FOLLOWUP_CONTEXT_STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "if", "you", "u", "your", "ur", "i",
    "we", "my", "our", "it", "that", "this", "one", "thing", "what", "was", "is",
    "are", "were", "did", "do", "does", "about", "tell", "me", "more", "latest",
    "newest", "recent", "last", "just", "into", "with", "for", "from", "there",
}


def _format_creator_name_for_search(creator_name: Optional[str]) -> str:
    name = re.sub(r"\s+", " ", str(creator_name or "")).strip()
    if not name:
        return ""
    if len(name.split()) >= 2 and not (name.startswith('"') and name.endswith('"')):
        return f'"{name}"'
    return name


def _replace_or_prepend_identity(query: str, identity: str, raw_identity: str) -> str:
    if not identity or not raw_identity:
        return query
    raw = re.sub(r"\s+", " ", raw_identity).strip()
    if not raw:
        return query

    if identity.lower() in query.lower():
        return query

    exact_pattern = re.compile(rf'(?<![\w@]){re.escape(raw)}(?![\w@])', re.IGNORECASE)
    if exact_pattern.search(query):
        return exact_pattern.sub(identity, query, count=1)

    identity_key = identity.lower().strip('"')
    if identity_key and identity_key in query.lower():
        return query
    return f"{identity} {query}".strip()


def _clean_creator_niche(creator_niche: Optional[str]) -> str:
    niche = re.sub(r"\s+", " ", str(creator_niche or "")).strip(" ,.-")
    if not niche:
        return ""
    if niche.lower() in _GENERIC_CREATOR_NICHES:
        return ""
    return " ".join(niche.split()[:5])


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


def _recent_assistant_turns(history: Optional[List[Dict[str, str]]], limit: int = 2) -> List[str]:
    if not history:
        return []
    turns: List[str] = []
    for message in history:
        if (message.get("role") or "").lower() != "assistant":
            continue
        content = (message.get("content") or message.get("text") or "").strip()
        if content:
            turns.append(content)
    return turns[-limit:]


def _context_terms(text: str) -> set:
    return {
        word
        for word in re.findall(r"[a-z0-9']+", str(text or "").lower())
        if len(word) > 2 and word not in _FOLLOWUP_CONTEXT_STOP_WORDS
    }


def _assistant_context_for_followup(
    query: str,
    history: Optional[List[Dict[str, str]]],
) -> str:
    query_terms = _context_terms(query)
    pronoun_followup = bool(re.search(r"\b(?:it|that|this|one|thing)\b", str(query or "").lower()))
    resource_followup = bool(_RESOURCE_FOLLOWUP_RE.search(str(query or "")))
    if not query_terms and not pronoun_followup and not resource_followup:
        return ""

    best = ""
    best_score = 0
    for turn in _recent_assistant_turns(history, limit=2):
        for sentence in re.split(r"(?<=[.!?])\s+", turn):
            sentence = re.sub(r"\s+", " ", sentence).strip()
            if not sentence or not _ASSISTANT_CONTEXT_SIGNALS.search(sentence):
                continue
            sentence_terms = _context_terms(sentence)
            overlap = len(query_terms & sentence_terms)
            if not overlap and not pronoun_followup:
                if not (resource_followup and _RESOURCE_CONTEXT_RE.search(sentence)):
                    continue
            score = overlap * 4
            if re.search(r"\b(?:latest|newest|recent|last)\b", sentence, re.IGNORECASE):
                score += 2
            if resource_followup and _RESOURCE_CONTEXT_RE.search(sentence):
                score += 2
            if len(sentence) > 260:
                sentence = sentence[:260].rsplit(" ", 1)[0].strip()
            if score > best_score:
                best = sentence
                best_score = score
    return best


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

    # Creator public-fact / timeline / biographical questions that should
    # trigger web search. Covers dates, personal facts, stats, and history.
    CREATOR_FACT_PATTERNS = (
        r"\bwhen\s+did\s+(?:you|u|he|she|they)\b",
        r"\bhow\s+long\s+(?:ago|have)\b",
        r"\bhow\s+old\s+(?:are|is|r)\b",
        r"\bwhere\s+(?:are|is|r)\s+(?:you|u|he|she|they)\s+from\b",
        r"\bwhen\s+(?:did|was|were)\s+.+\s*(?:start|begin|launch|found|creat|born|marr|mov|first)\b",
        r"\bhow\s+long\s+.+(?:been|doing|creating|making|running)\b",
        r"\bwhat\s+year\b",
        r"\bwhat\s+age\b",
        r"\b(?:married|wife|husband|spouse|partner|girlfriend|boyfriend)\b",
        r"\b(?:first\s+date|how\s+(?:you|u|he|she|they)\s+met|how\s+(?:you|u|he|she|they)\s+and\s+\w+\s+met|where\s+(?:was|were)\s+(?:your|ur|his|her|their)\s+first\s+date)\b",
        r"\b(?:children|kids|son|daughter|baby|family)\b",
        r"\b(?:born|birthday|birth\s?place|hometown|grew\s+up|raised)\b",
        r"\b(?:net\s+worth|salary|income|revenue|earn)\b",
        r"\b(?:turning\s+point|rock\s+bottom|turn\s+(?:(?:your|his|her|their)?\s*life|it|that|this|things)\s+around|changed\s+(?:your|his|her|their)?\s*life)\b",
        r"\bwhat\s+(?:made|led|motivated|inspired|forced)\s+(?:you|u|him|her|them)\s+(?:to\s+)?(?:change|turn|rebuild|start\s+over|turn\s+things\s+around)\b",
        r"\bwho\s+(?:is|are|was)\s+(?:your|his|her|their)\b",
        r"\bwhere\s+(?:do|does|did)\s+(?:you|he|she|they)\s+live\b",
        r"\b(?:founded|co.?found|started\s+(?:the|a|his|her|their))\b",
    )
    if any(re.search(pat, current) for pat in CREATOR_FACT_PATTERNS):
        return True

    return False


def build_live_search_query(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    creator_name: Optional[str] = None,
    creator_handle: Optional[str] = None,
    creator_niche: Optional[str] = None,
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
            assistant_context = _assistant_context_for_followup(query, history)
            if assistant_context:
                query = f"{assistant_context} {query}".strip()
            else:
                prior_turns = [turn for turn in user_turns[:-1] if turn.strip()]
                if prior_turns:
                    context = " ".join(prior_turns[-2:]).strip()
                    if context:
                        query = f"{context} {query}".strip()

    query_lower = query.lower()

    if creator_name:
        creator_identity = _format_creator_name_for_search(creator_name)
        query = _replace_or_prepend_identity(query, creator_identity, creator_name)
        query_lower = query.lower()

    if creator_handle:
        handle = creator_handle.strip()
        if handle:
            bare_handle = handle.lstrip("@")
            handle_terms = {handle.lower(), bare_handle.lower()}
            if not any(term and term in query_lower for term in handle_terms):
                query = f"{query} @{bare_handle}".strip()
                query_lower = query.lower()

    if creator_niche:
        niche = _clean_creator_niche(creator_niche)
        if niche and niche.lower() not in query_lower:
            query = f"{query} {niche}".strip()
            query_lower = query.lower()

    platforms = [platform for platform in (preferred_platforms or []) if platform]
    for platform in platforms:
        if platform not in query_lower:
            query = f"{query} {platform}".strip()
            query_lower = query.lower()

    if require_video and not any(term in query_lower for term in VIDEO_TERMS):
        query = f"{query} video".strip()

    return re.sub(r"\s+", " ", query).strip()
