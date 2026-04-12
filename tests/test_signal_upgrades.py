import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest.mock import patch


def _make_splits_response(pitcher_id, home_era, away_era, home_whip, away_whip):
    return {"people": [{
        "id": pitcher_id,
        "stats": [{
            "splits": [
                {"split": {"code": "H"}, "stat": {
                    "era": str(home_era), "whip": str(home_whip),
                    "strikeoutsPer9Inn": "9.0", "walksPer9Inn": "2.5"
                }},
                {"split": {"code": "A"}, "stat": {
                    "era": str(away_era), "whip": str(away_whip),
                    "strikeoutsPer9Inn": "8.0", "walksPer9Inn": "3.0"
                }},
            ]
        }]
    }]}


def test_fetch_pitcher_home_away_splits_returns_both_splits():
    import data_mlb
    # Clear cache
    data_mlb._pitcher_split_cache.clear()

    fake = _make_splits_response(123, home_era=2.85, away_era=4.10,
                                 home_whip=1.05, away_whip=1.28)
    with patch("data_mlb.requests.get") as mock_get:
        mock_get.return_value.raise_for_status = lambda: None
        mock_get.return_value.json.return_value = fake
        result = data_mlb.fetch_pitcher_home_away_splits(123)

    assert result["home_era"] == pytest.approx(2.85)
    assert result["away_era"] == pytest.approx(4.10)
    assert result["home_whip"] == pytest.approx(1.05)
    assert result["away_whip"] == pytest.approx(1.28)


def test_fetch_pitcher_home_away_splits_caches():
    import data_mlb
    data_mlb._pitcher_split_cache.clear()

    fake = _make_splits_response(456, 3.0, 4.0, 1.1, 1.3)
    with patch("data_mlb.requests.get") as mock_get:
        mock_get.return_value.raise_for_status = lambda: None
        mock_get.return_value.json.return_value = fake
        data_mlb.fetch_pitcher_home_away_splits(456)
        data_mlb.fetch_pitcher_home_away_splits(456)  # second call — should not call API again
        assert mock_get.call_count == 1


def test_fetch_pitcher_home_away_splits_returns_empty_on_error():
    import data_mlb
    data_mlb._pitcher_split_cache.clear()
    with patch("data_mlb.requests.get", side_effect=Exception("timeout")):
        result = data_mlb.fetch_pitcher_home_away_splits(999)
    assert result == {}


def test_score_pitching_uses_away_split_for_away_sp():
    """Away SP pitching away should use away_era split, not season ERA."""
    from analysis import score_pitching

    game = {
        "away_pitcher_stats": {
            "era": 5.50, "whip": 1.50, "k_per_9": 7.0, "bb_per_9": 3.5,
            "k_bb_ratio": 2.0, "throws": "R", "days_rest": 5
        },
        "home_pitcher_stats": {
            "era": 5.50, "whip": 1.50, "k_per_9": 7.0, "bb_per_9": 3.5,
            "k_bb_ratio": 2.0, "throws": "R", "days_rest": 5
        },
        # Away SP has much better away ERA (good road pitcher)
        "away_pitcher_splits": {"away_era": 2.50, "away_whip": 1.00,
                                  "away_k9": 9.0, "away_bb9": 2.0},
        "home_pitcher_splits": {},
        "away_pitcher_rolling": None,
        "home_pitcher_rolling": None,
        "away_batting": {"strikeouts": 1000, "at_bats": 4500},
        "home_batting": {"strikeouts": 1000, "at_bats": 4500},
    }
    result = score_pitching(game)
    # Away SP 2.50 ERA (split) vs home SP 5.50 ERA (season) = away advantage
    assert result["score"] < 0.0, "Away SP's better road ERA should give away edge"
