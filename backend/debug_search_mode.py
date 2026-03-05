
from backend.db import db
import json

def check_creator_settings():
    # Use the name from the chat history
    creator_name = "Jordan Welch"
    row = db.execute_one("SELECT id, name, search_mode, google_api_key_enabled FROM creators WHERE name = %s", (creator_name,))
    if row:
        print(f"Creator: {row['name']} (ID: {row['id']})")
        print(f"Search Mode: {row['search_mode']}")
        print(f"Google API Key Enabled: {row['google_api_key_enabled']}")
    else:
        print(f"Creator '{creator_name}' not found.")
        # List all creators with search_mode
        rows = db.execute_query("SELECT id, name, search_mode FROM creators")
        print("\nAll Creators:")
        for r in rows:
            print(f"- {r['name']} (ID: {r['id']}): {r['search_mode']}")

if __name__ == "__main__":
    check_creator_settings()
