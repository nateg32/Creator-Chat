import sys
sys.path.append('backend')
from db import db
from settings import settings

print(f"DB Host: {settings.DB_HOST}")

row = db.execute_one("SELECT id, display_name, response_preferences FROM users WHERE id = 1")
print(f"User Row: {row}")
