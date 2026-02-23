import sys
import os
import json
sys.path.append(os.getcwd())
from db import db

def check_db():
    try:
        print("Checking creator 25...")
        creator = db.execute_one("SELECT * FROM creators WHERE id = 25")
        print(json.dumps(creator, indent=2, default=str))
        db.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_db()
