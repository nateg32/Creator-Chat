import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from backend.db import db

row = db.execute_one("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'creators' AND column_name = 'voice_profile'")
print("Column exists:", row)
