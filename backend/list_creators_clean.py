import sys
import os
import json
sys.path.append(os.getcwd())
from backend.db import db

def check_db():
    try:
        print("Listing ALL creators with specific focus on IDs...")
        creators = db.execute_query("SELECT id, name, handle, youtube_handle, youtube_channel_id FROM creators ORDER BY id ASC")
        for c in creators:
            print(f"ID: {c['id']}, Name: '{c['name']}', Handle: '{c['handle']}', YT_Handle: '{c['youtube_handle']}'")
        db.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_db()
