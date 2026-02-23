
import sys
import os
import logging

# Set up logging to stdout
logging.basicConfig(level=logging.INFO)

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from services.clf_service import CLFService
from db import db

def test_clf_extraction():
    # Find a test creator with chunks
    creator = db.execute_one("""
        SELECT c.id, c.name 
        FROM creators c
        JOIN documents d ON c.id = d.creator_id
        LIMIT 1
    """)
    if not creator:
        print("No creators with documents found in DB")
        return

    print(f"Testing CLF Extraction for: {creator['name']} (ID: {creator['id']})")
    
    clf = CLFService(creator['id'])
    profile = clf.extract_and_save_profile()
    
    print("\nEXTRACTED PROFILE:")
    import json
    print(json.dumps(profile, indent=2))

if __name__ == "__main__":
    test_clf_extraction()
