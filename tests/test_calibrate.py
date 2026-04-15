import sqlite3
import tempfile
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def _make_db(picks):
    """Create a temp SQLite DB with the picks table populated."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE picks (
            id INTEGER PRIMARY KEY,
            pick_type TEXT,
            pick_team TEXT,
            confidence INTEGER,
            status TEXT,
            edge_score REAL,
            edge_pitching TEXT,
            edge_offense TEXT,
            edge_bullpen TEXT,
            edge_advanced TEXT,
            edge_market TEXT,
            edge_weather TEXT,
            notes TEXT,
            ml_odds INTEGER,
            ou_odds INTEGER,
            ev_score REAL,
            discord_sent INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    for p in picks:
        conn.execute("""
            INSERT INTO picks (pick_type, pick_team, confidence, status,
                edge_score, edge_pitching, edge_offense, edge_bullpen,
                edge_advanced, edge_market, edge_weather, notes,
                ml_odds, ou_odds, ev_score, discord_sent, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            p.get("pick_type","moneyline"), p.get("pick_team","Team A"),
            p.get("confidence",7), p.get("status","won"),
            p.get("edge_score",0.20),
            p.get("edge_pitching",""), p.get("edge_offense",""),
            p.get("edge_bullpen",""), p.get("edge_advanced",""),
            p.get("edge_market",""), p.get("edge_weather",""),
            p.get("notes",""), p.get("ml_odds",-110), p.get("ou_odds",None),
            p.get("ev_score",0.05), p.get("discord_sent",1),
            p.get("created_at","2026-04-14 08:00:00"),
        ))
    conn.commit()
    return conn


def test_fetch_graded_picks_returns_sent_graded_only():
    from calibrate import fetch_graded_picks
    conn = _make_db([
        {"status": "won", "discord_sent": 1, "created_at": "2026-04-14 08:00:00"},
        {"status": "pending", "discord_sent": 1, "created_at": "2026-04-14 08:00:00"},
        {"status": "won", "discord_sent": 0, "created_at": "2026-04-14 08:00:00"},
        {"status": "lost", "discord_sent": 1, "created_at": "2026-04-08 08:00:00"},
    ])
    rows = fetch_graded_picks(conn, days=7, _now="2026-04-14 23:59:59")
    assert len(rows) == 2
    statuses = {r["status"] for r in rows}
    assert statuses == {"won", "lost"}


def test_fetch_graded_picks_window():
    from calibrate import fetch_graded_picks
    conn = _make_db([
        {"status": "won", "discord_sent": 1, "created_at": "2026-04-14 08:00:00"},
        {"status": "won", "discord_sent": 1, "created_at": "2026-04-06 08:00:00"},
    ])
    # 7-day window from Apr 14 → only Apr 14 pick qualifies
    rows = fetch_graded_picks(conn, days=7, _now="2026-04-14 23:59:59")
    assert len(rows) == 1
