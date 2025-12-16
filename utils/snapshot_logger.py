import json
from pathlib import Path
from datetime import datetime

SNAPSHOT_PATH = Path("utils/event_snapshots.jsonl")

def write_snapshot(snapshot: dict):
    snapshot["logged_at"] = datetime.utcnow().isoformat()
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SNAPSHOT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
