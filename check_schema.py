from backend.db import db
query = """
    SELECT table_name, column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name IN ('scrape_items', 'documents', 'chunks', 'embeddings')
    ORDER BY table_name, ordinal_position;
"""
results = db.execute_query(query)
for r in results:
    if r['table_name'] in ('scrape_items', 'documents'):
        print(f"{r['table_name']}.{r['column_name']}: {r['data_type']}")
