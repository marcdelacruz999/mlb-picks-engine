import pytest
import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as _db


def test_kelly_stake_favorite_high_confidence():
    from analysis import kelly_stake
    # -150 odds (team is favorite), model says 65% win prob
    result = kelly_stake(65.0, -150)
    assert 0.25 <= result <= 2.0


def test_kelly_stake_underdog_edge():
    from analysis import kelly_stake
    # +130 odds, model says 55% win prob
    result = kelly_stake(55.0, 130)
    assert result >= 0.25


def test_kelly_stake_strong_edge_bigger_stake():
    from analysis import kelly_stake
    # Model says 75%, odds +110 (big edge)
    result = kelly_stake(75.0, 110)
    assert result >= 0.25
    # Strong edge should produce more than weak edge
    result_weak = kelly_stake(52.0, -110)
    assert result > result_weak


def test_kelly_stake_capped_at_2x():
    from analysis import kelly_stake
    result = kelly_stake(95.0, 200)
    assert result <= 2.0


def test_kelly_stake_no_odds_returns_1x():
    from analysis import kelly_stake
    result = kelly_stake(62.0, None)
    assert result == 1.0


def test_discord_message_includes_kelly_stake():
    from discord_bot import _format_pick_message
    pick = {
        "game": "Yankees @ Red Sox",
        "pick_team": "Red Sox",
        "pick_type": "moneyline",
        "confidence": 8,
        "win_probability": 65.0,
        "kelly_fraction": 0.52,
        "ev_score": 0.045,
        "projected_away_score": 3.5,
        "projected_home_score": 4.2,
        "away_team": "Yankees",
        "home_team": "Red Sox",
        "game_time_utc": "",
        "analysis": {},
        "notes": "Lineups confirmed",
    }
    msg = _format_pick_message(pick)
    assert "0.52x units" in msg


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db, "DB_PATH", db_path)
    _db.init_db()
    return db_path


def test_pitcher_game_logs_has_opponent_team_id_column(fresh_db):
    conn = sqlite3.connect(fresh_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pitcher_game_logs)")]
    conn.close()
    assert "opponent_team_id" in cols


def test_store_boxscores_saves_opponent_team_id(fresh_db):
    pitcher_logs = [{
        "mlb_game_id": 99001,
        "game_date": "2026-04-12",
        "pitcher_id": 501,
        "pitcher_name": "Test Pitcher",
        "team_id": 10,
        "is_starter": True,
        "opponent_team_id": 20,
        "innings_pitched": 6.0,
        "earned_runs": 2,
        "strikeouts": 7,
        "walks": 1,
        "hits": 5,
        "home_runs": 0,
    }]
    _db.store_boxscores(pitcher_logs, [])
    conn = sqlite3.connect(fresh_db)
    row = conn.execute("SELECT opponent_team_id FROM pitcher_game_logs WHERE pitcher_id=501").fetchone()
    conn.close()
    assert row[0] == 20


def test_get_pitcher_rolling_stats_adjusted_returns_same_when_no_opponent_data(fresh_db):
    """When opponent_team_id is NULL, adjusted should return valid data."""
    conn = sqlite3.connect(fresh_db)
    conn.execute("""
        INSERT OR IGNORE INTO pitcher_game_logs
        (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
         opponent_team_id, innings_pitched, earned_runs, strikeouts, walks, hits, home_runs)
        VALUES (?,?,?,?,?,1,NULL,?,?,?,?,?,?)
    """, (77001, "2026-04-11", 601, "Test SP", 5, 6.0, 2, 8, 2, 5, 0))
    conn.commit()
    conn.close()

    today = "2026-04-12"
    plain = _db.get_pitcher_rolling_stats(601, days=21, as_of_date=today)
    adjusted = _db.get_pitcher_rolling_stats_adjusted(601, days=21, as_of_date=today)
    # Both should return valid ERA when no opponent data
    assert plain is not None
    assert adjusted is not None
    assert plain["era"] == adjusted["era"]


def test_get_pitcher_rolling_stats_adjusted_returns_valid_data_with_opponent(fresh_db):
    """Adjusted stats function returns valid ERA > 0 when data exists."""
    conn = sqlite3.connect(fresh_db)
    # Insert opponent team batting logs (team 30 — strong offense)
    for i, gd in enumerate(["2026-04-06", "2026-04-07", "2026-04-08",
                             "2026-04-09", "2026-04-10", "2026-04-11"]):
        conn.execute("""
            INSERT OR IGNORE INTO team_game_logs
            (mlb_game_id, game_date, team_id, is_away, runs, hits, home_runs,
             strikeouts, walks, at_bats, left_on_base)
            VALUES (?,?,30,0,7,9,2,8,3,35,5)
        """, (30000 + i, gd))

    # Pitcher 700: 3 ER in 6 IP vs opponent 30
    conn.execute("""
        INSERT OR IGNORE INTO pitcher_game_logs
        (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
         opponent_team_id, innings_pitched, earned_runs, strikeouts, walks, hits, home_runs)
        VALUES (70001, '2026-04-10', 700, 'SP Test', 5, 1, 30, 6.0, 3, 7, 2, 5, 0)
    """)
    conn.commit()
    conn.close()

    adjusted = _db.get_pitcher_rolling_stats_adjusted(700, days=21, as_of_date="2026-04-12")
    assert adjusted is not None
    assert "era" in adjusted
    assert adjusted["era"] > 0
