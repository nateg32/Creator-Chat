import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from backend.db import db

tables = ['documents', 'chunks', 'creators', 'user_creator_preferences', 'scrape_runs', 'scrape_items']

with open('schema_out_utf8.txt', 'w', encoding='utf-8') as f:
    for table in tables:
        f.write(f"\n--- TABLE: {table} ---\n")
        try:
            columns = db.execute_query(f"SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns WHERE table_name = '{table}';")
            for row in columns:
                f.write(f"{row['column_name']}: {row['data_type']} (Nullable: {row['is_nullable']}, Default: {row['column_default']})\n")
            
            # Get constraints
            f.write("Constraints:\n")
            constraints = db.execute_query(f"SELECT conname, pg_get_constraintdef(c.oid) as def FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE conrelid = '{table}'::regclass;")
            for row in constraints:
                f.write(f"  {row['conname']}: {row['def']}\n")
                
            f.write("Indexes:\n")
            indexes = db.execute_query(f"SELECT indexname, indexdef FROM pg_indexes WHERE tablename = '{table}';")
            for row in indexes:
                f.write(f"  {row['indexname']}: {row['indexdef']}\n")
                
        except Exception as e:
            f.write(f"Error fetching table {table}: {e}\n")
