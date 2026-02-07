import openai
from typing import List, Dict, Any, Optional
from db import db
from settings import settings

_client = None

def get_client():
    """Lazy initialization of OpenAI client to avoid import-time errors"""
    global _client
    if _client is None:
        _client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client

def get_persona(creator_id: int) -> Optional[str]:
    """Get persona document content for a creator"""
    query = """
        SELECT content 
        FROM documents 
        WHERE creator_id = %s 
        AND metadata->>'type' = 'persona'
        ORDER BY created_at DESC
        LIMIT 1
    """
    result = db.execute_one(query, (creator_id,))
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
    query = """
        SELECT 
            c.id as chunk_id,
            c.chunk_index,
            c.chunk_text,
            (e.embedding <=> %s::vector) as distance
        FROM chunks c
        JOIN embeddings e ON c.id = e.chunk_id
        JOIN documents d ON c.document_id = d.id
        WHERE d.creator_id = %s
        AND (d.metadata->>'type' IS NULL OR d.metadata->>'type' != 'persona')
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
            "content": r["chunk_text"]
        }
        for r in results
    ]

def generate_answer(
    question: str,
    retrieved_chunks: List[Dict[str, Any]],
    persona: Optional[str] = None
) -> str:
    """Generate answer using OpenAI with persona and retrieved context"""
    
    # Build context from retrieved chunks
    context_parts = []
    for chunk in retrieved_chunks:
        context_parts.append(f"[Chunk {chunk['chunk_index']}]: {chunk['content']}")
    
    context = "\n\n".join(context_parts) if context_parts else "No relevant content found."
    
    # Build system prompt with persona
    system_parts = [
        "You are a helpful AI assistant that answers questions based on provided context.",
        "Never mention internal systems like 'embeddings', 'vector database', 'retrieval', or 'training data'.",
        "Answer naturally and conversationally based on the context provided.",
    ]
    
    if persona:
        system_parts.append(f"\nYour persona and guidelines:\n{persona}")
    
    system_prompt = "\n".join(system_parts)
    
    # Build user message
    user_message = f"""Context:
{context}

Question: {question}

Answer the question based on the context above. If the context doesn't contain enough information, ask a short clarifying question instead of guessing."""
    
    try:
        response = get_client().chat.completions.create(
            model=settings.CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
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
    
    # Generate answer
    answer = generate_answer(question, retrieved, persona)

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
