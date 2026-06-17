from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional


_DIRECT_SELF_HARM_PATTERNS = [
    re.compile(r"\bshould\s+i\s+(?:kill|hurt|harm)\s+myself\b", re.IGNORECASE),
    re.compile(r"\b(?:kill|hurt|harm)\s+myself\b", re.IGNORECASE),
    re.compile(r"\bend\s+my\s+life\b", re.IGNORECASE),
    re.compile(r"\bcommit\s+suicide\b", re.IGNORECASE),
    re.compile(r"\bi\s*(?:am|'m)?\s*(?:suicidal|going\s+to\s+kill\s+myself|gonna\s+kill\s+myself)\b", re.IGNORECASE),
    re.compile(r"\bi(?:'ve| have| just)?\s+(?:been\s+)?(?:feeling|felt|feel)\s+(?:really\s+|so\s+|very\s+)?suicidal\b", re.IGNORECASE),
    re.compile(r"\bi(?:'ve| have)?\s+(?:been\s+)?(?:having|getting)\s+suicidal\s+(?:thoughts|feelings|ideas|ideation)\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:want|wanna|need|plan|planning|might|could)\s+to\s+(?:die|kill\s+myself|end\s+my\s+life|hurt\s+myself|harm\s+myself)\b", re.IGNORECASE),
    re.compile(r"\bi\s+don'?t\s+want\s+to\s+(?:live|be\s+alive|be\s+here)\b", re.IGNORECASE),
    re.compile(r"\bi\s+can'?t\s+keep\s+(?:living|going)\b", re.IGNORECASE),
    re.compile(r"\bkms\b", re.IGNORECASE),
]

_EDUCATIONAL_OR_THIRD_PARTY_RE = re.compile(
    r"\b("
    r"prevention|hotline|warning\s+signs?|research|study|studies|article|essay|"
    r"song|lyrics|quote|book|movie|podcast|video|clip|post|transcript|"
    r"friend|someone|somebody|person|people|he|she|they|them|character"
    r")\b",
    re.IGNORECASE,
)

_CRISIS_FOLLOWUP_RE = re.compile(
    r"\b("
    r"did\s+(?:u|you)\s+ever\s+(?:feel|felt)|"
    r"have\s+(?:u|you)\s+ever\s+(?:felt|been)|"
    r"what\s+about\s+(?:u|you)|"
    r"(?:your|ur)\s+(?:career|life|story|experience)|"
    r"feel\s+like\s+that"
    r")\b",
    re.IGNORECASE,
)

_CRISIS_HISTORY_RE = re.compile(
    r"\b("
    r"suicidal|suicide|kill\s+myself|hurt\s+yourself|hurt\s+myself|harm\s+yourself|"
    r"harm\s+myself|local\s+lifeline|emergency\s+services|crisis\s+line|stay\s+with\s+me"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CrisisIntent:
    kind: str
    urgency: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "urgency": self.urgency, "reason": self.reason}


def detect_crisis_intent(message: str) -> Optional[CrisisIntent]:
    """Detect direct self-harm crisis turns before retrieval or persona rendering.

    This is intentionally conservative and fast. It catches direct first-person
    danger language that should never wait on search, source selection, or repair.
    """
    text = re.sub(r"\s+", " ", str(message or "").strip())
    if not text:
        return None

    direct_match = next((pattern for pattern in _DIRECT_SELF_HARM_PATTERNS if pattern.search(text)), None)
    if not direct_match:
        return None

    lowered = text.lower()
    first_person = bool(re.search(r"\b(i|i'm|im|me|my|myself)\b", lowered))
    direct_question = bool(re.search(r"\bshould\s+i\b", lowered))
    if not first_person and not direct_question:
        return None

    if _EDUCATIONAL_OR_THIRD_PARTY_RE.search(text) and not direct_question and "myself" not in lowered:
        return None

    return CrisisIntent(kind="self_harm", urgency="immediate", reason="direct first-person self-harm language")


def detect_crisis_followup_intent(message: str, history: Optional[list[Dict[str, Any]]] = None) -> Optional[CrisisIntent]:
    text = re.sub(r"\s+", " ", str(message or "").strip())
    if not text or not _CRISIS_FOLLOWUP_RE.search(text):
        return None

    recent_text = " ".join(
        re.sub(r"\s+", " ", str((item or {}).get("content") or (item or {}).get("text") or ""))[:500]
        for item in (history or [])[-4:]
    )
    if not _CRISIS_HISTORY_RE.search(recent_text):
        return None

    return CrisisIntent(kind="self_harm_followup", urgency="immediate", reason="follow-up inside active self-harm context")


def build_crisis_response(
    *,
    user_name: Optional[str] = None,
    creator_profile: Optional[Dict[str, Any]] = None,
    followup: bool = False,
) -> str:
    """Build a fast, creator-compatible safety answer without using retrieval."""
    name = re.sub(r"\s+", " ", str(user_name or "").strip())
    prefix = f"{name}, " if name else ""
    creator_name = ""
    if creator_profile:
        creator_name = str(creator_profile.get("name") or creator_profile.get("handle") or "").strip()
    direct_line = "no. Do not do that."
    if creator_name and "hormozi" in creator_name.lower():
        direct_line = "no. Absolutely not."

    emergency_line = (
        "If you might act on this, call your local emergency number right now. "
        "If you are not in immediate danger, contact your local suicide lifeline or crisis line now. "
        "If you do not know the number, search for the suicide crisis line in your country or call emergency services."
    )
    support_text = (
        "Text someone near you this exact line: \"I might hurt myself and I need you with me now.\" "
        "Stay with me and reply with one word: safe."
    )

    if followup:
        return (
            f"{prefix}I am not going to turn this into my story right now. "
            f"The priority is keeping you alive and not alone in this moment.\n\n"
            f"{emergency_line}\n\n"
            f"{support_text}"
        )

    return (
        f"{prefix}{direct_line} Move away from anything you could use to hurt yourself and get another person with you right now.\n\n"
        f"{emergency_line}\n\n"
        f"{support_text}"
    )
