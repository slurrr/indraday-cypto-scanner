import sys
import os
sys.path.append(os.getcwd())

from models.types import StatusSink, PatternType

if __name__ == "__main__":
    print("Starting import...")
    try:
        from models.types import StatusSink
        print("Imported StatusSink")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
