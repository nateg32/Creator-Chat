from backend.db import db
import json

def check_schema():
    columns = db.execute_query("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'creators'")
    print("CREATORS TABLE COLUMNS:")
    for col in columns:
        print(f"  {col['column_name']} ({col['data_type']})")
    
    # Also check a sample record for visual_config etc
    sample = db.execute_one("SELECT * FROM creators LIMIT 1")
    if sample:
        print("\nSAMPLE DATA KEYS:")
        for k in sample.keys():
            val_preview = str(sample[k])[:50] + "..." if len(str(sample[k])) > 50 else str(sample[k])
            print(f"  {k}: {val_preview}")

if __name__ == "__main__":
    check_schema()
