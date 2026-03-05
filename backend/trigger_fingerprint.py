import asyncio
import sys
import os

# Add current directory to path
sys.path.append(os.getcwd())

async def trigger():
    from backend.services.fingerprint_service import fingerprint_service
    creator_id = 29
    print(f"Triggering fingerprint for creator {creator_id}...")
    await fingerprint_service.generate_fingerprint_async(creator_id)
    print("Done.")

if __name__ == "__main__":
    asyncio.run(trigger())
