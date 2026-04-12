import pytest
import sys, os
import sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest.mock import patch
import database as _db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db, "DB_PATH", db_path)
    _db.init_db()
    return db_path


def _insert_reliever(db_path, pitcher_id, team_id, game_date, ip, er, k):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR IGNORE INTO pitcher_game_logs
        (mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, is_starter,
         innings_pitched, earned_runs, strikeouts, walks, hits, home_runs)
        VALUES (?,?,?,?,?,0,?,?,?,0,0,0)
    """, (pitcher_id * 100, game_date, pitcher_id, f"Reliever {pitcher_id}",
          team_id, ip, er, k))
    conn.commit()
    conn.close()


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


def test_get_bullpen_top_relievers_returns_top_3_by_ip(fresh_db):
    today = "2026-04-12"
    # 4 relievers for team 10; top 3 by IP should be returned
    _insert_reliever(fresh_db, 1, 10, "2026-04-11", ip=2.0, er=0, k=3)  # 2 IP
    _insert_reliever(fresh_db, 2, 10, "2026-04-11", ip=1.0, er=1, k=1)  # 1 IP
    _insert_reliever(fresh_db, 3, 10, "2026-04-11", ip=3.0, er=0, k=4)  # 3 IP
    _insert_reliever(fresh_db, 4, 10, "2026-04-10", ip=1.5, er=2, k=2)  # 1.5 IP

    result = _db.get_bullpen_top_relievers(10, days=7, as_of_date=today)
    assert len(result) == 3
    # Top 3 by IP: pitcher 3 (3.0), pitcher 1 (2.0), pitcher 4 (1.5)
    total_ips = [r["total_ip"] for r in result]
    assert total_ips[0] >= total_ips[1] >= total_ips[2]


def test_get_bullpen_top_relievers_returns_empty_when_no_data(fresh_db):
    result = _db.get_bullpen_top_relievers(99, days=7, as_of_date="2026-04-12")
    assert result == []


def test_score_bullpen_includes_key_reliever_info(fresh_db, monkeypatch):
    """score_bullpen edge should include key reliever ERA when data exists."""
    import analysis
    from datetime import date, timedelta

    # Insert a reliever for team 10
    _insert_reliever(fresh_db, 11, 10, (date.today() - timedelta(days=2)).isoformat(),
                     ip=2.0, er=1, k=3)

    game = {
        "home_pitching": {"era": 4.0, "whip": 1.3, "k_per_9": 8.0,
                          "saves": 3, "save_opportunities": 4, "holds": 2, "blown_saves": 1},
        "away_pitching": {"era": 4.0, "whip": 1.3, "k_per_9": 8.0,
                          "saves": 3, "save_opportunities": 4, "holds": 2, "blown_saves": 1},
        "home_bullpen_rolling": None,
        "away_bullpen_rolling": None,
        "home_bullpen_usage": {"ip_last_3": 0.0, "ip_last_5": 0.0},
        "away_bullpen_usage": {"ip_last_3": 0.0, "ip_last_5": 0.0},
        "home_team_mlb_id": 10,
        "away_team_mlb_id": 99,  # no data for away
    }
    result = analysis.score_bullpen(game)
    assert "Home top pen" in result["edge"]
