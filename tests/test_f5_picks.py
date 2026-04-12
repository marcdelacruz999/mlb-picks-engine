import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest.mock import patch, MagicMock


def _make_f5_api_response(home="Red Sox", away="Yankees",
                          home_ml=-130, away_ml=110, total=4.5,
                          over_price=-115, under_price=-105):
    return [{
        "id": "abc123",
        "sport_key": "baseball_mlb_h1",
        "commence_time": "2099-01-01T20:00:00Z",  # future date — passes pre-game filter
        "home_team": home,
        "away_team": away,
        "bookmakers": [{
            "title": "DraftKings",
            "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": home, "price": home_ml},
                    {"name": away, "price": away_ml},
                ]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "point": total, "price": over_price},
                    {"name": "Under", "point": total, "price": under_price},
                ]},
            ]
        }]
    }]


def test_fetch_f5_odds_calls_correct_sport_key():
    import data_odds
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = _make_f5_api_response()
    mock_resp.headers = {"x-requests-remaining": "450"}

    with patch("data_odds.requests.get") as mock_get:
        mock_get.return_value = mock_resp
        data_odds.fetch_f5_odds()
        call_url = mock_get.call_args[0][0]
        assert "baseball_mlb_h1" in call_url


def test_fetch_f5_odds_returns_parsed_list():
    import data_odds
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = _make_f5_api_response()
    mock_resp.headers = {"x-requests-remaining": "450"}

    with patch("data_odds.requests.get") as mock_get:
        mock_get.return_value = mock_resp
        result = data_odds.fetch_f5_odds()

    assert len(result) == 1
    assert result[0]["consensus"]["total_line"] == 4.5
    assert result[0]["consensus"]["home_ml"] is not None


def test_match_f5_odds_to_game_finds_game():
    import data_odds
    odds_list = [{
        "home_team": "Boston Red Sox",
        "away_team": "New York Yankees",
        "consensus": {"total_line": 4.5, "home_ml": -130, "away_ml": 110},
        "bookmakers": [],
    }]
    result = data_odds.match_f5_odds_to_game(odds_list, "Boston Red Sox", "New York Yankees")
    assert result.get("consensus", {}).get("total_line") == 4.5


def test_match_f5_odds_to_game_returns_empty_when_no_match():
    import data_odds
    result = data_odds.match_f5_odds_to_game([], "Sox", "Yankees")
    assert result == {}
