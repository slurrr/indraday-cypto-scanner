import json
from pathlib import Path

PATH = Path("utils/event_snapshots.jsonl")

def write_snapshot(snapshot: dict):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    with PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot) + "\n")
