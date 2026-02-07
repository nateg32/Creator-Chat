import sys
print(f"Python executable: {sys.executable}")
print(f"Python path: {sys.path}")

try:
    import apify_client
    print(f"SUCCESS: apify_client imported from {apify_client.__file__}")
    from apify_client import ApifyClient
    print("SUCCESS: ApifyClient class imported")
except ImportError as e:
    print(f"ERROR: Failed to import apify_client: {e}")
except Exception as e:
    print(f"ERROR: {e}")
