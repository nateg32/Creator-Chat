import re
import json
import openai
from typing import List, Dict, Any
from .db import db
from .settings import settings

_client = None

def get_client():
    """Lazy initialization of OpenAI client to avoid import-time errors"""
    global _client
    if _client is None:
        _client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client

def chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> List[str]:
    """Split text into chunks with smart boundaries"""
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    paragraphs = re.split(r'\n\n+', text)
    current_chunk = ""
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        # If adding this paragraph would exceed chunk_size, finalize current chunk
        if current_chunk and len(current_chunk) + len(para) + 2 > chunk_size:
            chunks.append(current_chunk.strip())
            # Start new chunk with overlap from previous
            overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
            current_chunk = overlap_text + "\n\n" + para
        else:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
        
        # If a single paragraph is too long, split by sentences
        if len(current_chunk) > chunk_size:
            sentences = re.split(r'(?<=[.!?])\s+', current_chunk)
            temp_chunk = ""
            for sent in sentences:
                if len(temp_chunk) + len(sent) + 1 > chunk_size:
                    if temp_chunk:
                        chunks.append(temp_chunk.strip())
                    temp_chunk = sent
                else:
                    temp_chunk += " " + sent if temp_chunk else sent
            current_chunk = temp_chunk
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks


def chunk_text_structured(
    text: str,
    creator_id: int | None = None,
    document_id: int | None = None,
    chunk_size: int = 800,
    overlap: int = 120,
) -> List[Dict[str, Any]]:
    """
    Chunk text and return a list of dicts like:
      {"index": int, "text": str, "creator_id": optional[int], "document_id": optional[int]}

    This keeps the original `chunk_text()` API intact while supporting the approve_ingest pipeline.
    """
    chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    out: List[Dict[str, Any]] = []
    for idx, t in enumerate(chunks):
        out.append(
            {
                "index": idx,
                "text": t,
                "creator_id": creator_id,
                "document_id": document_id,
            }
        )
    return out

def get_embedding(text: str) -> List[float]:
    """Get embedding for text using OpenAI"""
    try:
        response = get_client().embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        raise Exception(f"Failed to get embedding: {str(e)}")


def embed_chunks(chunk_ids: List[int]) -> None:
    """
    Embed existing chunk rows by id and upsert into `embeddings`.

    Supports both legacy schema (`chunks.content`) and newer schema (`chunks.chunk_text`)
    by selecting whichever exists / is non-null.
    """
    if not chunk_ids:
        return

    # Prefer chunk_text (newer), fall back to content (legacy).
    # Note: selecting a non-existent column will raise; we handle that by trying legacy query.
    query_new = """
        SELECT id as chunk_id, chunk_text
        FROM chunks
        WHERE id = ANY(%s)
    """
    query_old = """
        SELECT id as chunk_id, content as chunk_text
        FROM chunks
        WHERE id = ANY(%s)
    """

    try:
        rows = db.execute_query(query_new, (chunk_ids,))
    except Exception:
        rows = db.execute_query(query_old, (chunk_ids,))

    for r in rows:
        text = r.get("chunk_text") or ""
        if not text.strip():
            continue

        embedding = get_embedding(text)
        embedding_str = "[" + ",".join(map(str, embedding)) + "]"
        embedding_query = """
            INSERT INTO embeddings (chunk_id, model, embedding)
            VALUES (%s, %s, %s::vector)
            ON CONFLICT (chunk_id)
            DO UPDATE SET embedding = EXCLUDED.embedding, model = EXCLUDED.model, created_at = NOW()
        """
        db.execute_update(embedding_query, (r["chunk_id"], settings.EMBEDDING_MODEL, embedding_str))

def ingest_document(
    creator_id: int,
    title: str,
    content: str,
    source: str,
    source_id: str,
    doc_type: str
) -> Dict[str, Any]:
    """Ingest a document: chunk, embed, and store in database"""
    
    # Insert document
    doc_query = """
        INSERT INTO documents (creator_id, title, content, source, source_id, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    metadata = json.dumps({"type": doc_type})
    doc_id = db.execute_insert(
        doc_query,
        (creator_id, title, content, source, source_id, metadata)
    )
    
    if not doc_id:
        raise Exception("Failed to insert document")
    
    # Chunk the content
    chunks = chunk_text(content)
    chunk_ids = []
    
    # Process each chunk
    for idx, chunk_text_content in enumerate(chunks):
        # Get embedding
        embedding = get_embedding(chunk_text_content)
        
        # Insert chunk
        chunk_query = """
            INSERT INTO chunks (document_id, chunk_index, content)
            VALUES (%s, %s, %s)
            RETURNING id
        """
        chunk_id = db.execute_insert(
            chunk_query,
            (doc_id, idx, chunk_text_content)
        )
        
        if not chunk_id:
            raise Exception(f"Failed to insert chunk {idx}")
        
        # Insert/upsert embedding
        embedding_query = """
            INSERT INTO embeddings (chunk_id, model, embedding)
            VALUES (%s, %s, %s::vector)
            ON CONFLICT (chunk_id) 
            DO UPDATE SET embedding = EXCLUDED.embedding, model = EXCLUDED.model, created_at = NOW()
        """
        embedding_str = "[" + ",".join(map(str, embedding)) + "]"
        db.execute_update(
            embedding_query,
            (chunk_id, settings.EMBEDDING_MODEL, embedding_str)
        )
        
        chunk_ids.append(chunk_id)
    
    return {
        "document_id": doc_id,
        "chunks_inserted": len(chunks),
        "chunk_ids": chunk_ids
    }
