"""
Creator-Centric RAG + global Creator Bot system prompt.

- FINAL_SYSTEM_PROMPT = CREATOR_BASE_SYSTEM_PROMPT with {{CREATOR_PERSONA_TEXT_HERE}}
  and {{OPTIONAL_PRODUCT_RULES_HERE}} replaced at runtime.
- Injects system prompt, then last N conversation turns, then <retrieved_sources>, then current user message.
"""

from __future__ import annotations

import re
from typing import List, Dict, Any, Optional

import backend.rag as rag
from backend.db import db
from backend.settings import settings
from backend.prompts.creator_base_prompt import CREATOR_BASE_SYSTEM_PROMPT

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

MAX_HISTORY_MESSAGES = 20
PLACEHOLDER_PERSONA = "{{CREATOR_PERSONA_TEXT_HERE}}"
PLACEHOLDER_PRODUCT_RULES = "{{OPTIONAL_PRODUCT_RULES_HERE}}"


def _client() -> "OpenAI":
    if OpenAI is None:
        raise RuntimeError("openai package is required for creator_engine")
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def get_creator_profile(creator_id: int) -> Dict[str, Any]:
    """Load creator handle and display name."""
    try:
        row = db.execute_one(
            "SELECT handle, display_name FROM creators WHERE id = %s LIMIT 1",
            (creator_id,),
        )
    except Exception:
        try:
            row = db.execute_one(
                "SELECT handle, name AS display_name FROM creators WHERE id = %s LIMIT 1",
                (creator_id,),
            )
        except Exception:
            row = None
    if not row:
        return {"handle": None, "display_name": None}
    return {
        "handle": row.get("handle"),
        "display_name": row.get("display_name") or row.get("handle") or "the creator",
    }


def _parse_persona_temp(persona: Optional[str]) -> float:
    if not persona:
        return 0.7
    m = re.search(r"(?:temperature|temp)\s*:\s*([0-9.]+)", persona, re.I)
    if m:
        t = float(m.group(1))
        return max(0.0, min(1.0, t))
    return 0.7


def _voodoo_temp(question: str, base_temp: float) -> float:
    q = question.lower()
    factual = any(
        w in q
        for w in ("when is", "when's", "what time", "how much", "which date", "where is")
    )
    return min(base_temp, 0.2) if factual else base_temp


def _build_product_rules(has_persona: bool) -> str:
    base = "Keep responses short to medium. Be actionable. Do not mention embeddings, retrieval, or training data."
    if not has_persona:
        base += " If no persona is loaded for this creator, refuse to answer and tell the user to set a persona first."
    return base


def _build_final_system_prompt(persona: Optional[str], product_rules: str) -> str:
    persona_text = (persona or "").strip()
    return (
        CREATOR_BASE_SYSTEM_PROMPT.replace(PLACEHOLDER_PERSONA, persona_text)
        .replace(PLACEHOLDER_PRODUCT_RULES, product_rules)
    )


def _trim_history(messages: List[Dict[str, Any]], max_messages: int = MAX_HISTORY_MESSAGES) -> List[Dict[str, str]]:
    """Normalize to {role, content} and keep last max_messages."""
    out: List[Dict[str, str]] = []
    for m in messages:
        role = (m.get("role") or "user").lower()
        if role not in ("user", "assistant"):
            if role == "system":
                continue
            role = "user"
        content = m.get("content") or m.get("text") or ""
        if not isinstance(content, str) or not content.strip():
            continue
        out.append({"role": role, "content": content.strip()})
    return out[-max_messages:]


def get_vector_memory(
    creator_id: int,
    query: str,
    top_k: int = 5,
    max_distance: float = 1.15,
) -> tuple[str, List[Dict[str, Any]]]:
    """RAG retrieval over creator content. Returns (sources_xml, retrieved)."""
    try:
        emb = _client().embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=query,
        )
        query_embedding = emb.data[0].embedding
    except Exception as e:
        raise RuntimeError(f"Failed to get query embedding: {e}") from e

    retrieved = rag.retrieve_chunks(
        creator_id, query_embedding, top_k=top_k, max_distance=max_distance
    )
    parts = []
    for c in retrieved:
        parts.append(f"[{c.get('chunk_index', 0)}] {c.get('content', '')}")
    xml = "\n\n".join(parts) if parts else "No relevant creator content found."
    return f"<retrieved_sources>\n{xml}\n</retrieved_sources>", retrieved


def _generate_response(
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
) -> str:
    resp = _client().chat.completions.create(
        model=settings.CHAT_MODEL,
        messages=messages,
        temperature=temperature,
    )
    return (resp.choices[0].message.content or "").strip()


def ask(
    creator_id: int,
    question: str,
    top_k: int = 5,
    max_distance: float = 1.15,
    messages: Optional[List[Dict[str, Any]]] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    Build FINAL_SYSTEM_PROMPT, inject conversation history + <retrieved_sources> + current user message,
    then generate. Returns {answer, retrieved, debug_info?}.
    """
    persona = rag.get_persona(creator_id)
    product_rules = _build_product_rules(bool(persona and persona.strip()))
    final_system = _build_final_system_prompt(persona, product_rules)

    base_temp = _parse_persona_temp(persona)
    temp = _voodoo_temp(question, base_temp)

    sources_block, retrieved = get_vector_memory(
        creator_id, question, top_k=top_k, max_distance=max_distance
    )

    # 1) System
    out: List[Dict[str, str]] = [{"role": "system", "content": final_system}]

    # 2) Last N conversation turns
    history = _trim_history(messages or [])
    for m in history:
        out.append({"role": m["role"], "content": m["content"]})

    # 3) Retrieved sources as dedicated block (user message)
    out.append({"role": "user", "content": sources_block})

    # 4) Current user message
    out.append({"role": "user", "content": question.strip()})

    answer = _generate_response(out, temperature=temp)

    result: Dict[str, Any] = {
        "answer": answer,
        "retrieved": [
            {
                "chunk_id": r["chunk_id"],
                "chunk_index": r["chunk_index"],
                "distance": round(r["distance"], 3),
                "preview": (r.get("content") or "")[:200] or None,
            }
            for r in retrieved
        ],
    }

    if debug:
        result["debug_info"] = {
            "persona_preview": (persona or "")[:200] if persona else None,
            "persona_loaded": bool(persona and persona.strip()),
            "sources_count": len(retrieved),
            "history_messages_injected": len(history),
            "product_rules_preview": product_rules[:150],
        }

    return result
