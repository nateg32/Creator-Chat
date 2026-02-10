import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from db import db

try:
    print("--- Users Column Types ---")
    columns = db.execute_query("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'users';
    """)
    for col in columns:
        print(f"{col['column_name']}: {col['data_type']}")
except Exception as e:
    print(f"Error: {e}")
