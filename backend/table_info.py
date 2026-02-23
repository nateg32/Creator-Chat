import db
import json

def main():
    db.db.connect()
    rows = db.db.execute_query("SELECT column_name FROM information_schema.columns WHERE table_name = 'documents'")
    cols = [r['column_name'] for r in rows]
    print(json.dumps(cols))

if __name__ == "__main__":
    main()
