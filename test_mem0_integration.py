import sys
import os
import time

# Ensure backend dir is in path
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.join(current_dir, "backend")
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

try:
    from core.memory_integration import MemoryIntegration
    print("Importing MemoryIntegration... Success")
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

def test_memory():
    print("Initializing MemoryIntegration...")
    mem_int = MemoryIntegration()
    
    print("MemoryIntegration initialized.")


    user_id = "test_user_123"
    fact = "I love eating pizza on Fridays."
    
    print(f"Adding user message: '{fact}'")
    mem_int.add_user_message(user_id, fact)
    
    # Wait a bit for async processing if any (mem0 might be async or slow)
    time.sleep(2)
    
    print("Searching for memory...")
    results = mem_int.search(user_id, "What do I like to eat?")
    print(f"Results: {results}")
    
    if any("pizza" in r.lower() for r in results):
        print("SUCCESS: Found 'pizza' in memory!")
    else:
        print("WARNING: 'pizza' not found immediately. This might be expected if extraction is slow.")

if __name__ == "__main__":
    test_memory()
