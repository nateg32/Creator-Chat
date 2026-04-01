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
    "git": {"programming", "tech", "software", "devops", "development", "engineering"},
    "coding": {"programming", "tech", "software", "development", "engineering"},
    "cooking": {"cooking", "food", "nutrition", "health", "lifestyle", "wellness", "chef"},
    "geography": {"education", "travel", "history", "language", "culture"},
    "grammar": {"education", "writing", "content", "copywriting", "journalism", "language"},
    "home_diy": {"home", "real_estate", "diy", "lifestyle", "construction", "design"},
    "medical_general": {"health", "wellness", "fitness", "medicine", "biology", "nursing"},
    "math_science": {"education", "engineering", "science", "math", "finance", "data"},
    "automotive": {"automotive", "cars", "motorsport", "lifestyle", "mechanics"},
    "sports": {"sports", "fitness", "athletics", "coaching", "health"},
    "entertainment": {"entertainment", "music", "art", "media", "content", "film"},
    "music_instrument": {"music", "instrument", "entertainment", "art", "audio"},
    "language_learning": {"education", "language", "travel", "culture", "linguistics"},
    "gaming": {"gaming", "esports", "entertainment", "tech", "content"},
    "history_trivia": {"education", "history", "writing", "culture"},
    "how_to_general": set(),  # empty set — always redirects unless creator has an explicit domain match
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
        r"\b(python|javascript|java|c\+\+|ruby|php|swift|kotlin|typescript|go|rust)\s+(reverse|reversal|loop|function|class|method|array|list|string|variable|dictionary|tuple|set|object|module|import|exception|error|syntax|operator|recursion|iteration|lambda|generator|decorator|closure|regex|parse|sort|search|filter|map|reduce)\b",
        r"\b(reverse|reversal|loop|iterate|parse|sort|filter|map|reduce)\s+(a\s+|the\s+|in\s+|with\s+)?(python|javascript|java|c\+\+|ruby|php|typescript|go|rust)\b",
        r"\bhow\s+to\s+\w+\s+(a\s+)?(list|string|array|dict|dictionary|tuple|set|file|object|number|int|float|variable)\s+(in|with|using)\s+(python|javascript|java|c\+\+|ruby|php|swift|kotlin|typescript|go|rust)\b",
        r"\bhow\s+to\s+\w+\s+(in|with|using)\s+(python|javascript|java|c\+\+|ruby|php|swift|kotlin|typescript|go|rust)\b",
        r"\b(python|javascript|java|typescript)\s+(code|script|snippet)\s+(for|to|that)\b",
        r"\bwrite\s+(a|me|the)\s+(python|javascript|java|typescript|code|script|function|program)\b",
        r"\b(for\s+loop|while\s+loop|if\s+else|try\s+except|switch\s+case)\s+(in\s+)?(python|javascript|java)?\b",
        r"\bhow\s+to\s+do\s+a?\s*(python|javascript|java|c\+\+|ruby|php|swift|kotlin|typescript|go|rust)\b",
    ],
    "cooking": [
        r"\b(recipe|ingredient)\s+(for|to\s+make)\b",
        r"\bhow\s+to\s+(bake|fry|boil|grill|roast|steam|poach|cook|saut[eé]|braise|simmer|blanch|marinate|smoke|barbecue|bbq)\b",
        r"\boven\s+temperature\s+for\b",
        r"\bwhat\s+to\s+cook\s+for\b",
        r"\bdinner\s+(idea|recipe)\b",
        r"\bhow\s+to\s+make\s+(a\s+)?(cake|bread|pizza|pasta|soup|stew|salad|sandwich|omelette|pancake|cookie|pie|steak|burger|smoothie|juice)\b",
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
    "sports": [
        r"\bhow\s+to\s+play\s+(soccer|football|basketball|baseball|tennis|golf|hockey|cricket|volleyball|rugby|badminton|lacrosse|handball|pickleball|ping\s*pong|table\s*tennis|squash|bowling|softball|water\s*polo)\b",
        r"\brules\s+of\s+(soccer|football|basketball|baseball|tennis|golf|hockey|cricket|volleyball|rugby|badminton|boxing|wrestling|mma|karate|judo|swimming|lacrosse|handball|pickleball)\b",
        r"\bhow\s+to\s+(kick|throw|shoot|dribble|serve|volley|tackle|block|spike|punt|swing|pitch)\s+(a\s+)?(ball|puck|shuttlecock)?\b",
        r"\b(soccer|football|basketball|baseball|tennis|golf|hockey|cricket|volleyball|rugby|badminton|boxing|wrestling|mma|karate|judo)\s+(basics|rules|tutorial|technique|drill|strategy|play|position|formation|offense|defense)\b",
        r"\bhow\s+to\s+(swim|surf|ski|snowboard|skate|skateboard|run|sprint|marathon|cycle|hike|climb|kayak|row|sail|dive|box|wrestle|fence)\b",
    ],
    "entertainment": [
        r"\bhow\s+to\s+(sing|dance|draw|paint|sculpt|act|perform|juggle|knit|crochet|sew|embroider)\b",
        r"\b(movie|film|show|tv|series|anime|manga|novel|book)\s+recommendation\b",
        r"\brecommend\s+(a|me|some)\s+(movie|film|show|tv|book|novel|anime|manga|game|song)\b",
        r"\bwhat\s+(movie|show|series|anime|book|novel|game)\s+should\s+I\s+(watch|read|play)\b",
    ],
    "music_instrument": [
        r"\bhow\s+to\s+play\s+(guitar|piano|drums|bass|violin|ukulele|flute|saxophone|trumpet|clarinet|cello|harmonica|banjo|mandolin|keyboard|organ)\b",
        r"\b(chord|scale|key|tempo|rhythm|melody|harmony)\s+(progression|theory|lesson|chart)\b",
        r"\bmusic\s+theory\b",
        r"\bhow\s+to\s+(read|write)\s+(sheet\s+)?music\b",
        r"\bhow\s+to\s+tune\s+(a\s+)?(guitar|piano|violin|ukulele)\b",
    ],
    "language_learning": [
        r"\bhow\s+to\s+(speak|learn|say|translate|pronounce)\s+(spanish|french|german|japanese|chinese|korean|arabic|italian|portuguese|russian|hindi|mandarin|cantonese|swahili|dutch|swedish|norwegian|polish|turkish|thai|vietnamese|greek|hebrew|latin)\b",
        r"\btranslate\s+.+\s+to\s+(spanish|french|german|japanese|chinese|korean|arabic|italian|portuguese|russian|hindi|english)\b",
        r"\bhow\s+do\s+you\s+say\s+.+\s+in\s+(spanish|french|german|japanese|chinese|korean|arabic|italian|portuguese|russian|hindi)\b",
    ],
    "gaming": [
        r"\bhow\s+to\s+(play|beat|win|complete|finish)\s+(minecraft|fortnite|valorant|league\s+of\s+legends|roblox|gta|zelda|elden\s+ring|destiny|cod|call\s+of\s+duty|overwatch|apex|dota|smash|mario|pokemon|fifa|madden|nba\s+2k|chess|checkers|monopoly|uno|poker|blackjack)\b",
        r"\b(fortnite|minecraft|valorant|roblox|gta|zelda|elden\s+ring|apex|overwatch)\s+(tips|guide|tutorial|strategy|build|loadout|walkthrough)\b",
    ],
    "history_trivia": [
        r"\bwho\s+(was|is|were)\s+(the\s+)?(first|last|oldest|youngest|tallest|shortest|fastest|biggest|smallest)\b",
        r"\bwhen\s+(was|did|were)\s+(the\s+)?(world\s+war|civil\s+war|revolution|declaration|constitution|moon\s+landing)\b",
        r"\bwhat\s+(year|century|decade)\s+(was|did|were)\b.{5,}",
    ],
    "how_to_general": [
        r"\bhow\s+to\s+(tie|fold|iron|sew|knit|crochet|braid|plait)\s+(a\s+)?(tie|shirt|napkin|paper|bow|knot|blanket|scarf)\b",
        r"\bhow\s+to\s+(ride|drive|fly|sail|operate)\s+(a\s+)?(bike|bicycle|motorcycle|car|boat|plane|drone|tractor|forklift)\b",
        r"\bhow\s+to\s+(clean|wash|dry|bleach|polish|wax|scrub|disinfect|sanitize)\b",
        r"\bhow\s+to\s+(plant|grow|garden|prune|harvest|compost|mulch|fertilize)\b",
        r"\bhow\s+to\s+(train|groom|feed|walk|bathe)\s+(a\s+)?(dog|cat|puppy|kitten|pet|horse|fish|bird|hamster|rabbit)\b",
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
