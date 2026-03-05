import sys
sys.path.insert(0, './backend')
from db import db
creator = db.execute_one('SELECT id FROM creators LIMIT 1')
print(f"Valid Creator ID: {creator['id']}" if creator else "No creators found.")
