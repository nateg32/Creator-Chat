from backend.db import db
import json
import sys

try:
    print("Fetching last 5 search runs...")
    rows = db.execute_query(
        "SELECT search_id, progress_data, updated_at FROM search_progress ORDER BY updated_at DESC LIMIT 5"
    )
    
    for row in rows:
        print(f"--- Search ID: {row['search_id']} ---")
        print(f"Updated At: {row['updated_at']}")
        data = row['progress_data']
        print(f"Status: {data.get('status')}")
        print(f"Error: {data.get('error')}")
        
except Exception as e:
    print(f"Error: {e}")
