import os
import sqlite3
import sys
from pathlib import Path

# Windows shells default to cp1252; this script prints Unicode paths.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ws = Path("workspace")
print("=== workspace files ===")
if ws.exists():
    for p in sorted(ws.rglob("*")):
        if p.is_file():
            print(p, p.stat().st_size)
else:
    print("(missing)")

c = sqlite3.connect("engram.db")
print("\n=== recent tool/system events ===")
for r in c.execute(
    """
    SELECT id, ts, actor, kind, substr(content, 1, 250)
    FROM events
    WHERE content LIKE '%create_document%'
       OR content LIKE '%send_file%'
       OR content LIKE '%tool%'
       OR kind = 'tool'
    ORDER BY id DESC LIMIT 20
    """
):
    print(r)

print("\n=== last 6 user + agent messages ===")
for r in c.execute(
    """
    SELECT id, ts, actor, substr(content, 1, 120)
    FROM events
    WHERE kind = 'message' AND actor IN ('user', 'agent')
    ORDER BY id DESC LIMIT 12
    """
):
    print(r)
