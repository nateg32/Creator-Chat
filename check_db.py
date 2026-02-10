import sys
import os
import pprint
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from db import db

try:
    print("--- Creators Column Types ---")
    columns = db.execute_query("""
        SELECT column_name, data_type, character_maximum_length
        FROM information_schema.columns 
        WHERE table_name = 'creators'
        ORDER BY column_name;
    """)
    for col in columns:
        print(f"{col['column_name']}: {col['data_type']} (max: {col['character_maximum_length']})")

    print("\n--- Creators Data Sample ---")
    creators = db.execute_query("SELECT id, name, handle, profile_picture_url FROM creators ORDER BY id DESC LIMIT 1")
    for c in creators:
        # Truncate long strings for display
        c_disp = {k: (v[:50] + '...' if isinstance(v, str) and len(v) > 50 else v) for k, v in c.items()}
        print(c_disp)

except Exception as e:
    print(f"Error: {e}")
