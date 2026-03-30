import re
from typing import Dict, List, Optional, Set

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


# ──────────────────────────────────────────────────────────────
# GENERAL KNOWLEDGE REDIRECT
# Detects "generic AI assistant" questions (git tutorials, cooking,
# trivia, etc.) that are clearly outside the creator's specialty.
# ──────────────────────────────────────────────────────────────

# Map: broad topic key → set of creator domain keywords that legitimately allow the topic
_GENERAL_TOPIC_DOMAIN_MAP: Dict[str, Set[str]] = {
    "git": {"programming", "tech", "software", "devops", "development", "engineering", "saas"},
    "coding": {"programming", "tech", "software", "development", "engineering", "saas"},
    "cooking": {"cooking", "food", "nutrition", "health", "lifestyle", "wellness", "chef"},
    "geography": {"education", "travel", "history", "language", "culture"},
    "grammar": {"education", "writing", "content", "copywriting", "journalism", "language"},
    "home_diy": {"home", "real_estate", "diy", "lifestyle", "construction", "design"},
    "medical_general": {"health", "wellness", "fitness", "medicine", "biology", "nursing"},
    "math_science": {"education", "engineering", "science", "math", "finance", "data"},
    "automotive": {"automotive", "cars", "motorsport", "lifestyle", "mechanics"},
}

# Regex patterns per topic — order matters, checked top-to-bottom
_GENERAL_TOPIC_PATTERNS: Dict[str, List[str]] = {
    "git": [
        r"\bgit\s*(commit|push|pull|merge|clone|branch|rebase|stash|status|log|add|diff|checkout|init|reset|revert|fetch)\b",
        r"\b(commit|push|pull|merge|clone|branch|rebase|stash|checkout|fetch)\s+(in|with|using|on|to|from|via)\s+git\b",
        r"\bhow\s+to\s+(commit|push|pull|merge|clone|branch|rebase|stash|checkout|fetch)\b.*\bgit\b",
        r"\bhow\s+to\s+do\s+a\s+git\b",
        r"\bhow\s+to\s+use\s+git\b",
        r"\bgit\s+workflow\b",
        r"\bversion\s+control\b",
        r"\bgitHub\s+(pull\s+request|repo|repository)\b",
    ],
    "coding": [
        r"\bhow\s+to\s+(code|program)\s+in\s+(python|javascript|java|c\+\+|ruby|php|swift|kotlin|typescript|go|rust)\b",
        r"\b(python|javascript|html|css|sql|react|vue|angular|node\.?js)\s+(tutorial|basics|syntax|example|guide)\b",
        r"\b(debug|fix)\s+this\s+(code|function|script|class)\b",
        r"\bdata\s+structure\b",
        r"\bsorting\s+algorithm\b",
        r"\btime\s+complexity\b",
        r"\brecursion\s+example\b",
    ],
    "cooking": [
        r"\b(recipe|ingredient)\s+(for|to\s+make)\b",
        r"\bhow\s+to\s+(bake|fry|boil|grill|roast|steam|poach)\b",
        r"\boven\s+temperature\s+for\b",
        r"\bwhat\s+to\s+cook\s+for\b",
        r"\bdinner\s+(idea|recipe)\b",
    ],
    "geography": [
        r"\bcapital\s+(of|city\s+of)\s+\w+",
        r"\bpopulation\s+of\s+\w+",
        r"\blargest\s+(country|city|continent|desert|ocean)\b",
        r"\bwhere\s+is\s+\w+\s+(located|situated|country|city)\b",
    ],
    "grammar": [
        r"\b(grammar|grammatical)\s+(rule|mistake|error|check)\b",
        r"\bhow\s+to\s+spell\b",
        r"\bpunctuation\s+(rule|guide)\b",
        r"\bdifference\s+between\s+(their|there|they're|your|you're|its|it's)\b",
    ],
    "home_diy": [
        r"\bhow\s+to\s+(fix|repair|install|replace)\s+(a\s+|the\s+)?(roof|pipe|faucet|toilet|sink|drywall|tile|floor|door|window|fence|electrical|wiring)\b",
        r"\bdiy\s+(project|home\s+repair|plumbing|electrical)\b",
    ],
    "medical_general": [
        r"\bsymptom(s)?\s+of\s+(a\s+)?(cold|flu|covid|pneumonia|cancer|diabetes|appendicitis)\b",
        r"\bhow\s+to\s+treat\s+(a\s+)?(\w+\s+)?(fever|rash|sprain|fracture|wound|infection)\b",
        r"\bwhat\s+medicine\s+(for|to\s+take\s+for)\b",
        r"\bmedication\s+dosage\s+for\b",
    ],
    "math_science": [
        r"\b(calculate|solve|compute)\s+(the\s+)?(integral|derivative|matrix|equation|formula)\b",
        r"\bchemical\s+formula\s+for\b",
        r"\bperiodic\s+table\b",
        r"\bnewton'?s\s+(law|laws)\b",
    ],
    "automotive": [
        r"\bhow\s+to\s+(change|replace|fix)\s+(a\s+)?(oil|tire|spark\s+plug|brake|battery|transmission)\b",
        r"\bcar\s+(maintenance|repair)\s+(guide|tips|how\s+to)\b",
    ],
}


def detect_general_knowledge_topic(question: str) -> Optional[str]:
    """
    Returns a broad topic key if the question reads as a generic how-to / tutorial
    that any AI assistant could answer but is unrelated to most creator specialties.
    Returns None if no pattern matches.
    """
    q = (question or "").lower().strip()
    if not q:
        return None
    for topic, patterns in _GENERAL_TOPIC_PATTERNS.items():
        if any(re.search(p, q, re.IGNORECASE) for p in patterns):
            return topic
    return None


def should_redirect_general_knowledge(
    question: str,
    creator_primary_domains: Optional[List[str]] = None,
    creator_secondary_domains: Optional[List[str]] = None,
) -> bool:
    """
    Returns True when the question is a generic AI tutorial / factual question
    that falls outside the creator's area of expertise.

    This prevents the bot from acting as a general ChatGPT wrapper.
    When True, the creator should decline in character and pivot to their domain.
    """
    topic = detect_general_knowledge_topic(question)
    if not topic:
        return False

    allowed_for_topic: Set[str] = _GENERAL_TOPIC_DOMAIN_MAP.get(topic, set())
    if not allowed_for_topic:
        # Unknown topic — don't block
        return False

    all_creator_domains: Set[str] = set()
    for d in (creator_primary_domains or []):
        all_creator_domains.add(d.lower().strip())
    for d in (creator_secondary_domains or []):
        all_creator_domains.add(d.lower().strip())

    # If the creator's declared domains overlap with what this topic needs, let it through
    if all_creator_domains.intersection(allowed_for_topic):
        return False

    return True
