import requests
import base64
import sys
import os
import json

# Setup paths for direct DB access if needed (though we'll use API first)
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from db import db

# Dummy small transparent png base64
img_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
b64_img = "data:image/png;base64," + base64.b64encode(img_data).decode('utf-8')

API_URL = "http://127.0.0.1:8000"

try:
    # 1. Get a Creator ID
    print("[TEST] Fetching creators...")
    r = requests.get(f"{API_URL}/creators")
    if r.status_code != 200:
        print(f"[TEST] Failed to fetch creators: {r.text}")
        sys.exit(1)
    
    data = r.json()
    creators = data.get("creators", [])
    if not creators:
        print("[TEST] No creators found in DB. Cannot test update.")
        sys.exit(0)
    
    cid = creators[0]["id"]
    print(f"[TEST] Using Creator ID: {cid}")
    
    # 2. Call Update API
    print("[TEST] Sending PUT request with profile_picture_url...")
    payload = {"profile_picture_url": b64_img}
    r = requests.put(f"{API_URL}/creators/{cid}", json=payload)
    
    if r.status_code != 200:
        print(f"[TEST] Update API failed: {r.status_code} {r.text}")
        sys.exit(1)
    
    print("[TEST] Update API success.")
    resp_json = r.json()
    # Check if response reflects it
    server_pic = resp_json.get("profile_picture_url")
    print(f"[TEST] API Response profile_picture_url: {str(server_pic)[:50]}...")
    
    # 3. Verify in Database Directly
    print("[TEST] Verifying in Database...")
    row = db.execute_one("SELECT profile_picture_url FROM creators WHERE id = %s", (cid,))
    db_pic = row['profile_picture_url'] if row else None
    
    if db_pic:
        print(f"[TEST] DB VERIFIED: {str(db_pic)[:50]}...")
        if db_pic == b64_img:
             print("[TEST] SUCCESS: DB content matches sent content.")
        else:
             print("[TEST] WARNING: DB content differs (maybe re-encoded or partial match?)")
    else:
        print("[TEST] FAILURE: Database column is NULL or Empty after update.")

except Exception as e:
    print(f"[TEST] Error: {e}")
