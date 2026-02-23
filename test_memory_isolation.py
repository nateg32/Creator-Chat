import sys
import os
import time

# Ensure backend dir is in path
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.join(current_dir, "backend")
if backend_dir not in sys.path:
    sys.path.append(backend_dir)

from core.memory_integration import MemoryIntegration

def test_isolation():
    print("Initializing MemoryIntegration...")
    mem_int = MemoryIntegration()
    
    user_id = "user_999"
    creator_1 = "creator_alpha"
    creator_2 = "creator_beta"
    thread_A = "thread_A"
    thread_B = "thread_B"
    
    fact_A = "I love sushi."
    fact_B = "I prefer tacos."
    fact_C1 = "My favorite color is blue."
    
    print("\n--- Testing Thread Isolation ---")
    print(f"Adding memory to {creator_1} | {user_id} | {thread_A}: '{fact_A}'")
    mem_int.add_user_message(creator_1, user_id, thread_A, fact_A)
    
    print(f"Adding memory to {creator_1} | {user_id} | {thread_B}: '{fact_B}'")
    mem_int.add_user_message(creator_1, user_id, thread_B, fact_B)
    
    # Give it a second
    time.sleep(1)
    
    print(f"Searching {thread_A} for sushi...")
    res_A = mem_int.search(creator_1, user_id, thread_A, "What do I like to eat?")
    print(f"Results A: {res_A}")
    
    print(f"Searching {thread_B} for sushi...")
    res_B = mem_int.search(creator_1, user_id, thread_B, "What do I like to eat?")
    print(f"Results B: {res_B}")
    
    found_in_A = any("sushi" in r.lower() for r in res_A)
    found_in_B = any("sushi" in r.lower() for r in res_B)
    lost_in_B = any("tacos" in r.lower() for r in res_B)
    
    if found_in_A and not found_in_B and lost_in_B:
        print("SUCCESS: Thread isolation verified.")
    else:
        print(f"FAILURE: Thread isolation failed. A={found_in_A}, B_sushi={found_in_B}, B_tacos={lost_in_B}")

    print("\n--- Testing Creator Isolation ---")
    print(f"Adding memory to {creator_1} | {user_id} | {thread_A}: '{fact_C1}'")
    mem_int.add_user_message(creator_1, user_id, thread_A, fact_C1)
    
    # Search for it in Creator 2
    print(f"Searching {creator_2} for color...")
    res_C2 = mem_int.search(creator_2, user_id, thread_A, "What is my favorite color?")
    print(f"Results C2: {res_C2}")
    
    found_in_C2 = any("blue" in r.lower() for r in res_C2)
    
    if not found_in_C2:
        print("SUCCESS: Creator isolation verified.")
    else:
        print("FAILURE: Creator isolation failed.")

if __name__ == "__main__":
    test_isolation()
