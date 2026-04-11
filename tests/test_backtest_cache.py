"""Tests for backtest_cache.py — SQLite cache layer."""
import os
import sys
import sqlite3
import tempfile
import json
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def tmp_cache(tmp_path):
    """Return a BacktestCache pointed at a temp db."""
    import backtest_cache
    db_path = str(tmp_path / "test_cache.db")
    cache = backtest_cache.BacktestCache(db_path=db_path)
    yield cache
    cache.close()


def test_init_creates_tables(tmp_cache):
    conn = sqlite3.connect(tmp_cache.db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "season_games" in tables
    assert "team_stats" in tables
    assert "pitcher_stats" in tables
    assert "statcast_batting" in tables
    assert "statcast_pitching" in tables
    assert "statcast_pitchers" in tables


def test_save_and_load_season_games(tmp_cache):
    games = [
        {"mlb_game_id": 1001, "season": 2024, "game_date": "2024-04-01",
         "away_team_id": 147, "away_team_name": "NY Yankees", "away_team_abbr": "NYY",
         "home_team_id": 111, "home_team_name": "Boston Red Sox", "home_team_abbr": "BOS",
         "away_score": 3, "home_score": 5, "home_team_won": True,
         "away_pitcher_id": 5001, "away_pitcher_name": "Gerrit Cole",
         "home_pitcher_id": 5002, "home_pitcher_name": "Nick Pivetta",
         "venue_id": 3, "venue_name": "Fenway Park"},
    ]
    tmp_cache.save_season_games(2024, games)
    loaded = tmp_cache.load_season_games(2024)
    assert len(loaded) == 1
    assert loaded[0]["mlb_game_id"] == 1001
    assert loaded[0]["home_team_won"] is True


def test_save_and_load_team_stats(tmp_cache):
    stats = {"era": 3.85, "whip": 1.21, "k_per_9": 9.1}
    tmp_cache.save_team_stats(2024, 147, "batting", stats)
    loaded = tmp_cache.load_team_stats(2024, 147, "batting")
    assert loaded["era"] == 3.85


def test_save_and_load_pitcher_stats(tmp_cache):
    stats = {"era": 2.90, "whip": 1.05, "k_per_9": 11.2}
    tmp_cache.save_pitcher_stats(2024, 5001, stats)
    loaded = tmp_cache.load_pitcher_stats(2024, 5001)
    assert loaded["era"] == 2.90


def test_load_missing_returns_none(tmp_cache):
    assert tmp_cache.load_team_stats(2024, 9999, "batting") is None
    assert tmp_cache.load_pitcher_stats(2024, 9999) is None
    assert tmp_cache.load_season_games(2024) == []


def test_save_and_load_statcast_batting(tmp_cache):
    data = {"NYY": {"xwoba": 0.330, "woba_diff": -0.012}}
    tmp_cache.save_statcast_batting(2024, data)
    loaded = tmp_cache.load_statcast_batting(2024)
    assert loaded["NYY"]["xwoba"] == 0.330


def test_season_games_cached_flag(tmp_cache):
    assert not tmp_cache.is_season_games_cached(2024)
    tmp_cache.save_season_games(2024, [{"mlb_game_id": 1, "season": 2024, "game_date": "2024-04-01",
         "away_team_id": 1, "away_team_name": "A", "away_team_abbr": "A",
         "home_team_id": 2, "home_team_name": "B", "home_team_abbr": "B",
         "away_score": 1, "home_score": 2, "home_team_won": True,
         "away_pitcher_id": None, "away_pitcher_name": "TBD",
         "home_pitcher_id": None, "home_pitcher_name": "TBD",
         "venue_id": 1, "venue_name": "Park"}])
    assert tmp_cache.is_season_games_cached(2024)


# Tests carried over from Tasks 2 and 3
def test_fetch_pitcher_stats_uses_season_param():
    """fetch_pitcher_stats should use provided season, not always SEASON_YEAR."""
    import data_mlb
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"people": [{"fullName": "Test Pitcher", "pitchHand": {"code": "R"}, "stats": [{"splits": [{"stat": {"era": "3.50", "whip": "1.15", "strikeoutsPer9Inn": "9.0", "walksPer9Inn": "2.5", "inningsPitched": "150", "gamesStarted": 25, "wins": 12, "losses": 8, "avg": ".230", "obp": ".290", "slg": ".380", "homeRuns": 15, "strikeOuts": 150, "baseOnBalls": 45}}]}]}]}
    mock_resp.raise_for_status = MagicMock()
    captured_url = {}

    def fake_get(url, **kwargs):
        captured_url['url'] = url
        return mock_resp

    with patch('requests.get', side_effect=fake_get):
        result = data_mlb.fetch_pitcher_stats(12345, season=2024)

    assert '2024' in captured_url['url']
    assert result.get('era') == 3.50


def test_fetch_statcast_team_batting_uses_season_cache_key():
    """fetch_statcast_team_batting(season=2024) should cache under sc_bat_2024, not today's date."""
    import data_mlb
    data_mlb._statcast_cache.clear()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.content = b"player_name,xwoba,woba,wobadiff,hardhit_percent,barrels_per_bbe_percent,launch_speed\nNYY,0.320,0.315,-0.005,45.2,8.1,89.3\n"

    with patch('requests.get', return_value=mock_resp) as mock_get:
        data_mlb.fetch_statcast_team_batting(season=2024)
        first_call_count = mock_get.call_count
        data_mlb.fetch_statcast_team_batting(season=2024)
        assert mock_get.call_count == first_call_count

    from datetime import date
    today = date.today().isoformat()
    assert f"sc_bat_{today}" not in data_mlb._statcast_cache
    assert "sc_bat_2024" in data_mlb._statcast_cache
    data_mlb._statcast_cache.clear()


def test_fetch_season_schedule_returns_completed_games():
    import data_mlb
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "dates": [{"date": "2024-04-01", "games": [
            {"gamePk": 1001, "status": {"detailedState": "Final"},
             "teams": {
                 "away": {"team": {"id": 147, "name": "NY Yankees", "abbreviation": "NYY"}, "score": 3, "probablePitcher": {"id": 5001, "fullName": "Gerrit Cole"}},
                 "home": {"team": {"id": 111, "name": "Boston Red Sox", "abbreviation": "BOS"}, "score": 5, "probablePitcher": {"id": 5002, "fullName": "Nick Pivetta"}},
             }, "venue": {"id": 3, "name": "Fenway Park"}},
            {"gamePk": 1002, "status": {"detailedState": "Postponed"},
             "teams": {
                 "away": {"team": {"id": 147, "name": "NY Yankees", "abbreviation": "NYY"}, "score": None, "probablePitcher": {}},
                 "home": {"team": {"id": 111, "name": "Boston Red Sox", "abbreviation": "BOS"}, "score": None, "probablePitcher": {}},
             }, "venue": {"id": 3, "name": "Fenway Park"}},
        ]}]
    }
    with patch('requests.get', return_value=mock_resp):
        games = data_mlb.fetch_season_schedule(2024)
    assert len(games) == 1
    assert games[0]["home_team_won"] is True
