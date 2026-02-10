import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from db import db

try:
    print("Checking users table columns...")
    
    # Add display_name
    col_exists = db.execute_one("SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'display_name'")
    if not col_exists:
        print("Adding display_name...")
        db.execute_update("ALTER TABLE users ADD COLUMN display_name TEXT")

    # Add profile_picture_url
    col_exists = db.execute_one("SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'profile_picture_url'")
    if not col_exists:
        print("Adding profile_picture_url...")
        db.execute_update("ALTER TABLE users ADD COLUMN profile_picture_url TEXT")

    # Add response_preferences
    col_exists = db.execute_one("SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'response_preferences'")
    if not col_exists:
        print("Adding response_preferences...")
        db.execute_update("ALTER TABLE users ADD COLUMN response_preferences JSONB DEFAULT '{}'::jsonb")

    print("Done.")
except Exception as e:
    print(f"Error: {e}")
