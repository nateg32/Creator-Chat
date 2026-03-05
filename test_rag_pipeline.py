import sys, os, json, asyncio
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from db import db
from grounded_rag import grounded_rag_stream

async def main():
    creator = db.execute_one("SELECT id FROM creators ORDER BY id DESC LIMIT 1")
    creator_id = creator['id']
    
    question = "i wanna start dropshipping specifically with ads, what video would u reccomend?"
    print(f"Testing grounded_rag_stream for question: {question}")
    
    stream = grounded_rag_stream(
        creator_id=creator_id,
        question=question,
        user_id=1,
    )
    
    async for chunk in stream:
        print(chunk, end="")
    print("\n--- DONE ---")

if __name__ == "__main__":
    asyncio.run(main())
