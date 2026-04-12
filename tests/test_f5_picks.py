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


def test_analyze_f5_pick_recommends_home_ml_when_home_pitching_edge():
    from analysis import _analyze_f5_pick

    game = {
        "home_team_name": "Boston Red Sox",
        "away_team_name": "New York Yankees",
        "projected_away_score": 3.5,
        "projected_home_score": 4.2,
    }
    f5_odds = {"consensus": {"home_ml": -130, "away_ml": 110,
                              "total_line": 4.5, "over_price": -115, "under_price": -105}}
    # pitching_score > 0 means home pitching advantage
    result = _analyze_f5_pick(game, f5_odds, pitching_score=0.30)

    assert result["pick"] in ("f5_home", "f5_away")
    assert result["pick"] == "f5_home"
    assert result["confidence"] >= 7


def test_analyze_f5_pick_returns_none_when_weak_pitching_signal():
    from analysis import _analyze_f5_pick

    game = {
        "home_team_name": "Red Sox",
        "away_team_name": "Yankees",
        "projected_away_score": 4.0,
        "projected_home_score": 4.0,
    }
    f5_odds = {"consensus": {"home_ml": -110, "away_ml": -110, "total_line": 4.5}}
    result = _analyze_f5_pick(game, f5_odds, pitching_score=0.10)  # below 0.20 threshold
    assert result is None


def test_analyze_f5_pick_returns_none_when_no_f5_odds():
    from analysis import _analyze_f5_pick
    result = _analyze_f5_pick({}, {}, pitching_score=0.35)
    assert result is None


import sqlite3
import database as _db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(_db, "DB_PATH", db_path)
    _db.init_db()
    return db_path


def test_picks_table_accepts_f5_ml_pick_type(fresh_db):
    """picks table must accept f5_ml, f5_over, f5_under without CHECK constraint error."""
    conn = sqlite3.connect(fresh_db)
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    # Should not raise
    conn.execute("""
        INSERT INTO picks
        (game_id, pick_type, pick_team, confidence, win_probability, edge_score,
         projected_away_score, projected_home_score,
         edge_pitching, edge_offense, edge_advanced, edge_bullpen, edge_weather, edge_market,
         notes, ev_score, ml_odds, ou_odds, created_at, updated_at)
        VALUES (1,'f5_ml','Red Sox',8,62.0,0.15,3.2,4.1,'','','','','','','F5',0.05,-130,NULL,?,?)
    """, (now, now))
    conn.commit()
    conn.close()


def test_grade_f5_pick_from_linescore():
    """_grade_f5_pick returns 'won'/'lost'/'push' from inning scores."""
    from engine import _grade_f5_pick

    linescore = {
        "innings": [
            {"num": 1, "away": {"runs": 0}, "home": {"runs": 1}},
            {"num": 2, "away": {"runs": 2}, "home": {"runs": 0}},
            {"num": 3, "away": {"runs": 0}, "home": {"runs": 0}},
            {"num": 4, "away": {"runs": 1}, "home": {"runs": 0}},
            {"num": 5, "away": {"runs": 0}, "home": {"runs": 2}},
            {"num": 6, "away": {"runs": 3}, "home": {"runs": 0}},  # innings 6+ ignored
        ]
    }
    # F5: away=3 runs (inn 2+4), home=3 runs (inn 1+5) — push
    assert _grade_f5_pick("f5_away", linescore) == "push"

    # Modify: home scores more in F5
    linescore["innings"][4]["home"]["runs"] = 3  # home gets 4 total in F5
    assert _grade_f5_pick("f5_home", linescore) == "won"
    assert _grade_f5_pick("f5_away", linescore) == "lost"
