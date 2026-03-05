import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db import db

def list_tables():
    res = db.execute_query("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    for r in res:
        print(r['table_name'])

if __name__ == "__main__":
    list_tables()
