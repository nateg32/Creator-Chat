import openai
from typing import List, Dict, Any, Optional
from backend.db import db
from backend.settings import settings
import re
from backend.prompts.creator_base_prompt import CREATOR_BASE_SYSTEM_PROMPT

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
    response = get_client().embeddings.create(
        model=model,
        input=text,
    )
    return response.data[0].embedding


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
    """
    Wrapper for OpenAI chat completion.
    Returns string if stream=False, else returns a generator.
    If json_mode is True and parsing fails, auto-escalates to an advanced model (max 1 retry).
    """
    import json
    import logging
    logger = logging.getLogger(__name__)

    is_reasoning_model = "gpt-5" in model.lower() or "o1" in model.lower() or "o3" in model.lower()
    
    kwargs = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "tools": tools,
        "tool_choice": tool_choice
    }
    
    if is_reasoning_model:
        kwargs["temperature"] = 1.0
        if max_tokens:
            kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["temperature"] = temperature
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
    
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    try:
        response = get_chat_client(model).chat.completions.create(**kwargs)
        if stream:
            return response
            
        content = response.choices[0].message.content.strip()
        
        # Verify JSON validity if required
        if json_mode and allow_fallback and not stream:
            try:
                json.loads(content)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from {model}. Escalating to {settings.MODEL_FALLBACK_SMART}...")
                kwargs["model"] = settings.MODEL_FALLBACK_SMART
                fallback_response = get_chat_client(fallback_model).chat.completions.create(**kwargs)
                return fallback_response.choices[0].message.content.strip()
                
        return content
    except Exception as e:
        logger.error(f"Chat completion failed: {e}")
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
    """Async wrapper for OpenAI chat completion."""
    import json
    import logging
    logger = logging.getLogger(__name__)

    is_reasoning_model = "gpt-5" in model.lower() or "o1" in model.lower() or "o3" in model.lower()
    kwargs = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "tools": tools,
        "tool_choice": tool_choice
    }
    if is_reasoning_model:
        kwargs["temperature"] = 1.0
        if max_tokens: kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["temperature"] = temperature
        if max_tokens: kwargs["max_tokens"] = max_tokens
    
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    try:
        response = await get_async_chat_client(model).chat.completions.create(**kwargs)
        if stream:
            return response
            
        content = response.choices[0].message.content.strip()
        
        if json_mode and allow_fallback and not stream:
            try:
                json.loads(content)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from {model}. Escalating to {settings.MODEL_FALLBACK_SMART}...")
                kwargs["model"] = settings.MODEL_FALLBACK_SMART
                fallback_response = await get_async_chat_client(fallback_model).chat.completions.create(**kwargs)
                return fallback_response.choices[0].message.content.strip()
                
        return content
    except Exception as e:
        logger.error(f"Async chat completion failed: {e}")
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
