import re
import json
import openai
from typing import List, Dict, Any
from backend.db import db
from backend.settings import settings

_client = None


def clean_transcript_for_ingestion(transcript: str) -> str:
    """
    Strip transcript artifacts before chunking and embedding.

    This keeps timestamps, stage directions, and caption-noise markers out of
    the vector store so retrieved chunks sound more like content and less like
    a subtitle file.
    """
    if not transcript:
        return ""

    cleaned = str(transcript or "")
    cleaned = re.sub(r"\b\d{1,2}:\d{2}\b", " ", cleaned)
    cleaned = re.sub(r"\[(?:[^\]\n]{1,40})\]", " ", cleaned)
    cleaned = re.sub(r"\((?:[^\)\n]{1,40})\)", " ", cleaned)
    cleaned = re.sub(r"(?im)^\s*\d+\s*:\s*", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()

def get_client():
    """Lazy initialization of OpenAI client to avoid import-time errors"""
    global _client
    if _client is None:
        kwargs = {"api_key": settings.EMBEDDING_API_KEY or settings.OPENAI_API_KEY}
        if settings.EMBEDDING_BASE_URL:
            kwargs["base_url"] = settings.EMBEDDING_BASE_URL
        _client = openai.OpenAI(**kwargs)
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


def get_embeddings_batch(texts: List[str]) -> List[List[float]]:
    """Get embeddings for multiple texts in a single API call (much faster!)"""
    if not texts:
        return []
    
    try:
        # OpenAI allows up to 2048 texts per batch request
        # Process in batches to stay within limits
        BATCH_SIZE = 2048
        all_embeddings = []
        
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i:i + BATCH_SIZE]
            response = get_client().embeddings.create(
                model=settings.EMBEDDING_MODEL,
                input=batch
            )
            # Response data is sorted by index, so this preserves order
            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)
        
        return all_embeddings
    except Exception as e:
        raise Exception(f"Failed to get batch embeddings: {str(e)}")


def embed_chunks(chunk_ids: List[int], progress_callback=None) -> None:
    """
    Embed existing chunk rows by id and upsert into `embeddings`.
    NOW OPTIMIZED: Uses batch embedding API for 10-100x faster performance!

    Supports both legacy schema (`chunks.content`) and newer schema (`chunks.chunk_text`)
    by selecting whichever exists / is non-null.
    
    Args:
        chunk_ids: List of chunk IDs to embed
        progress_callback: Optional callback function(current, total, stage) for progress updates
    """
    if not chunk_ids:
        return

    # Prefer chunk_text (newer), fall back to content (legacy).
    query_new = """
        SELECT id as chunk_id, chunk_text
        FROM chunks
        WHERE id = ANY(%s)
        ORDER BY id
    """
    query_old = """
        SELECT id as chunk_id, content as chunk_text
        FROM chunks
        WHERE id = ANY(%s)
        ORDER BY id
    """

    try:
        rows = db.execute_query(query_new, (chunk_ids,))
    except Exception:
        rows = db.execute_query(query_old, (chunk_ids,))

    if not rows:
        return
    
    # Prepare data for batch embedding
    valid_rows = []
    texts = []
    for r in rows:
        text = r.get("chunk_text") or ""
        if text.strip():
            valid_rows.append(r)
            texts.append(text)
    
    if not texts:
        return
    
    total = len(texts)
    
    # Get all embeddings in batch (MUCH faster than one-by-one!)
    if progress_callback:
        progress_callback(0, total, "embedding")
    
    embeddings = get_embeddings_batch(texts)
    
    if progress_callback:
        progress_callback(total, total, "embedding")
    
    # Now insert all embeddings into database
    if progress_callback:
        progress_callback(0, total, "storing")
    
    for idx, (r, embedding) in enumerate(zip(valid_rows, embeddings)):
        embedding_str = "[" + ",".join(map(str, embedding)) + "]"
        embedding_query = """
            INSERT INTO embeddings (chunk_id, model, embedding)
            VALUES (%s, %s, %s::vector)
            ON CONFLICT (chunk_id)
            DO UPDATE SET embedding = EXCLUDED.embedding, model = EXCLUDED.model, created_at = NOW()
        """
        db.execute_update(embedding_query, (r["chunk_id"], settings.EMBEDDING_MODEL, embedding_str))
        
        if progress_callback and (idx + 1) % 10 == 0:  # Update every 10 items
            progress_callback(idx + 1, total, "storing")

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
            INSERT INTO chunks (creator_id, document_id, chunk_index, chunk_text)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """
        chunk_id = db.execute_insert(
            chunk_query,
            (creator_id, doc_id, idx, chunk_text_content)
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
