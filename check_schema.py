from backend.db import db
import sys

try:
    print("Columns in scrape_items:")
    cols = db.execute_query("SELECT column_name FROM information_schema.columns WHERE table_name = 'scrape_items'")
    for c in sorted([x['column_name'] for x in cols]):
        print(c)
except Exception as e:
    print(f"Error: {e}")
