"""Tests for backtest_cache.py — SQLite cache layer."""
import os
import sys
import sqlite3
import tempfile
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_placeholder():
    """Placeholder so pytest can discover this file before backtest_cache exists."""
    assert True


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

    assert '2024' in captured_url['url'], f"Expected 2024 in URL, got: {captured_url['url']}"
    assert result.get('era') == 3.50


def test_fetch_statcast_team_batting_uses_season_cache_key():
    """fetch_statcast_team_batting(season=2024) should cache under sc_bat_2024, not today's date."""
    import data_mlb
    # Clear module-level cache
    data_mlb._statcast_cache.clear()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    # Return minimal CSV-like content
    import io
    mock_resp.content = b"player_name,xwoba,woba,wobadiff,hardhit_percent,barrels_per_bbe_percent,launch_speed\nNYY,0.320,0.315,-0.005,45.2,8.1,89.3\n"

    with patch('requests.get', return_value=mock_resp) as mock_get:
        data_mlb.fetch_statcast_team_batting(season=2024)
        first_call_count = mock_get.call_count

        # Second call with same season should hit cache, not make another request
        data_mlb.fetch_statcast_team_batting(season=2024)
        assert mock_get.call_count == first_call_count, "Second call should use cache, not re-fetch"

    # Verify cache key is season-based, not date-based
    from datetime import date
    today = date.today().isoformat()
    assert f"sc_bat_{today}" not in data_mlb._statcast_cache
    assert "sc_bat_2024" in data_mlb._statcast_cache

    # Clean up
    data_mlb._statcast_cache.clear()


def test_fetch_season_schedule_returns_completed_games():
    """fetch_season_schedule should return games with final scores only."""
    import data_mlb
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "dates": [
            {
                "date": "2024-04-01",
                "games": [
                    {
                        "gamePk": 1001,
                        "status": {"detailedState": "Final"},
                        "teams": {
                            "away": {"team": {"id": 147, "name": "NY Yankees", "abbreviation": "NYY"}, "score": 3, "probablePitcher": {"id": 5001, "fullName": "Gerrit Cole"}},
                            "home": {"team": {"id": 111, "name": "Boston Red Sox", "abbreviation": "BOS"}, "score": 5, "probablePitcher": {"id": 5002, "fullName": "Nick Pivetta"}},
                        },
                        "venue": {"id": 3, "name": "Fenway Park"},
                    },
                    {
                        "gamePk": 1002,
                        "status": {"detailedState": "Postponed"},
                        "teams": {
                            "away": {"team": {"id": 147, "name": "NY Yankees", "abbreviation": "NYY"}, "score": None, "probablePitcher": {}},
                            "home": {"team": {"id": 111, "name": "Boston Red Sox", "abbreviation": "BOS"}, "score": None, "probablePitcher": {}},
                        },
                        "venue": {"id": 3, "name": "Fenway Park"},
                    },
                ]
            }
        ]
    }

    with patch('requests.get', return_value=mock_resp):
        games = data_mlb.fetch_season_schedule(2024)

    # Postponed game should be excluded
    assert len(games) == 1
    g = games[0]
    assert g["mlb_game_id"] == 1001
    assert g["away_score"] == 3
    assert g["home_score"] == 5
    assert g["home_team_won"] is True
    assert g["away_pitcher_id"] == 5001
    assert g["season"] == 2024
