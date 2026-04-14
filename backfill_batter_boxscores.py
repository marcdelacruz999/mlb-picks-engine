#!/usr/bin/env python3
"""
One-shot backfill script — collects per-batter game logs from
opening day (2026-04-01) through yesterday and stores them in batter_game_logs.
Safe to re-run: INSERT OR IGNORE means no duplicates.

Run once to seed the table, then nightly --results handles it going forward.
"""
from datetime import date, timedelta
import database as db

START_DATE = date(2026, 4, 1)
END_DATE   = date.today() - timedelta(days=1)   # through yesterday

db.init_db()

current = START_DATE
total = 0

while current <= END_DATE:
    ds = current.isoformat()
    inserted = db.collect_batter_boxscores(ds)
    total += inserted
    current += timedelta(days=1)

print(f"\nDone. Stored {total} batter game log rows total.")
