
import sys
import os
import json
import logging

# Set up logging to stdout
logging.basicConfig(level=logging.INFO)

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from grounded_rag import grounded_rag_ask
from db import db

def test_dm_policy():
    # Find a test creator
    creator = db.execute_one("SELECT id, name FROM creators LIMIT 1")
    if not creator:
        print("No creators found in DB")
        return

    print(f"Testing for creator: {creator['name']} (ID: {creator['id']})")
    
    # 1. Underspecified message
    # 'start_goal' intent usually needs goal_type and experience_level
    question1 = "I want to start bulking." 
    print(f"\n--- Turn 1: {question1} ---")
    res1 = grounded_rag_ask(
        creator_id=creator['id'],
        question=question1,
        user_id=1,
        debug=True
    )
    print("\nAI Response:\n", res1['answer'])
    print("\nCards:", res1.get('cards', []))
    
    # 2. Sequential message to see if it fills slots
    question2 = "I'm a beginner."
    print(f"\n--- Turn 2: {question2} ---")
    res2 = grounded_rag_ask(
        creator_id=creator['id'],
        question=question2,
        user_id=1,
        conversation_history=[{"role": "user", "content": question1}, {"role": "assistant", "content": res1['answer']}],
        debug=True
    )
    print("\nAI Response:\n", res2['answer'])
    print("\nCards:", res2.get('cards', []))

if __name__ == "__main__":
    test_dm_policy()
