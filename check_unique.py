from backend.db import db

def check_unique():
    res = db.execute_query("""
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE tablename = 'documents' AND indexdef LIKE '%UNIQUE%';
    """)
    print("Unique Indexes on 'documents':")
    for r in res:
        print(f"Index: {r['indexname']}")
        print(f"Def: {r['indexdef']}")
        print("-" * 20)

if __name__ == "__main__":
    check_unique()
