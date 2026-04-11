import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock
from datetime import date


def _make_schedule_response(team_id, game_date, game_entries):
    """
    Build a fake MLB schedule API response.
    game_entries: list of {"date": str, "is_away": bool, "home_abbr": str}
    """
    dates = []
    for entry in game_entries:
        if entry["is_away"]:
            away_team = {"id": team_id, "abbreviation": "NYY"}
            home_team = {"id": 9999, "abbreviation": entry["home_abbr"]}
        else:
            away_team = {"id": 9999, "abbreviation": "BOS"}
            home_team = {"id": team_id, "abbreviation": entry["home_abbr"]}

        dates.append({
            "date": entry["date"],
            "games": [{
                "status": {"detailedState": "Final"},
                "teams": {
                    "away": {"team": away_team},
                    "home": {"team": home_team},
                },
            }],
        })
    return {"dates": dates}


def test_fetch_travel_context_consecutive_road_games():
    """Mock API with 6 consecutive away games — consecutive_road_games should be 6."""
    import data_mlb

    team_id = 147  # Yankees
    game_date = "2026-04-11"

    # Build 6 consecutive away games ending at game_date
    entries = [
        {"date": f"2026-04-0{d}", "is_away": True, "home_abbr": "BOS"}
        for d in range(6, 10)  # Apr 6-9
    ] + [
        {"date": "2026-04-10", "is_away": True, "home_abbr": "BOS"},
        {"date": "2026-04-11", "is_away": True, "home_abbr": "BOS"},
    ]

    fake_response = _make_schedule_response(team_id, game_date, entries)

    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_response
    mock_resp.raise_for_status = MagicMock()

    with patch("data_mlb.requests.get", return_value=mock_resp):
        result = data_mlb.fetch_travel_context(team_id, game_date)

    assert result.get("consecutive_road_games") == 6


def test_fetch_travel_context_api_error_returns_empty():
    """When requests.get raises an exception, fetch_travel_context returns {}."""
    import data_mlb

    with patch("data_mlb.requests.get", side_effect=Exception("connection timeout")):
        result = data_mlb.fetch_travel_context(147, "2026-04-11")

    assert result == {}


def _make_momentum_game(away_travel=None, home_record=None, away_record=None):
    """Build a minimal game dict for score_momentum tests."""
    return {
        "home_record": home_record or {},
        "away_record": away_record or {},
        "away_travel": away_travel,
    }


def test_score_momentum_no_travel_data_unchanged():
    """Without away_travel in the game dict, score_momentum behaves exactly as before."""
    import analysis

    # Two equal teams, no travel data at all
    game_with = _make_momentum_game(away_travel=None)
    game_without = {
        "home_record": {},
        "away_record": {},
        # no away_travel key
    }

    result_with = analysis.score_momentum(game_with)
    result_without = analysis.score_momentum(game_without)

    assert result_with["score"] == result_without["score"]
    assert result_with["score"] == 0.0


def test_score_momentum_extended_road_trip_penalty():
    """With consecutive_road_games=6, score shifts toward home (positive)."""
    import analysis

    game_no_travel = _make_momentum_game(away_travel={})
    game_with_travel = _make_momentum_game(away_travel={
        "consecutive_road_games": 6,
        "timezone_changes_last_5d": 0,
        "days_since_off_day": 6,
    })

    result_no = analysis.score_momentum(game_no_travel)
    result_travel = analysis.score_momentum(game_with_travel)

    # Travel penalty should shift score toward home (positive direction)
    assert result_travel["score"] > result_no["score"]
    # Penalty of 0.04 for 6 road games
    assert result_travel["score"] == pytest.approx(0.04, abs=0.001)
    # Signal should mention road trip
    assert "road trip" in result_travel["edge"].lower()
