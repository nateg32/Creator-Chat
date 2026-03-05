import sys
import os
sys.path.append(os.getcwd())
from backend.db import db

def check_db():
    try:
        print("Checking creators...")
        creators = db.execute_query("SELECT * FROM creators")
        print(json.dumps(creators, indent=2, default=str))
            print(f"Creator ID: {r['id']}, Name: {r.get('name')}, Handle: {r.get('handle')}")
            
            # Use 'TradesbySci' or similar
            if 'tradesbysci' in str(r.get('handle','')).lower() or 'tradesbysci' in str(r.get('name','')).lower():
                print(f"  MATCH FOUND: {r['id']}")
                docs = db.execute_query("SELECT id, title, url FROM documents WHERE creator_id = %s AND title ILIKE '%%market structure%%' LIMIT 5", (r['id'],))
                for d in docs:
                    print(f"    - {d['title']} ({d['url']})")
            else:
                print("    - No documents found with 'market structure' in title.")
        
        db.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_db()
