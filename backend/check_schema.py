from db import Database
import json

db = Database()
try:
    rows = db.execute_query("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'chat_threads'")
    print(json.dumps(rows, indent=2))
except Exception as e:
    print(e)
