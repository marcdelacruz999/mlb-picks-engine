import pytest
import sqlite3
import sys, os
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()


def _insert_batter_log(game_pk, game_date, batter_id, team_id, at_bats, hits,
                        doubles=0, triples=0, home_runs=0, rbi=0, walks=0, strikeouts=0):
    conn = db.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO batter_game_logs
        (mlb_game_id, game_date, batter_id, batter_name, team_id,
         at_bats, hits, doubles, triples, home_runs, rbi, walks, strikeouts, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (game_pk, game_date, batter_id, f"Batter {batter_id}", team_id,
          at_bats, hits, doubles, triples, home_runs, rbi, walks, strikeouts,
          "2026-04-13T00:00:00"))
    conn.commit()
    conn.close()


# ── Test 1: table created by init_db ──────────────────────────────────────────

def test_batter_game_logs_table_exists():
    conn = db.get_connection()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(batter_game_logs)")]
    conn.close()
    assert "batter_id" in cols
    assert "batter_name" in cols
    assert "team_id" in cols
    assert "at_bats" in cols
    assert "hits" in cols
    assert "doubles" in cols
    assert "triples" in cols
    assert "home_runs" in cols
    assert "rbi" in cols
    assert "walks" in cols
    assert "strikeouts" in cols
    assert "game_date" in cols
    assert "mlb_game_id" in cols


# ── Test 2: collect_batter_boxscores parses boxscore correctly ────────────────

def test_collect_batter_boxscores_parses_correctly(tmp_path, monkeypatch):
    """Mock the API responses and verify rows are inserted correctly."""
    schedule_payload = {
        "dates": [{
            "games": [{"gamePk": 999001, "status": {"abstractGameState": "Final"}}]
        }]
    }

    boxscore_payload = {
        "teams": {
            "away": {
                "team": {"id": 101},
                "batters": [501, 502],
                "players": {
                    "ID501": {
                        "person": {"fullName": "Hot Batter"},
                        "stats": {
                            "batting": {
                                "atBats": 4, "hits": 3, "doubles": 1, "triples": 0,
                                "homeRuns": 1, "rbi": 2, "baseOnBalls": 0, "strikeOuts": 0,
                            }
                        }
                    },
                    "ID502": {
                        "person": {"fullName": "Cold Batter"},
                        "stats": {
                            "batting": {
                                "atBats": 4, "hits": 0, "doubles": 0, "triples": 0,
                                "homeRuns": 0, "rbi": 0, "baseOnBalls": 0, "strikeOuts": 3,
                            }
                        }
                    },
                },
            },
            "home": {
                "team": {"id": 102},
                "batters": [601],
                "players": {
                    # pitcher — has pitching stats, should be skipped
                    "ID601": {
                        "person": {"fullName": "Starting Pitcher"},
                        "stats": {
                            "pitching": {"inningsPitched": "6.0"},
                            "batting": {
                                "atBats": 1, "hits": 0, "doubles": 0, "triples": 0,
                                "homeRuns": 0, "rbi": 0, "baseOnBalls": 0, "strikeOuts": 1,
                            },
                        },
                    }
                },
            },
        }
    }

    def mock_get(url, timeout=15):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "schedule" in url:
            resp.json.return_value = schedule_payload
        else:
            resp.json.return_value = boxscore_payload
        return resp

    with patch("requests.get", side_effect=mock_get), \
         patch("time.sleep"):
        inserted = db.collect_batter_boxscores("2026-04-13")

    # 2 away batters parsed; 1 home pitcher skipped → 2 rows
    assert inserted == 2

    conn = db.get_connection()
    rows = conn.execute(
        "SELECT batter_id, hits, at_bats FROM batter_game_logs ORDER BY batter_id"
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0]["batter_id"] == 501
    assert rows[0]["hits"] == 3
    assert rows[0]["at_bats"] == 4
    assert rows[1]["batter_id"] == 502
    assert rows[1]["hits"] == 0


# ── Test 3: get_team_batter_hot_cold returns correct keys ─────────────────────

def test_get_team_batter_hot_cold_correct_keys():
    """Insert enough AB for threshold, verify returned dict has all expected keys."""
    team_id = 10
    today = date.today().isoformat()

    # Insert 4 batters with sufficient AB (12+) spread across multiple games
    for game_num, (batter_id, ab, hits) in enumerate([
        (1001, 15, 6),   # .400 BA → hot
        (1002, 14, 2),   # .143 BA → cold
        (1003, 13, 5),   # .385 BA → hot
        (1004, 12, 3),   # .250 BA → neither
    ]):
        _insert_batter_log(5000 + game_num, today, batter_id, team_id, ab, hits)

    result = db.get_team_batter_hot_cold(team_id)

    assert result is not None
    assert "hot_count" in result
    assert "cold_count" in result
    assert "avg_ba_10d" in result
    assert "sample_abs" in result
    assert result["hot_count"] == 2
    assert result["cold_count"] == 1
    assert result["sample_abs"] == 54  # 15+14+13+12


# ── Test 4: get_team_batter_hot_cold returns None with insufficient data ──────

def test_get_team_batter_hot_cold_returns_none_insufficient_data():
    """Fewer than 30 total AB → should return None."""
    team_id = 20
    today = date.today().isoformat()

    # Only 2 batters with 10 AB each = 20 AB total (below 30 threshold)
    _insert_batter_log(6001, today, 2001, team_id, at_bats=10, hits=3)
    _insert_batter_log(6002, today, 2002, team_id, at_bats=10, hits=4)

    result = db.get_team_batter_hot_cold(team_id)
    assert result is None


# ── Test 5: get_team_batter_hot_cold ignores data older than window ───────────

def test_get_team_batter_hot_cold_ignores_stale_data():
    """Batter logs older than 10 days should not be counted."""
    team_id = 30
    today = date.today()
    old_date = (today - timedelta(days=15)).isoformat()
    recent_date = today.isoformat()

    # Old data (15 days ago) — should be ignored
    for i in range(8):
        _insert_batter_log(7000 + i, old_date, 3000 + i, team_id, at_bats=5, hits=2)

    # Recent data — only 2 batters = 20 AB total (below 30 threshold)
    _insert_batter_log(8001, recent_date, 3100, team_id, at_bats=10, hits=4)
    _insert_batter_log(8002, recent_date, 3101, team_id, at_bats=10, hits=3)

    # Only recent data considered → 20 AB < 30 → None
    result = db.get_team_batter_hot_cold(team_id)
    assert result is None
