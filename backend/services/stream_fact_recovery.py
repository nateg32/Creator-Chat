from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.services.creator_fact_policy import classify_creator_fact_query


def _normalize_search_mode(value: Any) -> str:
    normalized = str(value or "hybrid").strip().lower()
    return "ingested_only" if normalized == "ingested" else normalized


def _normalize_personal_sources(sources: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for idx, source in enumerate(sources or [], start=1):
        title = str(source.get("title") or source.get("text") or f"Source {idx}").strip()
        url = str(source.get("url") or "").strip()
        if url or title:
            normalized.append(
                {
                    "source_id": f"personal_{idx}",
                    "title": title[:140],
                    "url": url,
                    "snippet": str(source.get("text") or "")[:240],
                    "platform": str(source.get("source") or "profile"),
                }
            )
    return normalized


def _get_personal_bio_service() -> Any:
    from backend.services.personal_bio_service import personal_bio_service

    return personal_bio_service


def recover_streamed_creator_fact_answer(
    *,
    user_id: int,
    creator_id: int,
    question: str,
    creator_row: Optional[Dict[str, Any]],
    conversation_history: Optional[List[Dict[str, str]]] = None,
    personal_service: Optional[Any] = None,
) -> Dict[str, Any]:
    policy = classify_creator_fact_query(question or "")
    if policy.kind not in {"creator_journey", "creator_start_timeline", "publication_timeline"}:
        return {"answer": "", "citations": [], "move": None}

    creator_profile = dict(creator_row or {})
    if not creator_profile:
        return {"answer": "", "citations": [], "move": None}

    creator_name = str(creator_profile.get("name") or creator_profile.get("handle") or "the creator").strip()
    service = personal_service or _get_personal_bio_service()
    result = service.handle_personal_question(
        user_id=user_id,
        creator_id=creator_id,
        question=question,
        voice_profile=creator_profile.get("voice_profile") or {},
        creator_name=creator_name or "the creator",
        decision_policy=creator_profile.get("decision_policy") or {},
        creator_profile=creator_profile,
        conversation_history=conversation_history,
        allow_web=_normalize_search_mode(creator_profile.get("search_mode")) == "hybrid",
    )

    answer = str((result or {}).get("answer") or "").strip()
    if not answer:
        return {"answer": "", "citations": [], "move": (result or {}).get("move")}

    return {
        "answer": answer,
        "citations": _normalize_personal_sources((result or {}).get("sources")),
        "move": (result or {}).get("move"),
    }