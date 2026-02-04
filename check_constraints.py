from backend.db import db

def check_constraints():
    res = db.execute_query("""
        SELECT
            conname as constraint_name,
            pg_get_constraintdef(c.oid) as constraint_definition
        FROM
            pg_constraint c
        JOIN
            pg_class t ON c.conrelid = t.oid
        WHERE
            t.relname = 'documents';
    """)
    print("Constraints on 'documents' table:")
    for r in res:
        print(f"  - {r['constraint_name']}: {r['constraint_definition']}")

if __name__ == "__main__":
    check_constraints()
