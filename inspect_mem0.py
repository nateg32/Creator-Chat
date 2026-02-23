import sys
import os
try:
    import mem0
    from mem0 import Memory
    print(f"Mem0 Location: {mem0.__file__}")
    
    # Try to inspect defaults
    try:
        m = Memory()
        if hasattr(m, 'config'):
            print("Config:", m.config)
        else:
            print("No visible config attribute on instance.")
    except Exception as e:
        print(f"Instantiation Error: {e}")

    # Inspect Init signature
    import inspect
    sig = inspect.signature(Memory.__init__)
    print(f"Init Signature: {sig}")

except ImportError as e:
    print(f"Import Error: {e}")
    # Print sys.path
    print("Sys Path:", sys.path)
except Exception as e:
    print(f"General Error: {e}")
