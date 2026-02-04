from backend.db import db

def fix_dan_associations():
    # Target creator
    target_id = 12
    # Dan's name/handle patterns
    patterns = ['%danmartell%', '%dmartell%']
    
    # 1. Find all Dan creators
    dan_creators = db.execute_query("""
        SELECT id FROM creators 
        WHERE handle ILIKE '%danmartell%' 
        OR display_name ILIKE '%danmartell%'
        OR handle ILIKE '%dmartell%'
    """)
    dan_ids = [r['id'] for r in dan_creators]
    print(f"Found Dan creator IDs: {dan_ids}")
    
    if target_id not in dan_ids:
        print(f"Warning: Target ID {target_id} not in found IDs. Proceeding anyway.")
        dan_ids.append(target_id)
        
    # 2. Update documents: if a document belongs to ANY Dan creator, assign it to target_id
    res = db.execute_update("""
        UPDATE documents
        SET creator_id = %s
        WHERE creator_id = ANY(%s)
    """, (target_id, dan_ids))
    print(f"Updated {res} documents to creator_id {target_id}")
    
    # 3. Update chunks
    res = db.execute_update("""
        UPDATE chunks
        SET creator_id = %s
        WHERE creator_id = ANY(%s)
    """, (target_id, dan_ids))
    print(f"Updated {res} chunks to creator_id {target_id}")

if __name__ == "__main__":
    fix_dan_associations()
