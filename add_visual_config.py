import sys
import os
# Add backend directory to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from db import db

try:
    print("Checking if visual_config column exists...")
    col_exists = db.execute_one("""
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'creators' AND column_name = 'visual_config'
    """)
    if not col_exists:
        print("Adding visual_config column...")
        db.execute_update("ALTER TABLE creators ADD COLUMN visual_config JSONB DEFAULT '{}'::jsonb")
        print("Done.")
    else:
        print("Column already exists.")
except Exception as e:
    print(f"Error: {e}")
