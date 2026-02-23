
import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from db import db
import json

def check_state():
    # Fetch all states to see what happened during test
    rows = db.execute_query("SELECT * FROM conversation_state")
    if rows:
        for row in rows:
            print(f"State for User {row['user_id']}, Creator {row['creator_id']}:")
            print("Known Slots:", row['known_slots'])
            print("Last Intent:", row['last_intent'])
            print("Last Question:", row['last_question'])
            print("-" * 20)
    else:
        print("No state found in DB")

if __name__ == "__main__":
    check_state()
