"""
Search Engine Wrapper
Handles interaction with the configured search provider.
"""
from typing import List, Dict, Any, Optional
from backend.settings import settings
import logging

class SearchEngine:
    def __init__(self):
        # We prefer SerpApi for consistent structured results
        try:
            from serpapi import GoogleSearch
            self.GoogleSearch = GoogleSearch
            self.api_key = settings.SEARCH_API_KEY
            if not self.api_key:
                logging.warning("SEARCH_API_KEY not found in environment")
        except ImportError:
            self.GoogleSearch = None
            logging.warning("serpapi library not installed")

    def search(self, query: str, num_results: int = 5) -> List[Dict[str, Any]]:
        """
        Execute a search query and return normalized results.
        Returns list of {"title": str, "link": str, "snippet": str, "source": str}
        """
        if not self.GoogleSearch or not self.api_key:
            return []

        try:
            params = {
                "q": query,
                "api_key": self.api_key,
                "num": num_results,
                "engine": "google" 
            }
            search = self.GoogleSearch(params)
            results = search.get_dict()
            
            organic = results.get("organic_results", [])
            output = []
            
            for item in organic:
                output.append({
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                    "source": item.get("source", "Web"),
                    "metadata": item.get("rich_snippet", {}) or item.get("video_result", {}) or item
                })
                
            return output
        except Exception as e:
            logging.error(f"Search engine error: {e}")
            return []
