from __future__ import annotations

from dataclasses import dataclass
import re


CREATOR_START_PATTERNS = [
    re.compile(r"\bwhen\s+(?:did|do|was|were)\s+(?:you|u|he|she|they)\s+(?:start|begin|began|get\s+into|got\s+into|trade|day\s*trad(?:e|ing)|invest(?:ing)?|build|built|launch|launched|create|created)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+long\s+(?:have|has)\s+(?:you|u|he|she|they)\s+been\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(?:year|date|month)\s+did\s+(?:you|u|he|she|they)\b", re.IGNORECASE),
]

CREATOR_JOURNEY_PATTERNS = [
    re.compile(r"\bhow\s+did\s+(?:you|u|he|she|they)\s+(?:get|become)\s+(?:rich|wealthy|successful)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+did\s+(?:you|u|he|she|they)\s+(?:make|build)\s+(?:your|ur|his|her|their)?\s*(?:money|wealth|fortune)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+made\s+(?:you|u|him|her|them)\s+(?:rich|wealthy|successful)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+inspired\s+(?:you|u|him|her|them)\s+to\s+(?:start|build|found|launch|create)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(?:inspired|made|led|motivated)\s+(?:you|u|him|her|them)\s+to\s+(?:write|create|make|build|publish|release|launch)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+made\s+(?:you|u|him|her|them)\s+start\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+did\s+(?:you|u|he|she|they)\s+start\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+did\s+(?:you|u|he|she|they)\s+(?:write|create|make|build|publish|release|launch)\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+did(?:n'?t| not)\s+(?:you|u|he|she|they)\s+(?:just\s+)?(?:retire|stop|quit)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+did\s+(?:you|u|he|she|they)\s+get\s+into\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+got\s+(?:you|u|him|her|them)\s+into\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+did\s+(?:you|u|he|she|they)\s+do\s+(?:at|in|with|for)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+was\s+(?:your|ur|his|her|their)\s+role\s+(?:at|in|with|for)\b", re.IGNORECASE),
    re.compile(r"\btell\s+me\s+about\s+(?:your|ur|his|her|their)\s+time\s+(?:at|in|with)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+did\s+(?:you|u|he|she|they)\s+(?:build|scale|grow)\b", re.IGNORECASE),
]

CREATOR_JOURNEY_TURNING_POINT_PATTERNS = [
    re.compile(
        r"\bwhat\s+(?:made|led|pushed|forced|motivated|inspired)\s+(?:you|u|him|her|them)\s+(?:to\s+)?(?:turn|change|rebuild|fix|straighten)\s+(?:your|ur|his|her|their)?\s*(?:life|path|career|future|self)\s*(?:around)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bhow\s+did\s+(?:you|u|he|she|they)\s+(?:turn|change|rebuild|fix|straighten)\s+(?:your|ur|his|her|their)?\s*(?:life|path|career|future|self)\s*(?:around)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:what|why|how)\s+(?:made|did|led|pushed|forced|motivated|inspired)?\s*(?:you|u|he|she|they|him|her|them)?\s*(?:to\s+)?turn\s+(?:it|that|this|things)\s+around\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat\s+(?:was|were)\s+(?:your|ur|his|her|their)\s+(?:turning\s+point|rock\s+bottom|wake\s+up\s+call|wake-up\s+call)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:hit|hitting|reached|reaching)\s+rock\s+bottom\b|\brock\s+bottom\s+(?:moment|point|story)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat\s+(?:made|caused|got|led)\s+(?:you|u|him|her|them)\s+to\s+(?:change|stop|start\s+over|clean\s+up|turn\s+things\s+around)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhy\s+did\s+(?:you|u|he|she|they)\s+(?:change|stop|start\s+over|clean\s+up|turn\s+things\s+around)\b",
        re.IGNORECASE,
    ),
]

PUBLICATION_DATE_TOKENS = (
    "published",
    "publication",
    "release",
    "released",
    "come out",
)
PUBLICATION_SUBJECT_TOKENS = (
    "write",
    "wrote",
    "written",
    "book",
    "ebook",
    "e book",
    "author",
)
TEMPORAL_LOOKUP_TOKENS = (
    "when",
    "what year",
    "what date",
    "which month",
    "date",
    "year",
    "month",
)

CATALOG_PATTERNS = [
    re.compile(r"\bhow\s+many\s+(books|courses|programs|podcasts|shows)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(books|courses|programs|podcasts|shows)\b", re.IGNORECASE),
    re.compile(r"\bwhich\s+(books|courses|programs|podcasts|shows)\b", re.IGNORECASE),
    re.compile(r"\bhave\s+(?:you|u)\s+(?:written|published|made|created)\b", re.IGNORECASE),
    re.compile(r"\b(?:books|courses|programs|podcasts|shows)\s+(?:have\s+)?(?:you|u)\s+(?:written|published|made|created)\b", re.IGNORECASE),
]

IDENTITY_PATTERNS = [
    re.compile(r"\b(?:what(?:'s|\s+is)?|whats)\s+(?:your|ur|u)\s+(?:full|real|legal)\s+name\b", re.IGNORECASE),
    re.compile(r"\b(?:tell\s+me|say)\s+(?:your|ur|u)\s+(?:full|real|legal)\s+name\b", re.IGNORECASE),
    re.compile(r"\b(?:what(?:'s|\s+is)?|whats)\s+(?:your|ur|u)\s+last\s+name\b", re.IGNORECASE),
    re.compile(r"\b(?:real|full|legal)\s+name\b", re.IGNORECASE),
]

ROLE_PATTERNS = [
    re.compile(r"\bwhat\s+do\s+(?:you|u)\s+(?:manage|run|own|operate|lead)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(?:company|business|companies|businesses)\s+do\s+(?:you|u)\s+(?:manage|run|own|operate|lead|found)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(?:are|were)\s+(?:you|u)\s+(?:founder|co-?founder|owner|ceo|partner|managing\s+partner)\s+of\b", re.IGNORECASE),
    re.compile(r"\b(?:founder|co-?founder|owner|ceo|partner|managing\s+partner)\s+of\s+what\b", re.IGNORECASE),
    re.compile(r"\b(?:your|ur)\s+(?:company|business|companies|businesses|role|job|title)\b", re.IGNORECASE),
    re.compile(r"\b(?:what|which)\s+(?:company|business)\s+(?:are|were)\s+(?:you|u)\s+(?:with|at|part\s+of)\b", re.IGNORECASE),
    re.compile(r"\b(?:managing\s+partner|founder|co-?founder|ceo)\b", re.IGNORECASE),
]

PUBLIC_PROFILE_PATTERNS = [
    (
        "age",
        re.compile(
            r"\b(?:how\s+old\s+(?:are|is)\s+(?:you|u|he|she|they)|(?:your|ur|his|her|their)\s+age|when\s+(?:were|was)\s+(?:you|u|he|she|they)\s+born|(?:your|ur|his|her|their)\s+birthday)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "relationship_status",
        re.compile(
            r"\b(?:are|is|was|were)\s+(?:you|u|he|she|they)\s+(?:married|dating|single)|\b(?:do|does|did)\s+(?:you|u|he|she|they)\s+have\s+(?:a\s+)?(?:wife|wifey|husband|girlfriend|boyfriend|partner|spouse|missus|misus|mrs|missis)\b|\bwho(?:'s|\s+is|s)?\s+(?:your|ur|his|her|their)\s+(?:wife|wifey|husband|girlfriend|boyfriend|partner|spouse|missus|misus|mrs|missis)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "relationship_story",
        re.compile(
            r"\b(?:how|where|when)\s+did\s+(?:you|u|he|she|they)\s+(?:meet|meet\s+your|meet\s+his|meet\s+her)\b|\b(?:where|when|what)\s+(?:was|were)\s+(?:your|ur|his|her|their)\s+first\s+date\b|\b(?:first\s+date|how\s+you\s+met|how\s+we\s+met|how\s+i\s+met)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "family",
        re.compile(
            r"\b(?:do|does|did)\s+(?:you|u|he|she|they)\s+have\s+(?:kids|children|a\s+son|a\s+daughter)|\b(?:your|ur|his|her|their)\s+(?:kids|children|family|son|daughter)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "public_location",
        re.compile(
            r"\b(?:where\s+(?:are|were|was|is)\s+(?:you|u|he|she|they)\s+(?:from|based)|where\s+(?:were|was)\s+(?:you|u|he|she|they)\s+born|(?:your|ur|his|her|their)\s+hometown)\b",
            re.IGNORECASE,
        ),
    ),
]

FINANCIAL_STAT_PATTERNS = [
    re.compile(r"\b(?:revenue|arr|valuation|net\s+worth|profit|profits|income|earnings)\b", re.IGNORECASE),
    re.compile(r"\bsales\s+(?:figures?|numbers?|volume|revenue|total|totals|report|reports)\b", re.IGNORECASE),
    re.compile(r"\b(?:current|latest|annual|monthly|yearly|last\s+year|this\s+year)\s+sales\b", re.IGNORECASE),
    re.compile(r"\b(?:how\s+much|what(?:'s|\s+is)?|whats|most|highest|biggest).{0,45}\b(?:made|earned)\b", re.IGNORECASE),
    re.compile(r"\b(?:you|u|he|she|they).{0,25}\b(?:made|earned)\b", re.IGNORECASE),
    re.compile(r"\b(?:made|earned).{0,45}\b(?:money|revenue|sales|income|profit)\b", re.IGNORECASE),
]


@dataclass(frozen=True)
class CreatorFactPolicy:
    kind: str
    focus: str = ""
    requires_web: bool = False
    requires_verified_sources: bool = False
    is_personal: bool = False
    fact_field: str = "public_fact"


def extract_timeline_focus(question: str) -> str:
    normalized = re.sub(r"\s+", " ", str(question or "")).strip(" ?!.").lower()
    patterns = [
        re.compile(r"what\s+inspired\s+(?:you|u|him|her|them)\s+to\s+(?:start|build|found|launch|create)\s+(.+)$", re.IGNORECASE),
        re.compile(r"what\s+(?:inspired|made|led|motivated)\s+(?:you|u|him|her|them)\s+to\s+(?:write|create|make|build|publish|release|launch)\s+(.+)$", re.IGNORECASE),
        re.compile(r"why\s+did\s+(?:you|u|he|she|they)\s+(?:write|create|make|build|publish|release|launch)\s+(.+)$", re.IGNORECASE),
        re.compile(r"(?:start|started|begin|began|get into|got into)\s+(.+)$", re.IGNORECASE),
        re.compile(r"how\s+long\s+(?:have|has)\s+(?:you|u|he|she|they)\s+been\s+(.+)$", re.IGNORECASE),
        re.compile(r"what\s+made\s+(?:you|u|him|her|them)\s+start\s+(.+)$", re.IGNORECASE),
        re.compile(r"why\s+did\s+(?:you|u|he|she|they)\s+start\s+(.+)$", re.IGNORECASE),
        re.compile(r"how\s+did\s+(?:you|u|he|she|they)\s+get\s+into\s+(.+)$", re.IGNORECASE),
        re.compile(r"what\s+got\s+(?:you|u|him|her|them)\s+into\s+(.+)$", re.IGNORECASE),
    ]
    for pattern in patterns:
        match = pattern.search(normalized)
        if match:
            focus = re.sub(r"\s+", " ", match.group(1)).strip(" \"'")
            focus = re.sub(r"^(?:doing|in|into|on)\s+", "", focus)
            focus = re.split(r"\s*[,;?]\s*", focus, maxsplit=1)[0].strip(" \"'")
            focus = re.split(
                r"\b(?:why\s+did|why\s+didn'?t|why\s+not|after\s+scaling)\b",
                focus,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(" \"'")
            if focus:
                return focus
    for candidate in (
        "day trading",
        "trading",
        "investing",
        "youtube",
        "dropshipping",
        "business",
        "podcast",
        "content creation",
    ):
        if candidate in normalized:
            return candidate
    return ""


def is_publication_timeline_question(question: str) -> bool:
    lowered = str(question or "").lower()
    if any(token in lowered for token in PUBLICATION_DATE_TOKENS):
        return True
    if any(token in lowered for token in PUBLICATION_SUBJECT_TOKENS) and any(token in lowered for token in TEMPORAL_LOOKUP_TOKENS):
        return True
    return bool(
        re.search(
            r"\b(?:when|what\s+year|what\s+date|which\s+month)\b.*\b(?:launch|launched)\b",
            lowered,
            re.IGNORECASE,
        )
    )


def is_creator_start_timeline_question(question: str) -> bool:
    lowered = str(question or "").strip().lower()
    if not lowered:
        return False
    return any(pattern.search(lowered) for pattern in CREATOR_START_PATTERNS)


def is_creator_journey_question(question: str) -> bool:
    lowered = str(question or "").strip().lower()
    if not lowered:
        return False
    return is_creator_journey_turning_point_question(lowered) or any(pattern.search(lowered) for pattern in CREATOR_JOURNEY_PATTERNS)


def is_creator_journey_turning_point_question(question: str) -> bool:
    lowered = str(question or "").strip().lower()
    if not lowered:
        return False
    return any(pattern.search(lowered) for pattern in CREATOR_JOURNEY_TURNING_POINT_PATTERNS)


def is_creator_identity_question(question: str) -> bool:
    lowered = str(question or "").strip().lower()
    if not lowered:
        return False
    return any(pattern.search(lowered) for pattern in IDENTITY_PATTERNS)


def is_creator_role_question(question: str) -> bool:
    lowered = str(question or "").strip().lower()
    if not lowered:
        return False
    return any(pattern.search(lowered) for pattern in ROLE_PATTERNS)


def public_profile_fact_field(question: str) -> str:
    lowered = str(question or "").strip().lower()
    if not lowered:
        return ""
    if any(token in lowered for token in ("address", "phone", "email", "bank", "tax", "ssn", "credit card", "where do you live")):
        return ""
    for field, pattern in PUBLIC_PROFILE_PATTERNS:
        if pattern.search(lowered):
            return field
    return ""


def is_timeline_question(question: str, query_goal: str = "") -> bool:
    lowered = str(question or "").lower()
    if query_goal == "timeline_lookup":
        return True
    explicit_date_tokens = (
        "publish",
        "published",
        "publication",
        "release",
        "released",
        "come out",
        "what year",
        "what date",
        "which month",
    )
    if any(token in lowered for token in explicit_date_tokens):
        return True
    if is_creator_start_timeline_question(lowered):
        return True
    if any(token in lowered for token in ("write", "wrote", "written")) and any(
        token in lowered for token in ("when", "what year", "what date", "which month")
    ):
        return True
    return False


def looks_like_catalog_question(question: str, query_goal: str = "") -> bool:
    lowered = str(question or "").lower()
    if query_goal == "entity_catalog_lookup":
        return True
    return any(pattern.search(lowered) for pattern in CATALOG_PATTERNS)


def classify_creator_fact_query(question: str, *, entity_type: str = "", query_goal: str = "") -> CreatorFactPolicy:
    lowered = str(question or "").lower().strip()
    focus = extract_timeline_focus(question)

    if looks_like_catalog_question(question, query_goal=query_goal):
        return CreatorFactPolicy(kind="catalog", requires_web=True, requires_verified_sources=True, is_personal=False, fact_field="public_fact")

    if is_creator_identity_question(question):
        return CreatorFactPolicy(
            kind="identity",
            focus="creator_identity",
            requires_web=True,
            requires_verified_sources=True,
            is_personal=False,
            fact_field="full_name",
        )

    if is_creator_role_question(question):
        return CreatorFactPolicy(
            kind="role",
            focus="creator_role",
            requires_web=True,
            requires_verified_sources=True,
            is_personal=False,
            fact_field="role",
        )

    profile_field = public_profile_fact_field(question)
    if profile_field:
        return CreatorFactPolicy(
            kind="public_profile",
            focus=profile_field,
            requires_web=True,
            requires_verified_sources=True,
            is_personal=True,
            fact_field=profile_field,
        )

    if is_publication_timeline_question(question) and is_timeline_question(question, query_goal=query_goal):
        return CreatorFactPolicy(
            kind="publication_timeline",
            focus=focus,
            requires_web=True,
            requires_verified_sources=True,
            is_personal=True,
            fact_field="publication_date" if str(entity_type or "").lower() == "book" else "launch_date",
        )

    if is_creator_journey_question(question):
        turning_point = is_creator_journey_turning_point_question(question)
        return CreatorFactPolicy(
            kind="creator_journey",
            focus="turning_point" if turning_point else focus,
            requires_web=True,
            requires_verified_sources=False,
            is_personal=True,
            fact_field="journey_turning_point" if turning_point else "public_fact",
        )

    if is_creator_start_timeline_question(question):
        return CreatorFactPolicy(
            kind="creator_start_timeline",
            focus=focus,
            requires_web=True,
            requires_verified_sources=True,
            is_personal=True,
            fact_field="start_date",
        )

    financial_stat = any(pattern.search(lowered) for pattern in FINANCIAL_STAT_PATTERNS)

    if any(token in lowered for token in ("price", "pricing", "cost")) or ("how much" in lowered and not financial_stat):
        return CreatorFactPolicy(kind="price", requires_web=True, requires_verified_sources=True, is_personal=False, fact_field="price")

    if any(token in lowered for token in ("followers", "subscribers", "members", "students", "ranking", "ranked")) or financial_stat:
        return CreatorFactPolicy(kind="stats", requires_web=True, requires_verified_sources=True, is_personal=False, fact_field="followers")

    if re.search(r"\bwhere can i (?:buy|get|find|purchase)\b", lowered, re.IGNORECASE):
        return CreatorFactPolicy(kind="availability", requires_web=True, requires_verified_sources=True, is_personal=False, fact_field="official_url")

    return CreatorFactPolicy(kind="general", focus=focus, requires_web=False, requires_verified_sources=False, is_personal=False, fact_field="public_fact")
