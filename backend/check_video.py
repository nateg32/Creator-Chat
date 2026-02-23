from db import db
import sys

search_term = "cgFFQmry8n4" # The video ID from the user's message
results = db.execute_query("SELECT id, title FROM documents WHERE url LIKE %s OR metadata::text LIKE %s", (f"%{search_term}%", f"%{search_term}%"))
print(f"Found {len(results)} direct matches in DB.")
for r in results:
    print(f"ID: {r['id']}, Title: {r['title']}")
