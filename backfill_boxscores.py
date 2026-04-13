#!/usr/bin/env python3
"""
One-shot backfill script — collects all completed boxscores from
opening day (2026-04-01) through yesterday and stores them in the DB.
Safe to re-run: INSERT OR IGNORE means no duplicates.
"""
import sys
from datetime import date, timedelta
from data_mlb import collect_boxscores
from database import store_boxscores, init_db

START_DATE = date(2026, 4, 1)
END_DATE   = date.today() - timedelta(days=1)   # through yesterday

init_db()

current = START_DATE
total_pitchers = 0
total_teams = 0

while current <= END_DATE:
    ds = current.isoformat()
    data = collect_boxscores(ds)
    p = data.get("pitcher_logs", [])
    t = data.get("team_logs", [])
    if p or t:
        store_boxscores(p, t)
        print(f"  {ds}: {len(p)} pitcher rows, {len(t)} team rows")
        total_pitchers += len(p)
        total_teams    += len(t)
    else:
        print(f"  {ds}: no final games")
    current += timedelta(days=1)

print(f"\nDone. Stored {total_pitchers} pitcher logs, {total_teams} team logs.")
