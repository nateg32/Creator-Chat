import sys
import os
sys.path.append(os.getcwd())
from backend.db import db

def check_schema():
    print("Tables:")
    tables = db.execute_query("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    for t in tables:
        print(f"  {t['table_name']}")
    
    print("\nchat_messages table columns:")
    rows = db.execute_query("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'chat_messages'")
    for row in rows:
        print(f"  {row['column_name']}: {row['data_type']}")

if __name__ == "__main__":
    check_schema()

if __name__ == "__main__":
    check_schema()
