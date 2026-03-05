
import logging
import json
from typing import Dict, Any, List, Optional
import backend.rag as rag
from backend.settings import settings

logger = logging.getLogger(__name__)

class WebVerifyService:
    """
    Handles personal/factual questions using web search.
    """

    def verify_fact(self, question: str) -> Dict[str, Any]:
        """
        Calls web search and synthesizes a verified answer.
        """
        # 1. Search (Mock or real if SEARCH_API_KEY exists)
        search_results = self._search_web(question)
        
        # 2. Synthesize using GPT-5.2
        prompt = f"""
        You are a Fact Verifier. 
        User Question: {question}
        
        Web Search Results:
        {json.dumps(search_results, indent=2)}
        
        TASK:
        1. Extract the factual answer.
        2. Assign a confidence score (0.0 to 1.0).
        3. List specific sources (titles/URLs).
        
        Output ONLY structured JSON:
        {{
            "answer": "string",
            "confidence": float,
            "sources": [{{"title": "...", "url": "..."}}]
        }}
        """

        try:
            resp = rag.generate_chat_completion(
                messages=[{"role": "system", "content": prompt}],
                model=settings.MODEL_VERIFY,
                temperature=0.0,
                json_mode=True
            )
            return json.loads(resp)
        except Exception as e:
            logger.error(f"Web verify failed: {e}")
            return {
                "answer": "I couldn't verify this information right now.",
                "confidence": 0.0,
                "sources": []
            }

    def _search_web(self, query: str) -> List[Dict[str, Any]]:
        """Placeholder for web search integration."""
        if not settings.SEARCH_API_KEY:
            logger.warning("SEARCH_API_KEY not set. Returning empty results.")
            return []
            
        # Actual search logic would go here (e.g. Brave Search or Serper)
        return [{"title": "General Web Info", "snippet": "Sample search result content.", "url": "https://example.com"}]

web_verify = WebVerifyService()
