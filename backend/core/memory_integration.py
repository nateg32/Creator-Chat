import os
import logging
import json
from typing import List, Dict, Any, Optional
from backend.settings import settings
from backend.core.simple_vector_store import SimpleJSONVectorStore
# We use OpenAI client directly for embeddings
from openai import OpenAI

logger = logging.getLogger(__name__)

class MemoryIntegration:
    def __init__(self):
        self.user_id = "default_user"
        
        # Initialize Vector Store
        self.store_path = str(settings.BASE_DIR / "mem0_simple_store.json")
        self.vector_store = SimpleJSONVectorStore(self.store_path)
        
        # Initialize OpenAI Clients
        from backend.rag import get_client, get_async_client
        self.client = get_client()
        self.async_client = get_async_client()
        self.embedding_model = "text-embedding-3-small"
        
        logger.info(f"Initialized Lightweight MemoryIntegration at {self.store_path}")

    async def _get_embedding_async(self, text: str) -> List[float]:
        try:
            text = text.replace("\n", " ")
            resp = await self.async_client.embeddings.create(
                input=[text], model=self.embedding_model
            )
            return resp.data[0].embedding
        except Exception as e:
            logger.error(f"Async embedding error: {e}")
            return []

    def _get_embedding(self, text: str) -> List[float]:
        try:
            text = text.replace("\n", " ")
            return self.client.embeddings.create(
                input=[text], model=self.embedding_model
            ).data[0].embedding
        except Exception as e:
            logger.error(f"Embedding error: {e}")
            return []

    def add_user_message(self, creator_id: str, user_id: str, thread_id: str, message: str):
        """
        Extract facts from user message and store them.
        For simplicity in this robust version, we store the raw message if it looks useful,
        or we could ask LLM to extract facts. 
        Time constraint: Store raw message as 'fact' for now, or simple extraction.
        Let's do simple valid check.
        """
        if not message or len(message) < 5:
            return

        # Optional: Use LLM to extract facts (Enhancement)
        # For now, store the message itself as a memory fragment.
        # This is "Episodic" memory.
        
        try:
            vector = self._get_embedding(message)
            if not vector: return

            # Generate ID (simple hash or uuid)
            import uuid
            mem_id = str(uuid.uuid4())
            
            payload = {
                "data": message,
                "user_id": user_id,
                "creator_id": creator_id,
                "thread_id": thread_id,
                "role": "user",
                "timestamp": getattr(settings, "NOW", ""), # dynamic timestamp if possible
            }
            
            self.vector_store.insert(
                vectors=[vector],
                payloads=[payload],
                ids=[mem_id]
            )
            logger.info(f"Stored memory: {message[:50]}...")
            
        except Exception as e:
            logger.error(f"Add memory error: {e}")

    def add_bot_message(self, user_id: str, message: str):
        pass

    def search(self, creator_id: str, user_id: str, thread_id: str, query: str, limit: int = 3) -> List[str]:
        """Search for relevant memories."""
        try:
            vector = self._get_embedding(query)
            if not vector: return []
            
            # Strict hierarchical isolation: filter by creator, user, and thread
            filters = {
                "user_id": user_id,
                "creator_id": creator_id,
                "thread_id": thread_id
            }
            
            results = self.vector_store.search(
                query=query, 
                vectors=[vector], 
                limit=limit,
                filters=filters
            )
            
            return [r.payload.get("data", "") for r in results]
        except Exception as e:
            logger.error(f"Memory search error: {e}")
            return []

    async def search_async(self, creator_id: str, user_id: str, thread_id: str, query: str, limit: int = 3) -> List[str]:
        """Async search for relevant memories."""
        try:
            vector = await self._get_embedding_async(query)
            if not vector: return []
            
            filters = {
                "user_id": user_id,
                "creator_id": creator_id,
                "thread_id": thread_id
            }
            
            # SimpleJSONVectorStore is sync (file-based), but the network-heavy embedding call is now async.
            results = self.vector_store.search(
                query=query, 
                vectors=[vector], 
                limit=limit,
                filters=filters
            )
            
            return [r.payload.get("data", "") for r in results]
        except Exception as e:
            logger.error(f"Async memory search error: {e}")
            return []

    async def search_with_embedding_async(self, creator_id: str, user_id: str, thread_id: str, embedding: List[float], limit: int = 3) -> List[str]:
        """Search for relevant memories with a pre-computed embedding."""
        try:
            filters = {
                "user_id": user_id,
                "creator_id": creator_id,
                "thread_id": thread_id
            }
            results = self.vector_store.search(
                query="", 
                vectors=[embedding], 
                limit=limit,
                filters=filters
            )
            return [r.payload.get("data", "") for r in results]
        except Exception as e:
            logger.error(f"Async memory search (with emb) error: {e}")
            return []
