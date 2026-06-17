import openai
from typing import List, Dict, Any, Optional
from collections import OrderedDict
from threading import Lock
from backend.db import db
from backend.settings import settings
import re
from backend.prompts.creator_base_prompt import CREATOR_BASE_SYSTEM_PROMPT
from backend.services.llm_provider import get_gemini_provider

# In-process LRU cache for query embeddings. Keyed by (normalized_text, model).
# Bounded so it cannot grow unbounded under load.
_QUERY_EMBEDDING_CACHE: "OrderedDict[tuple, List[float]]" = OrderedDict()
_QUERY_EMBEDDING_CACHE_LOCK = Lock()
_QUERY_EMBEDDING_CACHE_MAX = 512


def _normalize_embedding_key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _cache_get_embedding(text: str, model: str) -> Optional[List[float]]:
    key = (_normalize_embedding_key(text), model)
    if not key[0]:
        return None
    with _QUERY_EMBEDDING_CACHE_LOCK:
        vec = _QUERY_EMBEDDING_CACHE.get(key)
        if vec is not None:
            _QUERY_EMBEDDING_CACHE.move_to_end(key)
        return vec


def _cache_put_embedding(text: str, model: str, vector: List[float]) -> None:
    key = (_normalize_embedding_key(text), model)
    if not key[0] or not vector:
        return
    with _QUERY_EMBEDDING_CACHE_LOCK:
        _QUERY_EMBEDDING_CACHE[key] = vector
        _QUERY_EMBEDDING_CACHE.move_to_end(key)
        while len(_QUERY_EMBEDDING_CACHE) > _QUERY_EMBEDDING_CACHE_MAX:
            _QUERY_EMBEDDING_CACHE.popitem(last=False)


def embed_query_cached(text: str, model: str = settings.EMBEDDING_MODEL) -> List[float]:
    """Sync query-embedding helper backed by an LRU cache."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    cached = _cache_get_embedding(text, model)
    if cached is not None:
        return cached
    response = get_client().embeddings.create(model=model, input=text)
    vec = response.data[0].embedding
    _cache_put_embedding(text, model, vec)
    return vec


async def embed_query_cached_async(text: str, model: str = settings.EMBEDDING_MODEL) -> List[float]:
    """Async query-embedding helper backed by the same LRU cache."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    cached = _cache_get_embedding(text, model)
    if cached is not None:
        return cached
    response = await get_async_client().embeddings.create(model=model, input=text)
    vec = response.data[0].embedding
    _cache_put_embedding(text, model, vec)
    return vec

_client = None
_async_client = None
_chat_client = None
_async_chat_client = None
_openai_chat_client = None
_openai_async_chat_client = None


def _client_kwargs(*, api_key: str, base_url: str = "") -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return kwargs


def _is_xai_model(model: Optional[str]) -> bool:
    return str(model or "").lower().startswith("grok")


def _gemini_chat_model(model: Optional[str]) -> str:
    return model if str(model or "").startswith("gemini-") else settings.GEMINI_CHAT_MODEL

def get_client():
    """Lazy initialization of embedding/default OpenAI client to avoid import-time errors"""
    global _client
    if _client is None:
        _client = openai.OpenAI(
            **_client_kwargs(
                api_key=settings.EMBEDDING_API_KEY or settings.OPENAI_API_KEY,
                base_url=settings.EMBEDDING_BASE_URL,
            )
        )
    return _client

def get_async_client():
    """Lazy initialization of async embedding/default OpenAI client."""
    global _async_client
    if _async_client is None:
        _async_client = openai.AsyncOpenAI(
            **_client_kwargs(
                api_key=settings.EMBEDDING_API_KEY or settings.OPENAI_API_KEY,
                base_url=settings.EMBEDDING_BASE_URL,
            )
        )
    return _async_client


def get_chat_client(model: Optional[str] = None):
    """Return a chat/completions client appropriate for the requested model family."""
    global _chat_client, _openai_chat_client
    if _is_xai_model(model):
        if _chat_client is None:
            _chat_client = openai.OpenAI(
                **_client_kwargs(
                    api_key=settings.XAI_API_KEY,
                    base_url=settings.XAI_BASE_URL,
                )
            )
        return _chat_client
    if _openai_chat_client is None:
        _openai_chat_client = openai.OpenAI(
            **_client_kwargs(
                api_key=settings.OPENAI_API_KEY or settings.EMBEDDING_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
            )
        )
    return _openai_chat_client


def get_async_chat_client(model: Optional[str] = None):
    """Return an async chat/completions client appropriate for the requested model family."""
    global _async_chat_client, _openai_async_chat_client
    if _is_xai_model(model):
        if _async_chat_client is None:
            _async_chat_client = openai.AsyncOpenAI(
                **_client_kwargs(
                    api_key=settings.XAI_API_KEY,
                    base_url=settings.XAI_BASE_URL,
                )
            )
        return _async_chat_client
    if _openai_async_chat_client is None:
        _openai_async_chat_client = openai.AsyncOpenAI(
            **_client_kwargs(
                api_key=settings.OPENAI_API_KEY or settings.EMBEDDING_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
            )
        )
    return _openai_async_chat_client


def create_embedding(text: str, model: str = settings.EMBEDDING_MODEL) -> List[float]:
    """Backward-compatible embedding helper used across older services."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    return embed_query_cached(text, model)


def generate_chat_completion(
    messages: List[Dict[str, str]],
    model: str = settings.CHAT_MODEL,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    json_mode: bool = False,
    stream: bool = False,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    allow_fallback: bool = True
) -> Any:
    """Gemini-only chat completion wrapper for creator replies and chat helpers."""
    import json
    import logging
    logger = logging.getLogger(__name__)

    if tools or tool_choice:
        logger.warning("Ignoring OpenAI-compatible tools in Gemini-only chat wrapper.")

    target_model = _gemini_chat_model(model)
    try:
        result = get_gemini_provider().generate_text(
            messages=messages,
            model=target_model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            stream=stream,
        )
        if stream or not json_mode:
            return result
        try:
            json.loads(result)
            return result
        except json.JSONDecodeError:
            if not allow_fallback:
                raise
            logger.warning("Invalid JSON from %s. Retrying with %s...", target_model, settings.MODEL_FALLBACK_SMART)
            repair_messages = list(messages) + [
                {"role": "user", "content": "Return the previous answer as valid JSON only. No prose, no markdown."}
            ]
            return get_gemini_provider().generate_text(
                messages=repair_messages,
                model=_gemini_chat_model(settings.MODEL_FALLBACK_SMART),
                temperature=min(temperature, 0.2),
                max_tokens=max_tokens,
                json_mode=True,
                stream=False,
            )
    except Exception as e:
        logger.error(f"Gemini chat completion failed: {e}")
        raise e

async def generate_chat_completion_async(
    messages: List[Dict[str, str]],
    model: str = settings.CHAT_MODEL,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    json_mode: bool = False,
    stream: bool = False,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    allow_fallback: bool = True
) -> Any:
    """Async Gemini-only chat completion wrapper."""
    import json
    import logging
    logger = logging.getLogger(__name__)

    if tools or tool_choice:
        logger.warning("Ignoring OpenAI-compatible tools in Gemini-only async chat wrapper.")

    target_model = _gemini_chat_model(model)
    try:
        result = await get_gemini_provider().generate_text_async(
            messages=messages,
            model=target_model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            stream=stream,
        )
        if stream or not json_mode:
            return result
        try:
            json.loads(result)
            return result
        except json.JSONDecodeError:
            if not allow_fallback:
                raise
            logger.warning("Invalid JSON from %s. Retrying with %s...", target_model, settings.MODEL_FALLBACK_SMART)
            repair_messages = list(messages) + [
                {"role": "user", "content": "Return the previous answer as valid JSON only. No prose, no markdown."}
            ]
            return await get_gemini_provider().generate_text_async(
                messages=repair_messages,
                model=_gemini_chat_model(settings.MODEL_FALLBACK_SMART),
                temperature=min(temperature, 0.2),
                max_tokens=max_tokens,
                json_mode=True,
                stream=False,
            )
    except Exception as e:
        logger.error(f"Async Gemini chat completion failed: {e}")
        raise e

def get_persona(creator_id: int) -> Optional[str]:
    """Get persona document content for a creator"""
    # use source column instead of metadata
    query = """
        SELECT content 
        FROM documents 
        WHERE creator_id = %s 
        AND source = 'persona'
        ORDER BY id DESC
        LIMIT 1
    """
    try:
        result = db.execute_one(query, (creator_id,))
    except Exception:
        # Fallback if source column missing (unlikely given validation)
        return None
        
    return result["content"] if result else None

def retrieve_chunks(
    creator_id: int,
    query_embedding: List[float],
    top_k: int = 10,
    max_distance: float = 1.15
) -> List[Dict[str, Any]]:
    """Retrieve relevant chunks using vector similarity"""
    import logging
    logger = logging.getLogger(__name__)
    
    embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"
    
    # Fixed query: filter by actual cosine distance, not similarity
    # Use source != 'persona' instead of metadata check
    query = """
        SELECT 
            c.id as chunk_id,
            c.chunk_index,
            c.chunk_text,
            d.title as doc_title,
            COALESCE(NULLIF(d.url, ''), d.metadata->>'canonical_url', d.metadata->>'source_url') as source_url,
            (e.embedding <=> %s::vector) as distance
        FROM chunks c
        JOIN embeddings e ON c.id = e.chunk_id
        JOIN documents d ON c.document_id = d.id
        WHERE d.creator_id = %s
        AND d.source != 'persona'
        AND e.model = %s
        AND (e.embedding <=> %s::vector) <= %s
        ORDER BY e.embedding <=> %s::vector
        LIMIT %s
    """
    
    logger.info(f"Retrieving chunks for creator_id={creator_id}, top_k={top_k}, max_distance={max_distance}, model={settings.EMBEDDING_MODEL}")
    
    results = db.execute_query(
        query,
        (embedding_str, creator_id, settings.EMBEDDING_MODEL, embedding_str, max_distance, embedding_str, top_k)
    )
    
    logger.info(f"Retrieved {len(results)} chunks from database")
    if results:
        logger.info(f"First chunk: id={results[0].get('chunk_id')}, distance={results[0].get('distance')}")
    
    return [
        {
            "chunk_id": r["chunk_id"],
            "chunk_index": r["chunk_index"],
            "distance": float(r["distance"]),
            "content": r["chunk_text"],
            "url": r["source_url"],
            "title": r.get("doc_title")
        }
        for r in results
    ]

def generate_answer(
    question: str,
    retrieved_chunks: List[Dict[str, Any]],
    persona: Optional[str] = None,
    creator_name: str = "Creator"
) -> str:
    """Generate answer using OpenAI with persona and retrieved context"""
    
    # Build context from retrieved chunks
    context_str = "\n\n".join([f"<source_chunk id='{c['chunk_id']}'>\n{c['content']}\n</source_chunk>" for c in retrieved_chunks])
    
    # Build system prompt from template
    system_prompt = CREATOR_BASE_SYSTEM_PROMPT.replace("{{CREATOR_NAME}}", creator_name)
    system_prompt = system_prompt.replace("{{USER_NAME}}", "User")
    system_prompt = system_prompt.replace("{{CREATOR_PERSONA_TEXT_HERE}}", persona or "No specific persona loaded.")
    system_prompt = system_prompt.replace("{{OPTIONAL_PRODUCT_RULES_HERE}}", "")
    system_prompt = system_prompt.replace("{{USER_PERSONALIZATION_HERE}}", "")
    
    # Build user message
    user_message = f"""<retrieved_sources>
{context_str}
</retrieved_sources>

User Question: {question}
"""
    
    try:
        answer = generate_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            model=settings.CHAT_MODEL,
            temperature=0.7
        )
        
        # Strip style analysis tags
        if "<style_analysis>" in answer:
            answer = re.sub(r"<style_analysis>.*?</style_analysis>", "", answer, flags=re.DOTALL).strip()
            
        return answer
    except Exception as e:
        raise Exception(f"Failed to generate answer: {str(e)}")

def ask_question(
    creator_id: int,
    question: str,
    top_k: int = 5,
    max_distance: float = 1.15
) -> Dict[str, Any]:
    """Main RAG function: get persona, retrieve chunks, generate answer"""
    
    # Get persona
    persona = get_persona(creator_id)
    
    # Get query embedding
    try:
        embedding_response = get_client().embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=question
        )
        query_embedding = embedding_response.data[0].embedding
    except Exception as e:
        raise Exception(f"Failed to get query embedding: {str(e)}")
    
    # Retrieve chunks
    retrieved = retrieve_chunks(creator_id, query_embedding, top_k, max_distance)
    
    # Fetch creator name
    creator_row = db.execute_one("SELECT name FROM creators WHERE id = %s", (creator_id,))
    creator_name = creator_row["name"] if creator_row else "Creator"

    # Generate answer
    answer = generate_answer(question, retrieved, persona, creator_name)

    # If no persona is loaded, warn that answers may be generic
    if not persona:
        answer = (
            "No persona loaded for this creator yet — answers may sound more generic.\n\n"
            + answer
        )
    
    return {
        "answer": answer,
        "retrieved": [
            {
                "chunk_id": r["chunk_id"],
                "chunk_index": r["chunk_index"],
                "distance": round(r["distance"], 3),
                "preview": r.get("content", "")[:200] if r.get("content") else None,
            }
            for r in retrieved
        ],
    }
