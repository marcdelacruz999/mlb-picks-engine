"""
calibrate.py — Weekly Signal Calibration Report

Usage:
    python3 calibrate.py             # analyze + post Discord report
    python3 calibrate.py --apply     # apply suggested weights to config.py + commit
    python3 calibrate.py --days N    # override lookback window (default 7)
    python3 calibrate.py --test      # dry run, print to stdout, no Discord send
"""

import sqlite3
import re
import json
import argparse
import subprocess
from datetime import datetime, timedelta
from typing import Optional, List

import config


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_graded_picks(conn: sqlite3.Connection, days: int = 7,
                       _now: Optional[str] = None) -> List[dict]:
    """Return all discord-sent, fully-graded picks within the last N days."""
    if _now is None:
        _now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cutoff_dt = datetime.strptime(_now, "%Y-%m-%d %H:%M:%S") - timedelta(days=days)
    cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")

    rows = conn.execute("""
        SELECT pick_type, pick_team, confidence, status, edge_score,
               edge_pitching, edge_offense, edge_bullpen, edge_advanced,
               edge_market, edge_weather, notes, ml_odds, ou_odds, ev_score,
               created_at
        FROM picks
        WHERE discord_sent = 1
          AND status IN ('won', 'lost', 'push')
          AND created_at >= ?
    """, (cutoff,)).fetchall()

    return [dict(r) for r in rows]
