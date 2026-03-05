from backend.db import db
try:
    print("Connecting to DB...")
    db.connect()
    print("Connected.")
    res = db.execute_query("SELECT 1")
    print(f"Query Result: {res}")
except Exception as e:
    print(f"DB Error: {e}")
