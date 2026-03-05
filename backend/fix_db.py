import psycopg
from backend.settings import settings

def fix_db():
    try:
        conn = psycopg.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            dbname=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD
        )
        conn.autocommit = True
    except Exception as e:
        print(f"Failed to connect to DB: {e}")
        return

    with conn.cursor() as cur:
        try:
            print("Checking 'creators' table schema...")
            
            # 1. Check for 'name' column
            cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='creators' AND column_name='name';")
            if not cur.fetchone():
                print("Adding missing 'name' column...")
                cur.execute("ALTER TABLE creators ADD COLUMN name TEXT DEFAULT '';")
            
            # 2. Check for 'handle' column
            cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='creators' AND column_name='handle';")
            if not cur.fetchone():
                print("Adding missing 'handle' column...")
                cur.execute("ALTER TABLE creators ADD COLUMN handle TEXT DEFAULT '';")
                
            # 3. Check for 'platforms' column
            cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='creators' AND column_name='platforms';")
            if not cur.fetchone():
                print("Adding missing 'platforms' column...")
                cur.execute("ALTER TABLE creators ADD COLUMN platforms JSONB DEFAULT '[]'::jsonb;")

            # 4. Check for 'platform_configs' column (from migration 004)
            cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='creators' AND column_name='platform_configs';")
            if not cur.fetchone():
                print("Adding missing 'platform_configs' column...")
                cur.execute("ALTER TABLE creators ADD COLUMN platform_configs JSONB DEFAULT '{}'::jsonb;")
            
            print("Schema fix complete.")
            
        except Exception as e:
            print(f"Error fixing schema: {e}")

    conn.close()

if __name__ == "__main__":
    fix_db()
