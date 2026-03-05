import sys
import os
import json
sys.path.append(os.getcwd())
from backend.db import db

def check_db():
    try:
        print("Checking creators...")
        creators = db.execute_query("SELECT id, name, handle, youtube_handle FROM creators")
        print(json.dumps(creators, indent=2, default=str))
        db.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_db()
