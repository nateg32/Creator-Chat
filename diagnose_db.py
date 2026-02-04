from backend.db import db
import json

def check_stats(creator_id):
    docs = db.execute_one("SELECT COUNT(*) FROM documents WHERE creator_id = %s", (creator_id,))
    chunks = db.execute_one("SELECT COUNT(*) FROM chunks WHERE creator_id = %s", (creator_id,))
    embs = db.execute_one("""
        SELECT COUNT(*) 
        FROM embeddings e 
        JOIN chunks c ON e.chunk_id = c.id 
        WHERE c.creator_id = %s
    """, (creator_id,))
    
    print(f"Stats for Creator {creator_id}:")
    print(f"  Documents: {docs['count'] if docs else 0}")
    print(f"  Chunks: {chunks['count'] if chunks else 0}")
    print(f"  Embeddings: {embs['count'] if embs else 0}")
    
    # Check if any documents have transcripts vs generic titles
    sample_docs = db.execute_query("SELECT id, title, source, metadata FROM documents WHERE creator_id = %s LIMIT 5", (creator_id,))
    print("\nSample Documents:")
    for d in sample_docs:
        print(f"  - [{d['source']}] {d['title']} (ID: {d['id']})")

if __name__ == "__main__":
    check_stats(12)
