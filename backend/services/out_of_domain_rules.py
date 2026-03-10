import re
from typing import Dict, List, Optional

TOPIC_RULES = {
    "crypto_price": {
        "patterns": ["bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "doge"],
        "signals": ["price", "worth", "trading at", "market cap", "chart", "current"],
        "allowed": {"crypto", "trading", "finance", "personal_finance"},
    },
    "stock_price": {
        "patterns": ["stock", "shares", "nasdaq", "nyse", "ticker", "s&p", "dow"],
        "signals": ["price", "worth", "trading at", "market cap", "chart", "current"],
        "allowed": {"trading", "finance", "personal_finance"},
    },
    "sports_score": {
        "patterns": ["score", "game", "match", "standings", "nba", "nfl", "mlb", "soccer", "epl"],
        "signals": ["current", "today", "tonight", "live", "latest"],
        "allowed": {"sports"},
    },
    "weather": {
        "patterns": ["weather", "forecast", "temperature", "rain", "snow", "humid", "sunny"],
        "signals": ["today", "tomorrow", "current", "this weekend"],
        "allowed": {"weather"},
    },
}

BRIDGE_QUESTION_BY_CATEGORY = {
    "business": "What are you trying to build right now?",
    "entrepreneurship": "What are you trying to build right now?",
    "fitness": "What are you trying to improve in training right now?",
    "trading": "What market are you focused on right now?",
    "crypto": "What part of crypto are you actually trying to understand or act on?",
    "finance": "What money decision are you trying to make right now?",
    "personal_finance": "What financial goal are you working on right now?",
    "ministry": "What are you actually needing prayer or clarity on right now?",
    "faith": "What are you actually needing prayer or clarity on right now?",
    "general": "What do you want help with right now?",
}


def detect_external_live_fact_topic(question: str) -> Optional[str]:
    q = (question or "").lower().strip()
    if not q:
        return None
    for topic, rule in TOPIC_RULES.items():
        if any(pattern in q for pattern in rule["patterns"]) and any(signal in q for signal in rule["signals"]):
            return topic
    return None


def should_soft_decline_external_live_fact(
    question: str,
    creator_category: str = "",
    stronghold_config: Optional[Dict[str, object]] = None,
) -> bool:
    topic = detect_external_live_fact_topic(question)
    if not topic:
        return False

    category = (creator_category or "general").lower().strip()
    allowed = set(TOPIC_RULES[topic]["allowed"])
    if category in allowed:
        return False

    config = stronghold_config or {}
    configured_domains = set()
    for key in ("primary_domains", "secondary_domains", "allowed_bridge_domains"):
        for value in config.get(key, []) or []:
            configured_domains.add(str(value).lower().strip())
    if configured_domains.intersection(allowed):
        return False

    return True


def recent_bridge_topic(history: Optional[List[Dict[str, str]]], current_question: str = "") -> Optional[str]:
    current = (current_question or "").strip().lower()
    if not history:
        return None
    candidates = []
    for message in history:
        if (message.get("role") or "").lower() != "user":
            continue
        content = (message.get("content") or message.get("text") or "").strip()
        if not content:
            continue
        if current and content.lower().strip() == current:
            continue
        candidates.append(content)
    if not candidates:
        return None
    return candidates[-1]


def default_bridge_question(creator_category: str = "general") -> str:
    category = (creator_category or "general").lower().strip()
    return BRIDGE_QUESTION_BY_CATEGORY.get(category, BRIDGE_QUESTION_BY_CATEGORY["general"])
