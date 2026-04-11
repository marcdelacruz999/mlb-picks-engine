import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import database as db


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DATABASE_PATH", db_path)
    db.init_db()
    yield
    if os.path.exists(db_path):
        os.remove(db_path)


def test_analysis_log_table_exists(tmp_db):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_log'"
    ).fetchone()
    conn.close()
    assert row is not None, "analysis_log table should exist after init_db()"


def test_save_analysis_log(tmp_db):
    entry = {
        "game_date": "2026-04-11",
        "mlb_game_id": 999001,
        "game": "Away Team @ Home Team",
        "away_team": "Away Team",
        "home_team": "Home Team",
        "away_pitcher": "Pitcher A",
        "home_pitcher": "Pitcher B",
        "composite_score": 0.123,
        "ml_pick_team": "Home Team",
        "ml_win_probability": 58.5,
        "ml_confidence": 6,
        "ou_pick": "under",
        "ou_line": 8.5,
        "ou_confidence": 7,
    }
    row_id = db.save_analysis_log(entry)
    assert row_id > 0

    conn = db.get_connection()
    row = conn.execute("SELECT * FROM analysis_log WHERE id=?", (row_id,)).fetchone()
    conn.close()
    assert row["mlb_game_id"] == 999001
    assert row["ml_pick_team"] == "Home Team"
    assert row["ou_pick"] == "under"
    assert row["ml_status"] == "pending"
    assert row["ou_status"] == "pending"
