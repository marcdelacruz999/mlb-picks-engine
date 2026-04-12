import pytest
import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database as db
from datetime import date, timedelta


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()


def _insert_pitcher_log(pitcher_id, team_id, game_date, is_starter, ip, er, k, bb, h, hr=0):
    conn = db.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO pitcher_game_logs
        (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
         innings_pitched, earned_runs, strikeouts, walks, hits, home_runs)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (1000 + pitcher_id, game_date, pitcher_id, f"Pitcher {pitcher_id}",
          team_id, is_starter, ip, er, k, bb, h, hr))
    conn.commit()
    conn.close()


def _insert_team_log(team_id, game_date, runs, hits, hr, k, bb, ab):
    conn = db.get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO team_game_logs
        (mlb_game_id, game_date, team_id, is_away, runs, hits, home_runs,
         strikeouts, walks, at_bats, left_on_base)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (2000 + team_id, game_date, team_id, True, runs, hits, hr, k, bb, ab, 0))
    conn.commit()
    conn.close()


def test_pitcher_game_logs_table_exists():
    conn = db.get_connection()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pitcher_game_logs)")]
    conn.close()
    assert "pitcher_id" in cols
    assert "innings_pitched" in cols
    assert "earned_runs" in cols
    assert "strikeouts" in cols
    assert "walks" in cols
    assert "is_starter" in cols


def test_team_game_logs_table_exists():
    conn = db.get_connection()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(team_game_logs)")]
    conn.close()
    assert "team_id" in cols
    assert "runs" in cols
    assert "hits" in cols
    assert "strikeouts" in cols
    assert "at_bats" in cols


def test_get_pitcher_rolling_stats_basic():
    today = date.today()
    for i in range(4):
        _insert_pitcher_log(
            pitcher_id=100,
            team_id=1,
            game_date=(today - timedelta(days=i * 5 + 1)).isoformat(),
            is_starter=True, ip=6.0, er=2, k=7, bb=2, h=5
        )
    result = db.get_pitcher_rolling_stats(100, days=21)
    assert result is not None
    assert result["games"] == 4
    # ERA = 2 ER / 6.0 IP * 9 = 3.00
    assert abs(result["era"] - 3.0) < 0.01
    # WHIP = (5 H + 2 BB) / 6.0 IP = 1.167
    assert abs(result["whip"] - (7 / 6.0)) < 0.01


def test_get_pitcher_rolling_stats_returns_none_for_no_data():
    result = db.get_pitcher_rolling_stats(999, days=21)
    assert result is None


def test_get_pitcher_rolling_stats_filters_by_days():
    today = date.today()
    # Game 25 days ago — outside 21-day window
    _insert_pitcher_log(100, 1, (today - timedelta(days=25)).isoformat(),
                        True, 6.0, 5, 5, 4, 8)
    # Game 10 days ago — inside window (different pitcher_id to avoid UNIQUE collision)
    _insert_pitcher_log(101, 1, (today - timedelta(days=10)).isoformat(),
                        True, 7.0, 1, 9, 1, 4)
    result = db.get_pitcher_rolling_stats(101, days=21)
    assert result is not None
    assert result["games"] == 1


def test_get_team_batting_rolling_basic():
    today = date.today()
    for i in range(5):
        _insert_team_log(
            team_id=10,
            game_date=(today - timedelta(days=i + 1)).isoformat(),
            runs=5, hits=9, hr=1, k=8, bb=3, ab=30
        )
    result = db.get_team_batting_rolling(10, days=14)
    assert result is not None
    assert result["games"] == 5
    assert abs(result["rpg"] - 5.0) < 0.01
    # OBP proxy = (H + BB) / (AB + BB) = (9+3)/(30+3) = 0.364
    assert abs(result["obp_proxy"] - (12 / 33)) < 0.01


def test_get_team_batting_rolling_returns_none_for_no_data():
    result = db.get_team_batting_rolling(999, days=14)
    assert result is None


def test_get_team_bullpen_rolling_basic():
    today = date.today()
    # 3 relief appearances across 3 different game_ids
    for i in range(3):
        conn = db.get_connection()
        conn.execute("""
            INSERT OR IGNORE INTO pitcher_game_logs
            (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
             innings_pitched, earned_runs, strikeouts, walks, hits, home_runs)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (3000 + i, (today - timedelta(days=i + 1)).isoformat(),
              200 + i, f"Reliever {i}", 20, False, 1.0, 0, 1, 0, 1, 0))
        conn.commit()
        conn.close()
    result = db.get_team_bullpen_rolling(20, days=14)
    assert result is not None
    assert result["games"] == 3
    # 0 ER over 3 IP → ERA 0.0
    assert result["era"] == 0.0


from unittest.mock import patch, MagicMock
import data_mlb


def _make_boxscore_response():
    """Minimal MLB API schedule+boxscore response for one game."""
    return {
        "dates": [{
            "date": "2026-04-11",
            "games": [{
                "gamePk": 823480,
                "status": {"abstractGameState": "Final"},
                "teams": {
                    "away": {
                        "team": {"id": 109},
                        "pitchers": [662253, 681911],
                        "players": {
                            "ID662253": {
                                "person": {"id": 662253, "fullName": "Zac Gallen"},
                                "stats": {"pitching": {
                                    "inningsPitched": "6.2", "earnedRuns": 2,
                                    "strikeOuts": 7, "baseOnBalls": 2,
                                    "hits": 5, "homeRuns": 1
                                }}
                            },
                            "ID681911": {
                                "person": {"id": 681911, "fullName": "Joe Mantiply"},
                                "stats": {"pitching": {
                                    "inningsPitched": "1.1", "earnedRuns": 0,
                                    "strikeOuts": 1, "baseOnBalls": 0,
                                    "hits": 1, "homeRuns": 0
                                }}
                            }
                        },
                        "teamStats": {
                            "batting": {
                                "runs": 3, "hits": 7, "homeRuns": 1,
                                "strikeOuts": 9, "baseOnBalls": 2,
                                "leftOnBase": 6, "atBats": 30
                            }
                        }
                    },
                    "home": {
                        "team": {"id": 143},
                        "pitchers": [669302],
                        "players": {
                            "ID669302": {
                                "person": {"id": 669302, "fullName": "Ranger Suarez"},
                                "stats": {"pitching": {
                                    "inningsPitched": "9.0", "earnedRuns": 3,
                                    "strikeOuts": 8, "baseOnBalls": 1,
                                    "hits": 7, "homeRuns": 0
                                }}
                            }
                        },
                        "teamStats": {
                            "batting": {
                                "runs": 4, "hits": 8, "homeRuns": 0,
                                "strikeOuts": 7, "baseOnBalls": 3,
                                "leftOnBase": 8, "atBats": 31
                            }
                        }
                    }
                }
            }]
        }]
    }


def test_collect_boxscores_returns_pitcher_and_team_logs():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = _make_boxscore_response()

    with patch("data_mlb.requests.get", return_value=mock_resp):
        result = data_mlb.collect_boxscores("2026-04-11")

    assert "pitcher_logs" in result
    assert "team_logs" in result

    pitcher_ids = [p["pitcher_id"] for p in result["pitcher_logs"]]
    assert 662253 in pitcher_ids  # Zac Gallen (starter)
    assert 681911 in pitcher_ids  # Joe Mantiply (reliever)
    assert 669302 in pitcher_ids  # Ranger Suarez (starter)

    # Gallen: is_starter=True
    gallen = next(p for p in result["pitcher_logs"] if p["pitcher_id"] == 662253)
    assert gallen["is_starter"] is True
    assert abs(gallen["innings_pitched"] - 6.667) < 0.01  # 6.2 = 6 + 2/3
    assert gallen["earned_runs"] == 2
    assert gallen["strikeouts"] == 7

    # Mantiply: is_starter=False
    mantiply = next(p for p in result["pitcher_logs"] if p["pitcher_id"] == 681911)
    assert mantiply["is_starter"] is False

    # Team logs: 2 teams
    team_ids = [t["team_id"] for t in result["team_logs"]]
    assert 109 in team_ids  # away team
    assert 143 in team_ids  # home team

    away_log = next(t for t in result["team_logs"] if t["team_id"] == 109)
    assert away_log["is_away"] is True
    assert away_log["runs"] == 3
    assert away_log["at_bats"] == 30


def test_collect_boxscores_skips_non_final_games():
    response = _make_boxscore_response()
    response["dates"][0]["games"][0]["status"]["abstractGameState"] = "Live"

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = response

    with patch("data_mlb.requests.get", return_value=mock_resp):
        result = data_mlb.collect_boxscores("2026-04-11")

    assert result["pitcher_logs"] == []
    assert result["team_logs"] == []


def test_collect_boxscores_handles_api_error():
    with patch("data_mlb.requests.get", side_effect=Exception("timeout")):
        result = data_mlb.collect_boxscores("2026-04-11")
    assert result == {"pitcher_logs": [], "team_logs": []}


import analysis


def test_blend_uses_season_when_no_rolling():
    result = analysis._blend(season_val=4.50, rolling_val=None, rolling_games=0)
    assert result == 4.50


def test_blend_season_only_below_threshold():
    # < 5 games → use season only
    result = analysis._blend(season_val=4.50, rolling_val=2.10, rolling_games=3)
    assert result == 4.50


def test_blend_weighted_5_to_9_games():
    # 5-9 games → 40% rolling, 60% season
    result = analysis._blend(season_val=4.50, rolling_val=2.50, rolling_games=7)
    expected = 0.4 * 2.50 + 0.6 * 4.50
    assert abs(result - expected) < 0.001


def test_blend_weighted_10_to_19_games():
    # 10-19 games → 60% rolling, 40% season
    result = analysis._blend(season_val=4.50, rolling_val=2.50, rolling_games=12)
    expected = 0.6 * 2.50 + 0.4 * 4.50
    assert abs(result - expected) < 0.001


def test_blend_weighted_20_plus_games():
    # ≥ 20 games → 75% rolling, 25% season
    result = analysis._blend(season_val=4.50, rolling_val=2.50, rolling_games=25)
    expected = 0.75 * 2.50 + 0.25 * 4.50
    assert abs(result - expected) < 0.001


def test_score_pitching_uses_rolling_when_available():
    game = {
        "away_pitcher_stats": {
            "name": "Away SP", "throws": "R", "era": 5.00, "whip": 1.50,
            "k_per_9": 7.0, "bb_per_9": 3.5, "k_bb_ratio": 2.0,
            "days_rest": 4,
        },
        "home_pitcher_stats": {
            "name": "Home SP", "throws": "R", "era": 5.00, "whip": 1.50,
            "k_per_9": 7.0, "bb_per_9": 3.5, "k_bb_ratio": 2.0,
            "days_rest": 4,
        },
        "away_batting": {"ops": 0.720, "obp": 0.320, "slg": 0.400,
                         "runs": 80, "games_played": 20, "strikeouts": 150, "at_bats": 600},
        "home_batting": {"ops": 0.720, "obp": 0.320, "slg": 0.400,
                         "runs": 80, "games_played": 20, "strikeouts": 150, "at_bats": 600},
        # Away pitcher rolling: much better ERA/WHIP → should push score toward away (negative)
        "away_pitcher_rolling": {"era": 1.50, "whip": 0.80, "k9": 10.0, "bb9": 1.5, "games": 20},
        "home_pitcher_rolling": None,
    }
    result = analysis.score_pitching(game)
    # Away pitcher has rolling ERA 1.50 blended vs season 5.00 → away should have edge (negative score)
    assert result["score"] < 0.0


def test_score_offense_uses_rolling_when_available():
    game = {
        "away_batting": {"ops": 0.700, "obp": 0.310, "slg": 0.390,
                         "runs": 80, "games_played": 20,
                         "strikeouts": 150, "at_bats": 600},
        "home_batting": {"ops": 0.700, "obp": 0.310, "slg": 0.390,
                         "runs": 80, "games_played": 20,
                         "strikeouts": 150, "at_bats": 600},
        "away_pitcher_stats": {}, "home_pitcher_stats": {},
        # Away team rolling much better — should push score toward away edge (negative)
        "away_batting_rolling": {"rpg": 7.0, "obp_proxy": 0.380,
                                  "hr_pg": 1.5, "k_pct": 0.18, "games": 20},
        "home_batting_rolling": {"rpg": 3.0, "obp_proxy": 0.280,
                                  "hr_pg": 0.5, "k_pct": 0.28, "games": 20},
    }
    result = analysis.score_offense(game)
    # Away much better rolling → negative score (away edge)
    assert result["score"] < 0.0
