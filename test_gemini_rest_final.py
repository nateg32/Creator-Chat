import sys
import os
import json
import logging
sys.path.append(os.path.join(os.getcwd(), 'backend'))

# Setup logging to see what's happening
logging.basicConfig(level=logging.INFO)

from services.research_provider import GeminiResearchProvider

def test_research():
    provider = GeminiResearchProvider()
    if not provider.enabled:
        print("GeminiResearchProvider NOT enabled. Check API key.")
        return

    # Mock creator profile (Tradesbysci)
    creator = {
        'id': 1,
        'name': 'Tradesbysci',
        'youtube_handle': 'Tradesbysci',
        'official_domains': ['tradesbysci.com'],
        'course_base_urls': []
    }
    
    query = "where do i start for beginner trading"
    print(f"Testing Gemini Research (REST) for: {query}")
    results = provider.search(query, creator)
    
    print("\n--- RESULTS ---")
    print(json.dumps(results, indent=2))
    
    if results:
        print("\nSuccess! Results found and verified.")
    else:
        print("\nNo results found or all rejected by COG.")

if __name__ == "__main__":
    test_research()
