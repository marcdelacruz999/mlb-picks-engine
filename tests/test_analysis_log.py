import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import date as dt
import database as db


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
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
        "game_date": dt.today().isoformat(),
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

    monkeypatch.setattr(engine, "fetch_todays_games", lambda d=None: fake_final)
    monkeypatch.setattr(db, "get_today_picks", lambda: [])
    monkeypatch.setattr(db, "collect_batter_boxscores", lambda d: 0)
    monkeypatch.setattr(engine, "_fetch_verified_score", lambda game_id: None)
    monkeypatch.setattr(engine, "send_nightly_report", lambda *a, **kw: False)

    import data_mlb
    monkeypatch.setattr(data_mlb, "collect_boxscores", lambda d: {"pitcher_logs": [], "team_logs": []})

    engine.run_results()

    conn = db.get_connection()
    row = conn.execute("SELECT * FROM analysis_log WHERE id=?", (log_id,)).fetchone()
    conn.close()
    # Home won 5-3 → ml correct. Total=8 = line 8.0 → push
    assert row["ml_status"] == "correct"
    assert row["ou_status"] == "push"
    assert row["actual_total"] == 8


def test_get_model_accuracy_summary(tmp_db):
    from datetime import date as dt
    today = dt.today().isoformat()

    for i, (ml_s, ou_s) in enumerate([
        ("correct", "correct"),
        ("correct", "incorrect"),
        ("incorrect", "none"),
        ("correct", "correct"),
    ]):
        log_id = db.save_analysis_log({
            "game_date": today,
            "mlb_game_id": 88000 + i,
            "game": f"A{i} @ B{i}",
            "away_team": f"A{i}", "home_team": f"B{i}",
            "away_pitcher": "P", "home_pitcher": "P",
            "composite_score": 0.1,
            "ml_pick_team": f"B{i}",
            "ml_win_probability": 55.0,
            "ml_confidence": 5,
            "ou_pick": "over" if ou_s != "none" else None,
            "ou_line": 8.0 if ou_s != "none" else None,
            "ou_confidence": 6 if ou_s != "none" else None,
        })
        db.update_analysis_log_result(log_id, ml_status=ml_s, ou_status=ou_s,
                                       actual_away=3, actual_home=5, actual_total=8)

    summary = db.get_model_accuracy_summary(30)
    assert summary["ml_correct"] == 3
    assert summary["ml_incorrect"] == 1
    assert summary["ml_total"] == 4
    assert summary["ml_accuracy"] == 75.0
    assert summary["ou_correct"] == 2
    assert summary["ou_incorrect"] == 1
