from backend.db import db

def diag_chunks(creator_id):
    res = db.execute_query("""
        SELECT d.id, d.title, d.source, COUNT(c.id) as chunk_count
        FROM documents d
        LEFT JOIN chunks c ON d.id = c.document_id
        WHERE d.creator_id = %s
        GROUP BY d.id, d.title, d.source
    """, (creator_id,))
    
    print(f"Documents and Chunk counts for Creator {creator_id}:")
    for r in res:
        print(f"  - [{r['source']}] {r['title']} (ID: {r['id']}): {r['chunk_count']} chunks")

if __name__ == "__main__":
    diag_chunks(12)
