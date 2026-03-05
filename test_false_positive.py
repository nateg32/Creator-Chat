import sys, os, json, logging, asyncio
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from services.research_provider import OpenAIResearchProvider

async def main():
    rp = OpenAIResearchProvider()
    creator_name = "Jordan Welch"
    query = "any other videos"
    conversation_history = [
        {"role": "user", "content": "yo so i wanna start dropshipping specifically with ads, what video would u reccomend?"},
        {"role": "assistant", "content": "Watch this one: The 'Boring' AI Business Model Making Millionaires In 2025 https://www.youtube.com/watch?v=lhYPNtqvhY"},
    ]
    
    topic_query = rp._extract_topic_from_context(query, creator_name, conversation_history)
    print(f"Topic Query: {topic_query}")
    
    results = rp.search(query=query, creator_profile={"name": "Jordan Welch", "id": 1}, resource_type="video", conversation_history=conversation_history)
    print(f"\n=== FINAL RESULTS ({len(results)}) ===")
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
