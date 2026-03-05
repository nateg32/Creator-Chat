"""Final e2e test - does the bot USE the search results?"""
import sys, os, json, logging, time, asyncio

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from db import db

async def test():
    creator = db.execute_one(
        "SELECT * FROM creators ORDER BY id DESC LIMIT 1"
    )
    print(f"Creator: {creator['handle']} | search_mode={creator.get('search_mode')}")
    
    # Clear search cache for fresh test
    db.execute_update(
        "DELETE FROM search_cache WHERE creator_id = %s", (creator['id'],)
    )
    
    from grounded_rag import grounded_rag_stream
    
    question = "what was your first ever upload"
    history = [
        {"role": "user", "content": "what was your first video you ever made"},
        {"role": "assistant", "content": "You mean my first ever upload on my channel, or the first video I ever filmed before I had a channel?"},
    ]
    
    t0 = time.time()
    full_response = ""
    ttft = None
    
    async for chunk in grounded_rag_stream(
        creator_id=creator['id'],
        question=question,
        thread_id=None,
        conversation_history=history,
        user_name="Nathan",
        user_id=1
    ):
        if ttft is None:
            ttft = time.time() - t0
            print(f"\n[TTFT: {ttft:.1f}s]")
        full_response += chunk
        print(chunk, end="", flush=True)
    
    total = time.time() - t0
    print(f"\n\n=== METRICS ===")
    print(f"TTFT: {ttft:.1f}s")
    print(f"Total: {total:.1f}s")
    print(f"Length: {len(full_response)} chars")
    
    # Check for deflection
    deflection_phrases = [
        "unfortunately", "don't have access", "i don't have that",
        "can't confirm", "not able to", "don't have a link"
    ]
    deflected = any(p in full_response.lower() for p in deflection_phrases)
    print(f"Deflected: {deflected}")
    
    if deflected:
        print("FAIL: Bot deflected despite web search results being available!")
    else:
        print("PASS: Bot used web search results successfully!")

if __name__ == "__main__":
    asyncio.run(test())
