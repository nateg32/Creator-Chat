import asyncio
import sys
import os
import argparse

# Add current directory to path
sys.path.append(os.getcwd())

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trigger soul_md / fingerprint generation for a creator.")
    parser.add_argument("creator_id", type=int, help="Creator ID to process")
    parser.add_argument("--refresh", action="store_true", help="Force a full refresh before generating")
    return parser.parse_args()


async def trigger(creator_id: int, refresh: bool = False):
    from backend.services.fingerprint_service import fingerprint_service
    print(f"Triggering fingerprint for creator {creator_id}...")
    await fingerprint_service.generate_fingerprint_async(creator_id, refresh=refresh)
    print("Done.")

if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(trigger(args.creator_id, refresh=args.refresh))
