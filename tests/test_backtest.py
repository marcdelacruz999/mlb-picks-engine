"""Tests for backtest.py."""
import os
import sys
import tempfile
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def tmp_cache(tmp_path):
    import backtest_cache
    db_path = str(tmp_path / "bt_test.db")
    cache = backtest_cache.BacktestCache(db_path=db_path)
    yield cache
    cache.close()


def test_load_season_games_uses_cache(tmp_cache):
    """load_season_games should return cached data without hitting the API."""
    import backtest
    games = [
        {"mlb_game_id": 1001, "season": 2024, "game_date": "2024-04-01",
         "away_team_id": 147, "away_team_name": "NY Yankees", "away_team_abbr": "NYY",
         "home_team_id": 111, "home_team_name": "Boston Red Sox", "home_team_abbr": "BOS",
         "away_score": 3, "home_score": 5, "home_team_won": True,
         "away_pitcher_id": 5001, "away_pitcher_name": "Cole",
         "home_pitcher_id": 5002, "home_pitcher_name": "Pivetta",
         "venue_id": 3, "venue_name": "Fenway"},
    ]
    tmp_cache.save_season_games(2024, games)

    with patch('data_mlb.fetch_season_schedule') as mock_fetch:
        result_games = backtest.load_season_games(2024, cache=tmp_cache)

    mock_fetch.assert_not_called()
    assert len(result_games) == 1


def test_load_season_games_fetches_when_not_cached(tmp_cache):
    """load_season_games should call the API when cache is empty."""
    import backtest
    mock_games = [
        {"mlb_game_id": 2001, "season": 2024, "game_date": "2024-04-01",
         "away_team_id": 147, "away_team_name": "NYY", "away_team_abbr": "NYY",
         "home_team_id": 111, "home_team_name": "BOS", "home_team_abbr": "BOS",
         "away_score": 2, "home_score": 4, "home_team_won": True,
         "away_pitcher_id": 5001, "away_pitcher_name": "Cole",
         "home_pitcher_id": 5002, "home_pitcher_name": "Pivetta",
         "venue_id": 3, "venue_name": "Fenway"},
    ]
    with patch('data_mlb.fetch_season_schedule', return_value=mock_games):
        result_games = backtest.load_season_games(2024, cache=tmp_cache)

    assert len(result_games) == 1
    assert tmp_cache.is_season_games_cached(2024)


def test_build_game_dict_structure(tmp_cache):
    """build_game_dict should produce a dict with all keys analysis.py expects."""
    import backtest

    game_row = {
        "mlb_game_id": 1001, "season": 2024, "game_date": "2024-04-01",
        "away_team_id": 147, "away_team_name": "NY Yankees", "away_team_abbr": "NYY",
        "home_team_id": 111, "home_team_name": "Boston Red Sox", "home_team_abbr": "BOS",
        "away_score": 3, "home_score": 5, "home_team_won": True,
        "away_pitcher_id": 5001, "away_pitcher_name": "Cole",
        "home_pitcher_id": 5002, "home_pitcher_name": "Pivetta",
        "venue_id": 3, "venue_name": "Fenway",
    }

    batting = {"ops": 0.750, "obp": 0.330, "slg": 0.420, "runs": 500, "games_played": 162,
               "strikeouts": 1200, "at_bats": 5400, "hits": 1350, "walks": 450, "home_runs": 180, "avg": 0.250}
    pitching = {"era": 4.10, "whip": 1.28, "k_per_9": 9.0, "saves": 35, "save_opportunities": 42,
                "holds": 55, "blown_saves": 7, "bb_per_9": 3.1}
    pitcher_stats = {"era": 3.50, "whip": 1.15, "k_per_9": 9.5, "bb_per_9": 2.5,
                     "k_bb_ratio": 3.8, "throws": "R", "days_rest": None,
                     "innings_pitched": 180, "games_started": 30}

    tmp_cache.save_team_stats(2024, 147, "batting", batting)
    tmp_cache.save_team_stats(2024, 111, "batting", batting)
    tmp_cache.save_team_stats(2024, 147, "pitching", pitching)
    tmp_cache.save_team_stats(2024, 111, "pitching", pitching)
    tmp_cache.save_pitcher_stats(2024, 5001, pitcher_stats)
    tmp_cache.save_pitcher_stats(2024, 5002, pitcher_stats)
    tmp_cache.save_statcast_batting(2024, {})
    tmp_cache.save_statcast_pitching(2024, {})
    tmp_cache.save_statcast_pitchers(2024, {})

    result = backtest.build_game_dict(game_row, cache=tmp_cache)

    required_keys = ["home_pitcher_stats", "away_pitcher_stats", "home_batting", "away_batting",
                     "home_pitching", "away_pitching", "home_record", "away_record",
                     "home_team_abbr", "away_team_abbr", "home_team_name", "away_team_name",
                     "weather", "hp_umpire"]
    for k in required_keys:
        assert k in result, f"Missing key: {k}"

    # weather and hp_umpire should be neutral (unavailable historically)
    assert result["weather"] == {}
    assert result["hp_umpire"] == ""


def test_score_historical_games_returns_result_per_game(tmp_cache):
    """score_historical_games should return one result dict per game."""
    import backtest

    game_rows = [
        {"mlb_game_id": 1001, "season": 2024, "game_date": "2024-04-01",
         "away_team_id": 147, "away_team_name": "NY Yankees", "away_team_abbr": "NYY",
         "home_team_id": 111, "home_team_name": "Boston Red Sox", "home_team_abbr": "BOS",
         "away_score": 3, "home_score": 5, "home_team_won": True,
         "away_pitcher_id": 5001, "away_pitcher_name": "Cole",
         "home_pitcher_id": 5002, "home_pitcher_name": "Pivetta",
         "venue_id": 3, "venue_name": "Fenway"},
    ]

    batting = {"ops": 0.750, "obp": 0.330, "slg": 0.420, "runs": 500,
               "games_played": 162, "strikeouts": 1200, "at_bats": 5400,
               "hits": 1350, "walks": 450, "home_runs": 180, "avg": 0.250}
    pitching = {"era": 4.10, "whip": 1.28, "k_per_9": 9.0, "saves": 35,
                "save_opportunities": 42, "holds": 55, "blown_saves": 7, "bb_per_9": 3.1}
    pitcher = {"era": 3.50, "whip": 1.15, "k_per_9": 9.5, "bb_per_9": 2.5,
               "k_bb_ratio": 3.8, "throws": "R", "days_rest": None,
               "innings_pitched": 180, "games_started": 30}

    for tid in [147, 111]:
        tmp_cache.save_team_stats(2024, tid, "batting", batting)
        tmp_cache.save_team_stats(2024, tid, "pitching", pitching)
    for pid in [5001, 5002]:
        tmp_cache.save_pitcher_stats(2024, pid, pitcher)
    tmp_cache.save_statcast_batting(2024, {})
    tmp_cache.save_statcast_pitching(2024, {})
    tmp_cache.save_statcast_pitchers(2024, {})

    results = backtest.score_historical_games(game_rows, cache=tmp_cache)

    assert len(results) == 1
    r = results[0]
    assert "mlb_game_id" in r
    assert "home_team_won" in r
    assert "model_pick_side" in r
    assert "ml_confidence" in r
    assert "ml_edge_score" in r
    assert "agent_scores" in r
    assert set(r["agent_scores"].keys()) == {"pitching", "offense", "bullpen", "advanced", "momentum", "weather"}
    assert r["model_correct"] in (True, False)


def test_score_historical_games_skips_games_missing_pitchers(tmp_cache):
    """Games where both pitchers are unknown should be skipped."""
    import backtest

    game_rows = [
        {"mlb_game_id": 2001, "season": 2024, "game_date": "2024-04-01",
         "away_team_id": 147, "away_team_name": "NY Yankees", "away_team_abbr": "NYY",
         "home_team_id": 111, "home_team_name": "Boston Red Sox", "home_team_abbr": "BOS",
         "away_score": 2, "home_score": 1, "home_team_won": False,
         "away_pitcher_id": None, "away_pitcher_name": "TBD",
         "home_pitcher_id": None, "home_pitcher_name": "TBD",
         "venue_id": 3, "venue_name": "Fenway"},
    ]

    batting = {"ops": 0.750, "obp": 0.330, "slg": 0.420, "runs": 500,
               "games_played": 162, "strikeouts": 1200, "at_bats": 5400,
               "hits": 1350, "walks": 450, "home_runs": 180, "avg": 0.250}
    pitching = {"era": 4.10, "whip": 1.28, "k_per_9": 9.0, "saves": 35,
                "save_opportunities": 42, "holds": 55, "blown_saves": 7, "bb_per_9": 3.1}

    for tid in [147, 111]:
        tmp_cache.save_team_stats(2024, tid, "batting", batting)
        tmp_cache.save_team_stats(2024, tid, "pitching", pitching)
    tmp_cache.save_statcast_batting(2024, {})
    tmp_cache.save_statcast_pitching(2024, {})
    tmp_cache.save_statcast_pitchers(2024, {})

    results = backtest.score_historical_games(game_rows, cache=tmp_cache)
    assert len(results) == 0
