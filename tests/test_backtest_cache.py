"""Tests for backtest_cache.py — SQLite cache layer."""
import os
import sqlite3
import tempfile
import pytest

# The cache module will be importable once backtest_cache.py exists.
# These tests import it directly.


def test_placeholder():
    """Placeholder so pytest can discover this file before backtest_cache exists."""
    assert True


from unittest.mock import patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


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
