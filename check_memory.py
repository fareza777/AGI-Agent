#!/usr/bin/env python3
"""One-off memory audit for engram.db."""
import os
import sqlite3

from engram import config
from engram.store import Store

store = Store()
conn = store._conn

print("=== ENGRAM MEMORY AUDIT ===")
print(f"DB path:   {config.DB_PATH}")
print(f"Exists:    {os.path.exists(config.DB_PATH)}")
if os.path.exists(config.DB_PATH):
    print(f"Size:      {os.path.getsize(config.DB_PATH):,} bytes")

events_total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
events_user = conn.execute(
    "SELECT COUNT(*) FROM events WHERE actor='user'"
).fetchone()[0]
events_agent = conn.execute(
    "SELECT COUNT(*) FROM events WHERE actor='agent'"
).fetchone()[0]
unprocessed = store.unprocessed_count()
claims_active = conn.execute(
    "SELECT COUNT(*) FROM claims WHERE valid_to IS NULL AND superseded_by IS NULL"
).fetchone()[0]
claims_total = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
claims_superseded = conn.execute(
    "SELECT COUNT(*) FROM claims WHERE valid_to IS NOT NULL OR superseded_by IS NOT NULL"
).fetchone()[0]
goals_active = conn.execute(
    "SELECT COUNT(*) FROM goals WHERE status='active'"
).fetchone()[0]

print()
print("--- COUNTS ---")
print(f"Events (total):        {events_total}")
print(f"  user messages:       {events_user}")
print(f"  agent messages:      {events_agent}")
print(f"  belum dikonsolidasi: {unprocessed}")
print(f"Claims (aktif):        {claims_active}")
print(f"Claims (total):        {claims_total}")
print(f"Claims (superseded):   {claims_superseded}")
print(f"Goals (aktif):         {goals_active}")

if events_total:
    ratio = claims_active / events_total * 100
    print(f"Distil ratio:          {claims_active}/{events_total} events -> {ratio:.1f}% jadi claim aktif")

print()
print("--- ACTIVE CLAIMS ---")
rows = conn.execute(
    """
    SELECT subject, predicate, value, kind, confidence, valid_from
    FROM claims
    WHERE valid_to IS NULL AND superseded_by IS NULL
    ORDER BY id
    """
).fetchall()
if not rows:
    print("(belum ada claim aktif)")
else:
    for r in rows:
        print(
            f"  • {r['subject']} | {r['predicate']} = {r['value']} "
            f"[{r['kind']}, conf {r['confidence']:.2f}, sejak {r['valid_from'][:10]}]"
        )

print()
print("--- RECENT EVENTS (8 terakhir) ---")
for r in conn.execute(
    """
    SELECT id, ts, actor, kind, substr(content, 1, 100) AS snippet
    FROM events ORDER BY id DESC LIMIT 8
    """
):
    print(f"  #{r['id']} [{r['ts'][:16]}] {r['actor']}/{r['kind']}: {r['snippet']!r}")

print()
print("--- SUPERSEDED / HISTORY ---")
hist = conn.execute(
    """
    SELECT subject, predicate, value, valid_from, valid_to
    FROM claims
    WHERE valid_to IS NOT NULL OR superseded_by IS NOT NULL
    ORDER BY id
    """
).fetchall()
if not hist:
    print("(belum ada perubahan keyakinan)")
else:
    for r in hist:
        end = r["valid_to"][:10] if r["valid_to"] else "?"
        print(
            f"  • {r['subject']}.{r['predicate']} = {r['value']} "
            f"({r['valid_from'][:10]} -> diganti {end})"
        )

print()
print("--- ALLERGY / COLOR / BIRTH ---")
for r in conn.execute(
    """
    SELECT subject, predicate, value, kind, confidence, valid_from, valid_to
    FROM claims
    WHERE predicate LIKE '%allerg%' OR predicate LIKE '%color%'
       OR predicate LIKE '%birth%' OR value LIKE '%kacang%'
       OR value LIKE '%hijau%' OR value LIKE '%udang%'
    ORDER BY id
    """
):
    status = "AKTIF" if r["valid_to"] is None else f"diganti {r['valid_to'][:10]}"
    print(f"  • {r['subject']} | {r['predicate']} = {r['value']} [{status}]")
