import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db
from datetime import date, timedelta


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()


def _insert_start(mlb_game_id, pitcher_id, opponent_team_id, game_date, ip, er, k, bb, h):
    conn = db.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO pitcher_game_logs
        (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, opponent_team_id,
         is_starter, innings_pitched, earned_runs, strikeouts, walks, hits, home_runs)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (mlb_game_id, game_date, pitcher_id, f"Pitcher {pitcher_id}",
          999, opponent_team_id, 1, ip, er, k, bb, h, 0))
    conn.commit()
    conn.close()


def test_returns_none_when_fewer_than_two_starts():
    """Returns None with only 1 start vs opponent."""
    _insert_start(1001, pitcher_id=10, opponent_team_id=20,
                  game_date="2026-01-01", ip=6.0, er=2, k=5, bb=2, h=6)
    result = db.get_pitcher_vs_team_history(10, 20)
    assert result is None


def test_returns_stats_with_two_or_more_starts():
    """Returns correct ERA/WHIP/K9 with 2 starts."""
    # Start 1: 6 IP, 2 ER, 6 K, 2 BB, 5 H
    _insert_start(1001, pitcher_id=10, opponent_team_id=20,
                  game_date="2026-01-01", ip=6.0, er=2, k=6, bb=2, h=5)
    # Start 2: 7 IP, 1 ER, 8 K, 1 BB, 4 H
    _insert_start(1002, pitcher_id=10, opponent_team_id=20,
                  game_date="2026-02-01", ip=7.0, er=1, k=8, bb=1, h=4)

    result = db.get_pitcher_vs_team_history(10, 20)
    assert result is not None
    assert result["starts"] == 2
    # ERA: (2+1)*9 / 13.0 = 2.08
    assert abs(result["era_vs_team"] - round(3 * 9 / 13.0, 2)) < 0.01
    # WHIP: (5+4+2+1) / 13.0
    assert abs(result["whip_vs_team"] - round(12 / 13.0, 3)) < 0.001
    # K9: (6+8)*9 / 13.0
    assert abs(result["k9_vs_team"] - round(14 * 9 / 13.0, 2)) < 0.01
    assert abs(result["avg_ip"] - round(13.0 / 2, 2)) < 0.01


def test_ignores_relief_appearances():
    """is_starter=0 rows are not counted."""
    # One relief appearance
    conn = db.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO pitcher_game_logs
        (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, opponent_team_id,
         is_starter, innings_pitched, earned_runs, strikeouts, walks, hits, home_runs)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (2001, "2026-01-01", 10, "Pitcher 10", 999, 20, 0, 2.0, 1, 2, 1, 3, 0))
    conn.commit()
    conn.close()
    # One start
    _insert_start(2002, pitcher_id=10, opponent_team_id=20,
                  game_date="2026-02-01", ip=6.0, er=2, k=6, bb=2, h=5)

    result = db.get_pitcher_vs_team_history(10, 20)
    # Only 1 start counted — relief appearance excluded
    assert result is None


def test_ignores_games_outside_days_window():
    """Starts older than the days window are excluded."""
    old_date = (date.today() - timedelta(days=400)).isoformat()
    _insert_start(3001, pitcher_id=10, opponent_team_id=20,
                  game_date=old_date, ip=6.0, er=2, k=5, bb=2, h=6)
    _insert_start(3002, pitcher_id=10, opponent_team_id=20,
                  game_date=old_date, ip=6.0, er=2, k=5, bb=2, h=6)

    result = db.get_pitcher_vs_team_history(10, 20, days=365)
    assert result is None


def test_does_not_mix_opponents():
    """Stats for opponent 20 and 30 are kept separate."""
    _insert_start(4001, pitcher_id=10, opponent_team_id=20,
                  game_date="2026-01-01", ip=6.0, er=0, k=9, bb=1, h=3)
    _insert_start(4002, pitcher_id=10, opponent_team_id=30,
                  game_date="2026-02-01", ip=5.0, er=5, k=3, bb=4, h=8)
    _insert_start(4003, pitcher_id=10, opponent_team_id=30,
                  game_date="2026-03-01", ip=5.0, er=5, k=3, bb=4, h=8)

    result_vs_20 = db.get_pitcher_vs_team_history(10, 20)
    result_vs_30 = db.get_pitcher_vs_team_history(10, 30)

    assert result_vs_20 is None  # only 1 start vs team 20
    assert result_vs_30 is not None
    assert result_vs_30["starts"] == 2


def test_score_pitching_applies_matchup_bonus_when_sp_dominates():
    """score_pitching adjusts score down for away team when away SP dominates home lineup."""
    import analysis

    # Away SP has good season ERA 3.50, but only 2.00 ERA vs this home team (dominates)
    # era_vt < away_era - 0.75 → 2.00 < 3.50 - 0.75 = 2.75 → True → score -= 0.06
    away_pitcher_id = 55
    home_team_id = 77
    _insert_start(5001, pitcher_id=away_pitcher_id, opponent_team_id=home_team_id,
                  game_date="2026-01-01", ip=7.0, er=2, k=8, bb=1, h=5)
    _insert_start(5002, pitcher_id=away_pitcher_id, opponent_team_id=home_team_id,
                  game_date="2026-02-01", ip=7.0, er=1, k=9, bb=1, h=4)

    game = {
        "away_pitcher_id": away_pitcher_id,
        "home_pitcher_id": None,
        "away_team_mlb_id": 66,
        "home_team_mlb_id": home_team_id,
        "away_pitcher_stats": {"era": 3.50, "whip": 1.20, "k_per_9": 9.0, "bb_per_9": 2.5, "k_bb_ratio": 3.6, "throws": "R"},
        "home_pitcher_stats": {"era": 3.50, "whip": 1.20, "k_per_9": 9.0, "bb_per_9": 2.5, "k_bb_ratio": 3.6, "throws": "R"},
        "away_pitcher_rolling": {},
        "home_pitcher_rolling": {},
        "away_pitcher_splits": {},
        "home_pitcher_splits": {},
        "away_batting": {"strikeouts": 1200, "at_bats": 5000},
        "home_batting": {"strikeouts": 1200, "at_bats": 5000},
    }

    result = analysis.score_pitching(game)
    # Away SP dominates home lineup → score should be negative (favors away = lowers home score)
    assert result["score"] < 0
    assert "dominates" in result["edge"]
