"""Compact short-term memory packet for creator chat turns.

This module intentionally has no database, cache, or model dependency. It is
safe to use in the hot chat path because it only compresses recent history and
an already-loaded thread snapshot into a small packet for Gemini to reason over.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence


_FOLLOWUP_RE = re.compile(
    r"\b(?:"
    r"that|this|it|one|same|earlier|previous|above|mentioned|mean|"
    r"why|how|what made|link|links|source|sources|video|episode|podcast|"
    r"resource|watch|listen|read|more|break\s*down|breakdown|summary|"
    r"summari[sz]e|recap|takeaways?|explain|elaborate"
    r")\b",
    re.IGNORECASE,
)
_RESOURCE_FOLLOWUP_RE = re.compile(
    r"\b(?:"
    r"link|links|source|sources|video|episode|podcast|resource|watch|listen|read|"
    r"deep|full|detailed|proper|complete|break\s*down|breakdown|summary|"
    r"summari[sz]e|recap|takeaways?|main\s+points|key\s+points|lessons?"
    r")\b",
    re.IGNORECASE,
)
_LOW_SIGNAL_ATTACHMENT_WORDS = {"copy", "source", "sources", "attached", "link", "links"}


def clean_text(value: Any, *, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = text.strip(" \t\r\n-:;,.")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].strip()
    return text


def clean_list(values: Any, *, limit: int = 6, item_limit: int = 120) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    seen = set()
    for value in values:
        text = clean_text(value, limit=item_limit)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _message_text(message: Dict[str, Any]) -> str:
    return clean_text(message.get("content") or message.get("text") or "", limit=700)


def latest_message(history: Optional[Sequence[Dict[str, Any]]], role: str) -> str:
    target = str(role or "").lower()
    for item in reversed(list(history or [])):
        if str(item.get("role") or "").lower() == target:
            return _message_text(item)
    return ""


def last_question_from_text(text: str) -> str:
    questions = re.findall(r"([^?]{6,180}\?)", str(text or ""))
    if not questions:
        return ""
    return clean_text(questions[-1], limit=180)


def split_memory_sentences(text: str, *, limit: int = 3) -> List[str]:
    out: List[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", str(text or "")):
        clean = clean_text(sentence, limit=220)
        if len(clean.split()) < 5:
            continue
        lowered = clean.lower()
        if any(word in lowered for word in _LOW_SIGNAL_ATTACHMENT_WORDS) and len(clean.split()) <= 10:
            continue
        out.append(clean)
        if len(out) >= limit:
            break
    return out


def extract_recent_resources(
    history: Optional[Sequence[Dict[str, Any]]],
    *,
    assistant_resources: Optional[Sequence[Dict[str, Any]]] = None,
    limit: int = 5,
) -> List[Dict[str, str]]:
    """Return newest resource/card/citation refs from assistant turns."""

    resources: List[Dict[str, str]] = []
    seen = set()

    def _add(raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        title = clean_text(raw.get("title") or raw.get("text") or raw.get("label"), limit=160)
        url = clean_text(raw.get("url") or raw.get("canonical_url") or raw.get("source_url"), limit=500)
        platform = clean_text(raw.get("platform") or raw.get("source") or raw.get("domain"), limit=60)
        if not title and not url:
            return
        key = (title.lower(), url.lower())
        if key in seen:
            return
        seen.add(key)
        resources.append({"title": title, "url": url, "platform": platform})

    for item in assistant_resources or []:
        _add(item)

    for msg in reversed(list(history or [])[-10:]):
        if str(msg.get("role") or "").lower() != "assistant":
            continue
        for bucket in ("cards", "citations"):
            values = msg.get(bucket) or []
            if isinstance(values, list):
                for value in values:
                    _add(value)
                    if len(resources) >= limit:
                        return resources[:limit]
        text = str(msg.get("content") or msg.get("text") or "")
        for quoted in re.findall(r'"([^"\n]{4,140})"', text):
            _add({"title": quoted})
            if len(resources) >= limit:
                return resources[:limit]

    return resources[:limit]


def extract_entities(*texts: str, resources: Optional[Sequence[Dict[str, str]]] = None, limit: int = 6) -> List[str]:
    entities: List[str] = []
    seen = set()

    def _add(value: str) -> None:
        clean = clean_text(value, limit=90).strip(" \"'.,:;!?")
        if not clean or len(clean) < 3:
            return
        key = clean.lower()
        if key in seen:
            return
        seen.add(key)
        entities.append(clean)

    for resource in resources or []:
        title = resource.get("title") or ""
        if title:
            _add(title)

    for text in texts:
        raw = str(text or "")
        for quoted in re.findall(r'"([^"]{3,90})"', raw):
            _add(quoted)
        for titled in re.findall(
            r"\b([A-Z][A-Za-z0-9$&'.-]+(?:\s+[A-Z][A-Za-z0-9$&'.-]+){1,7})\b",
            raw,
        ):
            if titled.lower() in {"you tube", "apple podcasts", "the game"}:
                continue
            _add(titled)
        if len(entities) >= limit:
            break
    return entities[:limit]


def _looks_like_followup(question: str, history: Optional[Sequence[Dict[str, Any]]]) -> bool:
    text = str(question or "").strip()
    if not text or not history:
        return False
    words = re.findall(r"[a-z0-9']+", text.lower())
    return bool(len(words) <= 18 and _FOLLOWUP_RE.search(text))


def _looks_like_resource_followup(question: str, resources: Sequence[Dict[str, str]], latest_assistant: str) -> bool:
    text = str(question or "")
    if not _RESOURCE_FOLLOWUP_RE.search(text):
        return False
    if resources:
        return True
    return bool(
        re.search(
            r"\b(?:attached|linked|video|episode|podcast|resource|watch|listen|read)\b",
            latest_assistant or "",
            re.IGNORECASE,
        )
    )


def _looks_like_turnaround_followup(question: str, latest_assistant: str) -> bool:
    current = str(question or "")
    assistant = str(latest_assistant or "")
    if not re.search(r"\bturn\s+(?:it|that|this|things)\s+around\b", current, re.IGNORECASE):
        return False
    return bool(
        re.search(
            r"\b(?:turn(?:ed|ing)?\s+(?:my|your|his|her|their)?\s*(?:life|path|career|future)?\s*around|"
            r"journey|background|story|dark\s+place|rock\s+bottom|legal\s+system|convict|"
            r"stolen\s+cars|trauma|changed\s+(?:my|his|her|their)\s+life)\b",
            assistant,
            re.IGNORECASE,
        )
    )


def _infer_current_topic(
    question: str,
    latest_user: str,
    latest_assistant: str,
    snapshot: Dict[str, Any],
    resources: Sequence[Dict[str, str]],
) -> str:
    for key in ("current_topic", "pending_followup_target", "next_best_step"):
        value = clean_text(snapshot.get(key), limit=130)
        if value:
            return value
    if resources:
        title = clean_text(resources[0].get("title"), limit=130)
        if title:
            return title
    if latest_user and len(latest_user.split()) > 3:
        return latest_user
    if latest_assistant:
        claims = split_memory_sentences(latest_assistant, limit=1)
        if claims:
            return claims[0]
    return clean_text(question, limit=130)


def build_conversation_memory_packet(
    question: str,
    history: Optional[Sequence[Dict[str, Any]]],
    *,
    snapshot: Optional[Dict[str, Any]] = None,
    assistant_resources: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the compact packet Gemini should reason over for this turn."""

    snapshot = snapshot or {}
    latest_user = latest_message(history, "user")
    latest_assistant = latest_message(history, "assistant")
    resources = extract_recent_resources(history, assistant_resources=assistant_resources)
    resource_title = clean_text(resources[0].get("title"), limit=160) if resources else ""

    resource_followup = _looks_like_resource_followup(question, resources, latest_assistant)
    turnaround_followup = _looks_like_turnaround_followup(question, latest_assistant)
    generic_followup = _looks_like_followup(question, history)
    last_question = last_question_from_text(latest_assistant) or clean_text(snapshot.get("last_assistant_question"), limit=180)
    current_topic = _infer_current_topic(question, latest_user, latest_assistant, snapshot, resources)

    if resource_followup and resource_title:
        target_hint = f'Resolve this as a request about the previously mentioned resource: "{resource_title}".'
        followup_kind = "resource_breakdown"
    elif turnaround_followup:
        target_hint = "Resolve this as the creator's public turning point/catalyst, not the full biography again."
        followup_kind = "creator_turnaround"
    elif generic_followup and last_question:
        target_hint = f"Resolve this against the last assistant question: {last_question}"
        followup_kind = "answer_to_previous_question"
    elif generic_followup:
        target_hint = "Resolve this against the immediately previous assistant answer before answering."
        followup_kind = "general_followup"
    else:
        target_hint = ""
        followup_kind = ""

    return {
        "current_topic": current_topic,
        "latest_user_before_current": clean_text(latest_user, limit=260),
        "last_assistant_question": last_question,
        "last_assistant_claims": split_memory_sentences(latest_assistant),
        "last_mentioned_entities_or_resources": extract_entities(
            latest_user,
            latest_assistant,
            resources=resources,
            limit=6,
        ),
        "last_referenced_resources": resources,
        "open_questions": clean_list(snapshot.get("open_questions"), limit=4, item_limit=120),
        "answered_questions": clean_list(snapshot.get("answered_questions"), limit=4, item_limit=120),
        "user_context": clean_list(snapshot.get("user_context"), limit=5, item_limit=100),
        "goals": clean_list(snapshot.get("goals"), limit=5, item_limit=100),
        "preferences": clean_list(snapshot.get("preferences"), limit=4, item_limit=100),
        "constraints": clean_list(snapshot.get("constraints"), limit=5, item_limit=110),
        "advice_given": clean_list(snapshot.get("advice_given"), limit=5, item_limit=120),
        "resources_shared": clean_list(snapshot.get("resources_shared"), limit=5, item_limit=130),
        "conversation_summary": clean_text(snapshot.get("conversation_summary"), limit=320),
        "next_best_step": clean_text(snapshot.get("next_best_step"), limit=140),
        "is_likely_contextual_followup": bool((generic_followup or resource_followup or turnaround_followup) and (latest_user or latest_assistant)),
        "contextual_followup_kind": followup_kind,
        "current_followup_target_hint": target_hint,
    }


def packet_prompt_block(packet: Dict[str, Any]) -> str:
    """Render a small prompt block from a packet. Empty packets render empty."""

    if not isinstance(packet, dict):
        return ""

    lines: List[str] = [
        "CONVERSATION MEMORY PACKET:",
        "Use this like human short-term memory. Continue the user's actual thread, do not repeat old answers, and do not mention this packet.",
    ]
    simple_fields = [
        ("current_topic", "Current topic"),
        ("conversation_summary", "Summary"),
        ("latest_user_before_current", "Previous user message"),
        ("last_assistant_question", "Last question asked"),
        ("current_followup_target_hint", "Follow-up target"),
        ("next_best_step", "Useful next step"),
    ]
    for key, label in simple_fields:
        value = clean_text(packet.get(key), limit=360)
        if value:
            lines.append(f"- {label}: {value}")

    list_fields = [
        ("user_context", "User context"),
        ("goals", "User goals"),
        ("constraints", "Constraints"),
        ("answered_questions", "Already answered"),
        ("advice_given", "Advice already given"),
        ("resources_shared", "Resources already shared"),
    ]
    for key, label in list_fields:
        values = clean_list(packet.get(key), limit=5, item_limit=120)
        if values:
            lines.append(f"- {label}: {'; '.join(values)}")

    resources = packet.get("last_referenced_resources") or []
    if isinstance(resources, list) and resources:
        resource_bits = []
        for resource in resources[:3]:
            if not isinstance(resource, dict):
                continue
            title = clean_text(resource.get("title"), limit=120)
            url = clean_text(resource.get("url"), limit=160)
            if title and url:
                resource_bits.append(f"{title} ({url})")
            elif title:
                resource_bits.append(title)
            elif url:
                resource_bits.append(url)
        if resource_bits:
            lines.append(f"- Recent resources: {'; '.join(resource_bits)}")

    if len(lines) <= 2:
        return ""
    return "\n".join(lines) + "\n"
