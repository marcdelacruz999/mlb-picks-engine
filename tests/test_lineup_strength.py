import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _make_player_api_response(player_stats: list) -> dict:
    """Build a fake MLB /people API response."""
    people = []
    for entry in player_stats:
        people.append({
            "id": entry["player_id"],
            "stats": [{
                "splits": [{
                    "stat": {
                        "ops": str(entry["ops"]),
                        "obp": str(entry["obp"]),
                        "slg": str(entry["slg"]),
                    }
                }]
            }]
        })
    return {"people": people}


def _make_game(home_ops=0.750, away_ops=0.720,
               home_obp=0.330, away_obp=0.310,
               home_slg=0.420, away_slg=0.410,
               home_runs=300, away_runs=280,
               home_games=60, away_games=60,
               home_lineup_ids=None, away_lineup_ids=None):
    return {
        "home_batting": {
            "ops": home_ops, "obp": home_obp, "slg": home_slg,
            "runs": home_runs, "games_played": home_games,
        },
        "away_batting": {
            "ops": away_ops, "obp": away_obp, "slg": away_slg,
            "runs": away_runs, "games_played": away_games,
        },
        "home_lineup_ids": home_lineup_ids or [],
        "away_lineup_ids": away_lineup_ids or [],
    }


# ──────────────────────────────────────────────────────────────
# Test 1: cache prevents duplicate requests
# ──────────────────────────────────────────────────────────────

def test_fetch_lineup_batting_uses_cache():
    """Second call with same IDs must not make a new HTTP request."""
    import data_mlb

    # Clear cache before test
    data_mlb._player_stat_cache.clear()

    fake_response = _make_player_api_response([
        {"player_id": 123, "ops": 0.800, "obp": 0.350, "slg": 0.450},
        {"player_id": 456, "ops": 0.720, "obp": 0.320, "slg": 0.400},
    ])
    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_response
    mock_resp.raise_for_status = MagicMock()

    with patch("data_mlb.requests.get", return_value=mock_resp) as mock_get:
        result1 = data_mlb.fetch_lineup_batting([123, 456])
        result2 = data_mlb.fetch_lineup_batting([123, 456])

    # Only one HTTP call should have been made
    assert mock_get.call_count == 1
    assert len(result1) == 2
    assert len(result2) == 2
    assert result1 == result2


# ──────────────────────────────────────────────────────────────
# Test 2: no lineup IDs → score_offense unchanged (no crash)
# ──────────────────────────────────────────────────────────────

def test_score_offense_without_lineup_ids():
    """When no lineup IDs present, score_offense behaves exactly as before."""
    import analysis

    game_no_ids = _make_game(
        home_ops=0.750, away_ops=0.750,  # identical → score ~0
        home_lineup_ids=[],
        away_lineup_ids=[],
    )
    result = analysis.score_offense(game_no_ids)

    assert "score" in result
    assert "edge" in result
    assert abs(result["score"]) < 0.10  # equal offenses → near zero
    # Confirmed lineup suffix should NOT be appended
    assert "(confirmed lineup)" not in result["edge"]


# ──────────────────────────────────────────────────────────────
# Test 3: stronger home confirmed lineup → positive adjustment
# ──────────────────────────────────────────────────────────────

def test_score_offense_with_stronger_home_lineup():
    """Home confirmed lineup OPS significantly above team season OPS → positive score adjustment."""
    import analysis

    # Equal season stats, so base score is ~0
    game = _make_game(
        home_ops=0.720, away_ops=0.720,
        home_lineup_ids=[101, 102, 103],
        away_lineup_ids=[],
    )

    # Home lineup players have ops=0.900 avg (25% above team 0.720)
    fake_lineup_stats = [
        {"player_id": 101, "ops": 0.900, "obp": 0.380, "slg": 0.520},
        {"player_id": 102, "ops": 0.900, "obp": 0.380, "slg": 0.520},
        {"player_id": 103, "ops": 0.900, "obp": 0.380, "slg": 0.520},
    ]

    with patch("analysis.fetch_lineup_batting", return_value=fake_lineup_stats):
        result = analysis.score_offense(game)

    # Home lineup is much stronger than season avg → positive score
    assert result["score"] > 0.0, f"Expected positive score, got {result['score']}"
    assert "(confirmed lineup)" in result["edge"]


# ──────────────────────────────────────────────────────────────
# Test 4: API failure in fetch_lineup_batting returns empty list
# ──────────────────────────────────────────────────────────────

def test_fetch_lineup_batting_api_failure_returns_empty():
    """When the MLB API raises, fetch_lineup_batting returns [] without crashing."""
    import data_mlb

    data_mlb._player_stat_cache.clear()

    with patch("data_mlb.requests.get", side_effect=Exception("connection timeout")):
        result = data_mlb.fetch_lineup_batting([111, 222])

    assert result == []
