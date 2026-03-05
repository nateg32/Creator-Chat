from backend.db import db
import json

cols = db.execute_query("SELECT column_name FROM information_schema.columns WHERE table_name = 'creators'")
print([r['column_name'] for r in cols])
