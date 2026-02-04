
from backend.db import db
from backend.lib.transcription import transcribe_video
from backend.settings import settings
import json

def rescue_transcripts():
    print("Rescuing transcripts for scrape_items...")
    # Find items that are missing transcripts but have video URLs
    query = """
        SELECT id, metadata, source_url
        FROM scrape_items
        WHERE (transcript IS NULL OR transcript = '')
        AND transcript_status != 'present'
    """
    items = db.execute_query(query)
    print(f"Found {len(items)} potential items to transcribe.")
    
    rescued = 0
    for item in items:
        item_id = item['id']
        metadata = item.get('metadata') or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
            
        video_url = metadata.get('video_url') or metadata.get('videoUrl') or metadata.get('video')
        
        # If still no video_url but it's instagram, try to get it from metadata if it was nested
        if not video_url and isinstance(metadata, dict):
            # sometimes it's inside another dict
            pass
            
        if not video_url:
            continue
            
        print(f"Transcribing {item_id} from {video_url[:50]}...")
        transcript = transcribe_video(video_url)
        
        if transcript:
            update_query = """
                UPDATE scrape_items
                SET transcript = %s, transcript_status = 'present'
                WHERE id = %s::uuid
            """
            db.execute_update(update_query, (transcript, item_id))
            print(f"Successfully rescued {item_id}")
            rescued += 1
            
            # Now, check if this item has already been ingested into 'documents'
            # Typically documents.source_id corresponds to search_item_id or similar
            doc_query = """
                UPDATE documents
                SET content = %s
                WHERE source_id = %s OR metadata->>'search_item_id' = %s
                RETURNING id
            """
            doc_results = db.execute_query(doc_query, (transcript, str(item_id), str(item_id)))
            
            if doc_results:
                for doc in doc_results:
                    doc_id = doc['id']
                    print(f"Updating chunks for document {doc_id}...")
                    from backend.ingest import chunk_text_structured, embed_chunks
                    
                    # Delete old chunks
                    db.execute_update("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
                    
                    # Creator ID lookup from document
                    creator_row = db.execute_one("SELECT creator_id FROM documents WHERE id = %s", (doc_id,))
                    creator_id = creator_row['creator_id']
                    
                    # Create new chunks
                    chunks = chunk_text_structured(transcript, creator_id, doc_id)
                    chunk_ids = []
                    for chunk in chunks:
                        cid = db.execute_insert(
                            "INSERT INTO chunks (creator_id, document_id, chunk_index, chunk_text) VALUES (%s, %s, %s, %s) RETURNING id",
                            (creator_id, doc_id, chunk['index'], chunk['text'])
                        )
                        if cid: chunk_ids.append(cid)
                    
                    # Embed
                    if chunk_ids:
                        embed_chunks(chunk_ids)
                        print(f"Updated chunks and embeddings for document {doc_id}")

    print(f"Finished. Rescued {rescued} transcripts.")

if __name__ == "__main__":
    rescue_transcripts()
