from db import db
import json

try:
    cols = db.execute_query("SELECT column_name FROM information_schema.columns WHERE table_name = 'creators'")
    print(json.dumps([r['column_name'] for r in cols]))
except Exception as e:
    print(f"Error: {e}")
