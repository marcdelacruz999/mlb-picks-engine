"""Tests for API retry logic in data_mlb.py and data_odds.py."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import requests
from unittest.mock import patch, MagicMock, call


# ─────────────────────────────────────────────
# data_mlb retry tests
# ─────────────────────────────────────────────

def test_fetch_pitcher_stats_retries_on_connection_error(capsys):
    """fetch_pitcher_stats should retry up to 2 times on ConnectionError, then warn."""
    import data_mlb

    with patch("data_mlb.time.sleep"):
        with patch("data_mlb.requests.get", side_effect=requests.exceptions.ConnectionError("conn refused")) as mock_get:
            result = data_mlb.fetch_pitcher_stats(123456)

    # Should have been called 3 times: 1 initial + 2 retries
    assert mock_get.call_count == 3
    assert result == {}

    captured = capsys.readouterr()
    assert "[WARNING]" in captured.out


def test_fetch_pitcher_stats_no_retry_on_http_error(capsys):
    """fetch_pitcher_stats should NOT retry on HTTPError — just fail immediately."""
    import data_mlb

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("404")
    with patch("data_mlb.requests.get", return_value=mock_resp) as mock_get:
        result = data_mlb.fetch_pitcher_stats(123456)

    # Only 1 call — no retry on non-connection errors
    assert mock_get.call_count == 1
    assert result == {}


def test_fetch_travel_context_retries_on_timeout(capsys):
    """fetch_travel_context should retry up to 2 times on Timeout, then warn and return {}."""
    import data_mlb

    with patch("data_mlb.time.sleep"):
        with patch("data_mlb.requests.get", side_effect=requests.exceptions.Timeout("timed out")) as mock_get:
            result = data_mlb.fetch_travel_context(147, "2026-04-11")

    assert mock_get.call_count == 3
    assert result == {}
    captured = capsys.readouterr()
    assert "[WARNING]" in captured.out


def test_fetch_venue_weather_retries_on_connection_error(capsys):
    """fetch_venue_weather should retry on ConnectionError for the weather API call."""
    import data_mlb

    # Mock coords fetch to succeed
    with patch("data_mlb.fetch_venue_coords", return_value=(40.829659, -74.928329)):
        with patch("data_mlb.time.sleep"):
            with patch("data_mlb.requests.get", side_effect=requests.exceptions.ConnectionError("net err")) as mock_get:
                result = data_mlb.fetch_venue_weather(3313, "2026-04-11")

    # Weather fetch should have been retried
    assert mock_get.call_count == 3
    assert result == {}
    captured = capsys.readouterr()
    assert "[WARNING]" in captured.out


# ─────────────────────────────────────────────
# data_odds retry tests
# ─────────────────────────────────────────────

def test_fetch_odds_retries_on_connection_error(capsys):
    """fetch_odds should retry up to 2 times on ConnectionError."""
    import data_odds
    from unittest.mock import patch as p

    with p("data_odds.ODDS_API_KEY", "test-key"):
        with p("data_odds.time.sleep"):
            with p("data_odds.requests.get", side_effect=requests.exceptions.ConnectionError("conn err")) as mock_get:
                result = data_odds.fetch_odds()

    assert mock_get.call_count == 3
    assert result == []
    captured = capsys.readouterr()
    assert "[WARNING]" in captured.out


def test_fetch_odds_no_retry_on_http_401(capsys):
    """fetch_odds should NOT retry on 401 — just fail immediately."""
    import data_odds

    mock_resp = MagicMock()
    http_err = requests.exceptions.HTTPError("401 Unauthorized")
    http_err.response = MagicMock()
    http_err.response.status_code = 401
    mock_resp.raise_for_status.side_effect = http_err

    with patch("data_odds.ODDS_API_KEY", "test-key"):
        with patch("data_odds.requests.get", return_value=mock_resp) as mock_get:
            result = data_odds.fetch_odds()

    assert mock_get.call_count == 1
    assert result == []
