
import os
import sys
import json
import logging

# Set up logging to stdout
logging.basicConfig(level=logging.INFO)

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from grounded_rag import recommend_one_content
from db import db

def test_recommendation():
    # Find a test creator
    creator = db.execute_one("SELECT id, name FROM creators LIMIT 1")
    if not creator:
        print("No creators found in DB")
        return

    print(f"Testing for creator: {creator['name']} (ID: {creator['id']})")
    
    question = "Recommend a video about your high protein diet."
    
    # We ignore history for simple test
    result = recommend_one_content(
        user_id="test_user",
        creator_id=creator['id'],
        user_message=question,
        conversation_history=[],
        creator_row=creator,
        debug=True
    )
    
    print("\nREC RESULT:")
    print(json.dumps(result, indent=2, default=str))

if __name__ == "__main__":
    test_recommendation()
