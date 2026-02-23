import sys
import os
import json
sys.path.append(os.getcwd())
from db import db

def check_db():
    try:
        print("Searching for TradesbySci...")
        res = db.execute_query("SELECT id, name, handle, youtube_handle, youtube_channel_id FROM creators WHERE name ILIKE '%tradesbysci%' OR handle ILIKE '%tradesbysci%' OR youtube_handle ILIKE '%tradesbysci%'")
        print(json.dumps(res, indent=2, default=str))
        db.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_db()
