import json
import logging
from typing import Any, Dict, List, Optional

import backend.rag as rag
from backend.services.live_search_rules import build_live_search_query, needs_fresh_public_web_search
from backend.services.research_provider import get_research_provider
from backend.settings import settings

logger = logging.getLogger(__name__)


def _trim_results(results: List[Dict[str, Any]], limit: int = 6) -> List[Dict[str, Any]]:
    trimmed = []
    seen = set()
    for item in results or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        key = (url or title).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        trimmed.append({
            "title": title,
            "url": url,
            "snippet": snippet,
            "source": item.get("source"),
        })
        if len(trimmed) >= limit:
            break
    return trimmed


class WebVerifyService:
    """Handles creator-adjacent factual questions using the real research provider."""

    def verify_fact(
        self,
        question: str,
        creator_profile: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        query = build_live_search_query(
            question,
            conversation_history,
            creator_name=(creator_profile or {}).get("name") or (creator_profile or {}).get("handle"),
            require_video=False,
        )
        intent_metadata = {"intent": "EVENT_PUBLIC_FACTS"} if needs_fresh_public_web_search(question, conversation_history) else None

        try:
            provider = get_research_provider()
            search_results = provider.search(
                query,
                creator_profile or {},
                conversation_history=conversation_history,
                intent_metadata=intent_metadata,
            )
        except Exception as exc:
            logger.error(f"Web verify search failed: {exc}")
            search_results = []

        search_results = _trim_results(search_results)
        if not search_results:
            return {
                "answer": "I couldn't verify that reliably from current public sources.",
                "confidence": 0.0,
                "sources": [],
            }

        prompt = f"""
You are a factual verifier for creator-chat responses.

User question:
{question}

Search results:
{json.dumps(search_results, indent=2)}

Rules:
- Use only the supplied search results.
- If the results conflict or stay vague, lower confidence instead of guessing.
- Prefer creator-owned or official sources when possible.
- For private, high-stakes, or weakly supported claims, answer conservatively.

Return ONLY JSON:
{{
  "answer": "short factual answer",
  "confidence": 0.0,
  "sources": [{{"title": "...", "url": "..."}}],
  "notes": "short rationale"
}}
"""

        try:
            resp = rag.generate_chat_completion(
                messages=[{"role": "system", "content": prompt}],
                model=settings.MODEL_VERIFY,
                temperature=0.0,
                json_mode=True,
            )
            data = json.loads(resp)
            return {
                "answer": str(data.get("answer") or "").strip() or "I couldn't verify that reliably from current public sources.",
                "confidence": float(data.get("confidence") or 0.0),
                "sources": data.get("sources") or [],
                "notes": data.get("notes") or "",
            }
        except Exception as exc:
            logger.error(f"Web verify synthesis failed: {exc}")
            return {
                "answer": "I found public sources, but I couldn't verify the answer cleanly enough to trust it.",
                "confidence": 0.0,
                "sources": search_results[:3],
            }


web_verify = WebVerifyService()
