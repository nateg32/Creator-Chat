import sys
import os
import json
import asyncio
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from services.research_provider import GeminiResearchProvider
from db import db

def test_research():
    provider = GeminiResearchProvider()
    if not provider.enabled:
        print("GeminiResearchProvider NOT enabled. Check API key.")
        return

    # Mock creator profile
    creator = {
        'id': 1,
        'name': 'Tradesbysci',
        'youtube_handle': 'Tradesbysci',
        'official_domains': ['tradesbysci.com'],
        'course_base_urls': []
    }
    
    query = "market structure for beginners"
    print(f"Testing Gemini Research for: {query}")
    results = provider.search(query, creator)
    
    print("\n--- RESULTS ---")
    print(json.dumps(results, indent=2))
    
    if results:
        print("\nSuccess! Results found and verified.")
    else:
        print("\nNo results found or all rejected by COG.")

if __name__ == "__main__":
    test_research()
