from __future__ import annotations

from dataclasses import dataclass
import re


CREATOR_START_PATTERNS = [
    re.compile(r"\bwhen\s+(?:did|do|was|were)\s+(?:you|u|he|she|they)\s+(?:start|begin|began|get\s+into|got\s+into|trade|day\s*trad(?:e|ing)|invest(?:ing)?|build|built|launch|launched|create|created)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+long\s+(?:have|has)\s+(?:you|u|he|she|they)\s+been\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(?:year|date|month)\s+did\s+(?:you|u|he|she|they)\b", re.IGNORECASE),
]

CREATOR_JOURNEY_PATTERNS = [
    re.compile(r"\bwhat\s+made\s+(?:you|u|him|her|them)\s+start\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+did\s+(?:you|u|he|she|they)\s+start\b", re.IGNORECASE),
    re.compile(r"\bhow\s+did\s+(?:you|u|he|she|they)\s+get\s+into\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+got\s+(?:you|u|him|her|them)\s+into\b", re.IGNORECASE),
]

PUBLICATION_TOKENS = (
    "published",
    "publication",
    "release",
    "released",
    "launch",
    "launched",
    "come out",
    "write",
    "wrote",
    "written",
    "book",
    "ebook",
    "e book",
    "author",
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
    return any(token in lowered for token in PUBLICATION_TOKENS)


def is_creator_start_timeline_question(question: str) -> bool:
    lowered = str(question or "").strip().lower()
    if not lowered:
        return False
    return any(pattern.search(lowered) for pattern in CREATOR_START_PATTERNS)


def is_creator_journey_question(question: str) -> bool:
    lowered = str(question or "").strip().lower()
    if not lowered:
        return False
    return any(pattern.search(lowered) for pattern in CREATOR_JOURNEY_PATTERNS)


def is_creator_identity_question(question: str) -> bool:
    lowered = str(question or "").strip().lower()
    if not lowered:
        return False
    return any(pattern.search(lowered) for pattern in IDENTITY_PATTERNS)


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
        "launch",
        "launched",
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

    if is_publication_timeline_question(question) and is_timeline_question(question, query_goal=query_goal):
        return CreatorFactPolicy(
            kind="publication_timeline",
            focus=focus,
            requires_web=True,
            requires_verified_sources=True,
            is_personal=True,
            fact_field="publication_date" if str(entity_type or "").lower() == "book" else "launch_date",
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

    if is_creator_journey_question(question):
        return CreatorFactPolicy(
            kind="creator_journey",
            focus=focus,
            requires_web=True,
            requires_verified_sources=False,
            is_personal=True,
            fact_field="public_fact",
        )

    if any(token in lowered for token in ("price", "pricing", "cost", "how much")):
        return CreatorFactPolicy(kind="price", requires_web=True, requires_verified_sources=True, is_personal=False, fact_field="price")

    if any(token in lowered for token in ("followers", "subscribers", "members", "students", "ranking", "ranked", "valuation", "net worth")):
        return CreatorFactPolicy(kind="stats", requires_web=True, requires_verified_sources=True, is_personal=False, fact_field="followers")

    if re.search(r"\bwhere can i (?:buy|get|find|purchase)\b", lowered, re.IGNORECASE):
        return CreatorFactPolicy(kind="availability", requires_web=True, requires_verified_sources=True, is_personal=False, fact_field="official_url")

    return CreatorFactPolicy(kind="general", focus=focus, requires_web=False, requires_verified_sources=False, is_personal=False, fact_field="public_fact")