"""Tests for analyze_agent_signals() using real numeric scores from analysis_log."""
import sqlite3
import tempfile
import os
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _make_test_db():
    """Create a temp DB with the tables optimizer needs."""
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.close()
    conn = sqlite3.connect(db.name)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE games (
            id INTEGER PRIMARY KEY,
            mlb_game_id INTEGER,
            game_date TEXT,
            status TEXT DEFAULT 'Final'
        );
        CREATE TABLE picks (
            id INTEGER PRIMARY KEY,
            game_id INTEGER,
            pick_type TEXT,
            pick_team TEXT,
            status TEXT,
            discord_sent INTEGER DEFAULT 1
        );
        CREATE TABLE analysis_log (
            id INTEGER PRIMARY KEY,
            mlb_game_id INTEGER,
            game_date TEXT,
            score_pitching REAL,
            score_offense REAL,
            score_bullpen REAL,
            score_advanced REAL,
            score_momentum REAL,
            score_weather REAL,
            score_market REAL
        );
    """)
    return db.name, conn


def _insert_game(conn, game_id, mlb_game_id, game_date):
    conn.execute(
        "INSERT INTO games VALUES (?,?,?,'Final')",
        (game_id, mlb_game_id, game_date)
    )


def _insert_pick(conn, pick_id, game_id, status):
    conn.execute(
        "INSERT INTO picks VALUES (?,?,'moneyline','Home',?,1)",
        (pick_id, game_id, status)
    )


def _insert_analysis(conn, mlb_game_id, game_date, scores):
    conn.execute(
        """INSERT INTO analysis_log
           (mlb_game_id, game_date, score_pitching, score_offense,
            score_bullpen, score_advanced, score_momentum, score_weather, score_market)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (mlb_game_id, game_date, scores["pitching"], scores["offense"],
         scores["bullpen"], scores["advanced"], scores.get("momentum", 0.0),
         scores.get("weather", 0.0), scores.get("market", 0.0))
    )


def test_differential_uses_analysis_log_scores(monkeypatch):
    """analyze_agent_signals should compute avg score on won vs lost from analysis_log."""
    db_name, conn = _make_test_db()
    today = date.today().isoformat()

    for i, (status, pitch_score) in enumerate([
        ("won", 0.8), ("won", 0.6), ("lost", 0.1), ("lost", 0.2)
    ], start=1):
        _insert_game(conn, i, 1000 + i, today)
        _insert_pick(conn, i, i, status)
        _insert_analysis(conn, 1000 + i, today, {
            "pitching": pitch_score, "offense": 0.3,
            "bullpen": 0.3, "advanced": 0.1
        })
    conn.commit()

    import optimizer
    monkeypatch.setattr(optimizer, "DATABASE_PATH", db_name)
    monkeypatch.setattr(optimizer, "load_backtest_lift", lambda: {
        "pitching": 0.08, "offense": 0.04, "bullpen": 0.05,
        "advanced": -0.01, "momentum": None, "weather": None, "market": None
    })

    result = optimizer.analyze_agent_signals(days=60)
    conn.close()
    os.unlink(db_name)

    # avg_won_pitching = (0.8+0.6)/2 = 0.7; avg_lost = (0.1+0.2)/2 = 0.15; diff = 0.55
    assert "pitching" in result
    assert abs(result["pitching"]["live_differential"] - 0.55) < 0.001, (
        f"Expected ~0.55, got {result['pitching']['live_differential']}"
    )


def test_no_garbage_from_text_extraction(monkeypatch):
    """Live differential must not be in range ±3 — that was the bug symptom."""
    db_name, conn = _make_test_db()
    today = date.today().isoformat()

    for i, status in enumerate(["won"] * 8 + ["lost"] * 5, start=1):
        _insert_game(conn, i, 2000 + i, today)
        _insert_pick(conn, i, i, status)
        _insert_analysis(conn, 2000 + i, today, {
            "pitching": 0.4 if status == "won" else 0.2,
            "offense": 0.3, "bullpen": 0.25, "advanced": 0.05
        })
    conn.commit()

    import optimizer
    monkeypatch.setattr(optimizer, "DATABASE_PATH", db_name)
    monkeypatch.setattr(optimizer, "load_backtest_lift", lambda: {
        "pitching": 0.08, "offense": 0.04, "bullpen": 0.05,
        "advanced": -0.01, "momentum": None, "weather": None, "market": None
    })

    result = optimizer.analyze_agent_signals(days=60)
    conn.close()
    os.unlink(db_name)

    assert abs(result["pitching"]["live_differential"] - 0.20) < 0.001, (
        f"Expected pitching live_differential ~0.20, got {result['pitching']['live_differential']}"
    )
    # Also verify no agent has a suspiciously large value
    for agent, data in result.items():
        live = data["live_differential"]
        assert abs(live) <= 0.5, (
            f"Agent '{agent}' live_differential={live:.3f} out of expected range for this test data"
        )


def test_returns_zero_differential_when_no_graded_picks(monkeypatch):
    """With no graded picks, all differentials should be 0.0."""
    db_name, conn = _make_test_db()
    conn.commit()

    import optimizer
    monkeypatch.setattr(optimizer, "DATABASE_PATH", db_name)
    monkeypatch.setattr(optimizer, "load_backtest_lift", lambda: {
        "pitching": 0.08, "offense": 0.04, "bullpen": 0.05,
        "advanced": -0.01, "momentum": None, "weather": None, "market": None
    })

    result = optimizer.analyze_agent_signals(days=60)
    conn.close()
    os.unlink(db_name)

    assert result["pitching"]["live_differential"] == 0.0
    assert result["pitching"]["n_won"] == 0
    assert result["pitching"]["n_lost"] == 0
