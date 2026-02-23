
import sys
import os
import logging

# Set up logging to stdout
logging.basicConfig(level=logging.INFO)

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from grounded_rag import grounded_rag_ask
from db import db

def test_greeting_policy():
    # Find a test creator
    creator = db.execute_one("SELECT id, name FROM creators LIMIT 1")
    if not creator:
        print("No creators found in DB")
        return

    print(f"Testing GREETING for creator: {creator['name']} (ID: {creator['id']})")
    
    # greeting_only
    question1 = "yo" 
    print(f"\n--- User: {question1} ---")
    res1 = grounded_rag_ask(
        creator_id=creator['id'],
        question=question1,
        user_id=1,
        debug=True
    )
    print("\nAI Response:\n", res1['answer'])
    
    # smalltalk
    question2 = "How's it going?"
    print(f"\n--- User: {question2} ---")
    res2 = grounded_rag_ask(
        creator_id=creator['id'],
        question=question2,
        user_id=1,
        debug=True
    )
    print("\nAI Response:\n", res2['answer'])

if __name__ == "__main__":
    test_greeting_policy()
