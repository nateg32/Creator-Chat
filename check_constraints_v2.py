from backend.db import db

def check_constraints():
    res = db.execute_query("""
        SELECT
            conname,
            pg_get_constraintdef(oid)
        FROM
            pg_constraint
        WHERE
            conrelid = 'documents'::regclass;
    """)
    print("Full Constraints list:")
    for r in res:
        print(f"Name: {r['conname']}")
        print(f"Def: {r['pg_get_constraintdef']}")
        print("-" * 20)

if __name__ == "__main__":
    check_constraints()
