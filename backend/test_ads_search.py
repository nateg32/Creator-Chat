import asyncio
import os
import json
import logging
from dotenv import load_dotenv
from backend.services.research_provider import OpenAIResearchProvider

logging.basicConfig(level=logging.INFO)
load_dotenv()

async def main():
    rp = OpenAIResearchProvider()
    creator_name = "Jordan Welch"
    query = "what was your first ever upload"
    conversation_history = [
        {"role": "user", "content": "what was your first video you ever made"},
        {"role": "assistant", "content": "You mean my first ever upload on my channel, or the first video I ever filmed before I had a channel?"},
    ]
    
    topic_query = rp._extract_topic_from_context(query, creator_name, conversation_history)
    print(f"Topic Query: {topic_query}")
    
    search_prompt = (
        f'Search the web for videos by {creator_name} about "{topic_query}".\n\n'
        f'Search query to use: {creator_name} "{topic_query}"\n\n'
        f'CRITICAL INSTRUCTIONS:\n'
        f'- Find videos where the TITLE specifically mentions: {topic_query}\n'
        f'- For example, if searching for "ads", find videos titled things like "Facebook Ads Tutorial", "I Spent $1M On Ads", "How To Run Ads", NOT "How To Start A Business" or "Beginner Guide"\n'
        f'- If the topic is "facebook ads", search: {creator_name} "facebook ads" OR "FB ads"\n'
        f'- If the topic is "dropshipping", search: {creator_name} "dropshipping" OR "shopify"\n'
        f'- ONLY return videos that are DIRECTLY about the topic. Reject anything generic.\n'
        f'- If you cannot find topic-specific videos, return an empty array [] — do NOT substitute unrelated videos.\n\n'
        f'Output ONLY a JSON array:\n'
        f'[\n  {{\n    "title": "Exact video title from search result",\n    "url": "Direct video URL",\n    "snippet": "Why this video is about {topic_query}",\n    "resource_type": "video",\n    "relation": "SELF",\n    "confidence": 0.8\n  }}\n]'
    )
    
    import openai
    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    
    print("Calling OpenAI Chat Completions...")
    response = client.chat.completions.create(
        model="gpt-4o-search-preview",
        messages=[{"role": "user", "content": search_prompt}]
    )
    
    raw_text = response.choices[0].message.content
    print("\n--- RAW CHAT COMPLETIONS TEXT ---")
    print(raw_text)
    print("---------------------------------\n")
    
    try:
        results = rp._extract_results_from_text(raw_text, creator_name)
    except Exception as e:
        print(f"Extraction failed: {type(e).__name__} - {e}")
        results = []
    
    print("\nFINAL RESULTS:")
    for r in results:
        print(f"- {r.get('title', 'Unknown')} | {r.get('url', 'No URL')}")

if __name__ == "__main__":
    asyncio.run(main())
