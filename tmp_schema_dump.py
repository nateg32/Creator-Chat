import psycopg
from psycopg.rows import dict_row

conn = psycopg.connect(
    host="localhost",
    port=5433,
    dbname="rag_db",
    user="postgres",
    password=""
)

tables = ['documents', 'chunks', 'search_cache', 'scrape_runs', 'scrape_items', 'creators', 'users']
for table in tables:
    print(f"\n--- TABLE: {table} ---")
    cur = conn.cursor(row_factory=dict_row)
    cur.execute(f"SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns WHERE table_name = '{table}';")
    for row in cur.fetchall():
        print(f"{row['column_name']}: {row['data_type']} (Nullable: {row['is_nullable']}, Default: {row['column_default']})")
    
    # Get constraints
    cur.execute(f"SELECT conname, pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE conrelid = '{table}'::regclass;")
    print("Constraints:")
    for row in cur.fetchall():
        print(f"  {row['conname']}: {row['pg_get_constraintdef']}")

conn.close()
