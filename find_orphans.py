from backend.db import db

def find_orphan_chunks(creator_id):
    res = db.execute_query("""
        SELECT document_id, COUNT(*) as count
        FROM chunks
        WHERE creator_id = %s
        GROUP BY document_id
    """, (creator_id,))
    
    print(f"Unique document_ids in chunks for Creator {creator_id}:")
    for r in res:
        doc = db.execute_one("SELECT id, title, creator_id FROM documents WHERE id = %s", (r['document_id'],))
        if doc:
            print(f"  - Document ID {r['document_id']} exists: '{doc['title']}' (Creator ID: {doc['creator_id']}). Chunks: {r['count']}")
        else:
            print(f"  - Document ID {r['document_id']} DOES NOT EXIST in documents table. Chunks: {r['count']}")

if __name__ == "__main__":
    find_orphan_chunks(12)
