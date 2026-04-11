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


def test_get_today_analysis_log(tmp_db):
    entry = {
        "game_date": "2026-04-11",
        "mlb_game_id": 999002,
        "game": "A @ B",
        "away_team": "A", "home_team": "B",
        "away_pitcher": "P1", "home_pitcher": "P2",
        "composite_score": 0.05,
        "ml_pick_team": "B",
        "ml_win_probability": 54.0,
        "ml_confidence": 4,
        "ou_pick": None, "ou_line": None, "ou_confidence": None,
    }
    db.save_analysis_log(entry)
    rows = db.get_today_analysis_log()
    assert len(rows) == 1
    assert rows[0]["mlb_game_id"] == 999002


def test_update_analysis_log_result(tmp_db):
    entry = {
        "game_date": "2026-04-11",
        "mlb_game_id": 999003,
        "game": "C @ D",
        "away_team": "C", "home_team": "D",
        "away_pitcher": "P3", "home_pitcher": "P4",
        "composite_score": 0.20,
        "ml_pick_team": "D",
        "ml_win_probability": 61.0,
        "ml_confidence": 7,
        "ou_pick": "over", "ou_line": 8.0, "ou_confidence": 8,
    }
    row_id = db.save_analysis_log(entry)
    db.update_analysis_log_result(row_id, ml_status="correct", ou_status="incorrect",
                                   actual_away=3, actual_home=5, actual_total=8)
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM analysis_log WHERE id=?", (row_id,)).fetchone()
    conn.close()
    assert row["ml_status"] == "correct"
    assert row["ou_status"] == "incorrect"
    assert row["actual_away_score"] == 3
    assert row["actual_home_score"] == 5
    assert row["actual_total"] == 8


def test_run_results_grades_analysis_log(tmp_db, monkeypatch):
    import engine
    from datetime import date as dt

    log_id = db.save_analysis_log({
        "game_date": dt.today().isoformat(),
        "mlb_game_id": 77001,
        "game": "Away @ Home",
        "away_team": "Away", "home_team": "Home",
        "away_pitcher": "P1", "home_pitcher": "P2",
        "composite_score": 0.15,
        "ml_pick_team": "Home",
        "ml_win_probability": 58.0,
        "ml_confidence": 6,
        "ou_pick": "over", "ou_line": 8.0, "ou_confidence": 7,
    })

    fake_final = [{
        "mlb_game_id": 77001,
        "away_team_name": "Away", "home_team_name": "Home",
        "status": "Final", "away_score": 3, "home_score": 5,
        "game_date": dt.today().isoformat(),
    }]

    monkeypatch.setattr(engine, "fetch_todays_games", lambda: fake_final)
    monkeypatch.setattr(db, "get_today_picks", lambda: [])

    engine.run_results()

    conn = db.get_connection()
    row = conn.execute("SELECT * FROM analysis_log WHERE id=?", (log_id,)).fetchone()
    conn.close()
    # Home won 5-3 → ml correct. Total=8 = line 8.0 → push
    assert row["ml_status"] == "correct"
    assert row["ou_status"] == "push"
    assert row["actual_total"] == 8
