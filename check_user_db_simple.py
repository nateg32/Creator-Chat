import sys
sys.path.append('backend')
from db import db
from settings import settings

row = db.execute_one("SELECT id, display_name, response_preferences FROM users WHERE id = 1")
if row:
    print(f"Name: {row.get('display_name')}")
    print(f"Prefs: {row.get('response_preferences')}")
else:
    print("No user found")
