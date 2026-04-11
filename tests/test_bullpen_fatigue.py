import sys
sys.path.insert(0, "/Users/marc/Documents/Claude/Projects/Shenron/mlb-picks-engine/.worktrees/bullpen-fatigue")

import pytest
from unittest.mock import patch, MagicMock
from datetime import date


def _make_game(date_str, team_id, side, pitcher_ids, ip_list, final=True):
    """
    Helper: build a fake game entry.
    pitcher_ids: [starter_id, rel1_id, rel2_id, ...]
    ip_list: ["5.0", "2.1", "1.0"] — one per pitcher, same order
    """
    players = {}
    for pid, ip in zip(pitcher_ids, ip_list):
        players[f"ID{pid}"] = {
            "stats": {"pitching": {"inningsPitched": ip}}
        }
    team_data = {"pitchers": pitcher_ids, "players": players, "team": {"id": team_id}}
    opponent = {"pitchers": [], "players": {}, "team": {"id": 9999}}

    if side == "home":
        teams = {"home": team_data, "away": opponent}
    else:
        teams = {"away": team_data, "home": opponent}

    state = "Final" if final else "Live"
    return {
        "dates": [{
            "date": date_str,
            "games": [{
                "status": {"abstractGameState": state},
                "teams": teams,
            }]
        }]
    }


def test_parse_ip_whole_innings():
    import data_mlb
    assert data_mlb._parse_ip("6.0") == pytest.approx(6.0)


def test_parse_ip_partial_innings():
    import data_mlb
    # "6.2" = 6 innings + 2 outs = 6 + 2/3
    assert data_mlb._parse_ip("6.2") == pytest.approx(6.667, abs=0.01)


def test_parse_ip_one_out():
    import data_mlb
    assert data_mlb._parse_ip("3.1") == pytest.approx(3.333, abs=0.01)


def test_parse_ip_invalid_returns_zero():
    import data_mlb
    assert data_mlb._parse_ip(None) == 0.0
    assert data_mlb._parse_ip("") == 0.0


def test_parse_ip_float_input_returned_as_decimal():
    """Float inputs bypass string parsing — 6.2 (float) returns 6.2, not 6.667."""
    import data_mlb
    assert data_mlb._parse_ip(6.0) == pytest.approx(6.0)
    assert data_mlb._parse_ip(3.0) == pytest.approx(3.0)
    # Note: float 6.2 is NOT the same as string "6.2" — floats return as-is
    assert data_mlb._parse_ip(6.2) == pytest.approx(6.2)
    assert data_mlb._parse_ip("6.2") == pytest.approx(6.667, abs=0.01)


def test_fetch_bullpen_recent_usage_sums_relief_ip():
    """Starter throws 5.0, two relievers throw 2.1 and 1.0 — bullpen IP = 3.333."""
    import data_mlb
    today = date(2026, 4, 15)
    game_date = date(2026, 4, 13).isoformat()  # 2 days ago
    fake_resp = _make_game(game_date, team_id=147, side="home",
                           pitcher_ids=[100, 101, 102],
                           ip_list=["5.0", "2.1", "1.0"])

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"dates": fake_resp["dates"]}
    mock_resp.raise_for_status = MagicMock()

    with patch("data_mlb.requests.get", return_value=mock_resp):
        result = data_mlb.fetch_bullpen_recent_usage(147, as_of_date=today)

    # 2.1 (=2.333) + 1.0 = 3.333 IP from relievers
    assert result["ip_last_3"] == pytest.approx(3.333, abs=0.01)
    assert result["ip_last_5"] == pytest.approx(3.333, abs=0.01)
    assert result["games_last_3"] == 1
    assert result["games_last_5"] == 1


def test_fetch_bullpen_recent_usage_excludes_nonfinal():
    """Live/in-progress games are excluded."""
    import data_mlb
    today = date(2026, 4, 15)
    game_date = date(2026, 4, 14).isoformat()
    fake_resp = _make_game(game_date, team_id=147, side="home",
                           pitcher_ids=[100, 101],
                           ip_list=["7.0", "2.0"],
                           final=False)

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"dates": fake_resp["dates"]}
    mock_resp.raise_for_status = MagicMock()

    with patch("data_mlb.requests.get", return_value=mock_resp):
        result = data_mlb.fetch_bullpen_recent_usage(147, as_of_date=today)

    assert result["ip_last_3"] == 0.0
    assert result["games_last_3"] == 0


def test_fetch_bullpen_recent_usage_only_counts_matching_team():
    """If the team played as away, use the away side's pitchers."""
    import data_mlb
    today = date(2026, 4, 15)
    game_date = date(2026, 4, 13).isoformat()
    fake_resp = _make_game(game_date, team_id=147, side="away",
                           pitcher_ids=[200, 201],
                           ip_list=["6.0", "3.0"])

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"dates": fake_resp["dates"]}
    mock_resp.raise_for_status = MagicMock()

    with patch("data_mlb.requests.get", return_value=mock_resp):
        result = data_mlb.fetch_bullpen_recent_usage(147, as_of_date=today)

    assert result["ip_last_3"] == pytest.approx(3.0)


def test_fetch_bullpen_recent_usage_api_error_returns_zeros():
    import data_mlb
    with patch("data_mlb.requests.get", side_effect=Exception("timeout")):
        result = data_mlb.fetch_bullpen_recent_usage(147)

    assert result == {"ip_last_3": 0.0, "ip_last_5": 0.0, "games_last_3": 0, "games_last_5": 0}


def test_fetch_bullpen_recent_usage_3day_vs_5day_boundary():
    """A game 4 days ago counts in ip_last_5 but NOT in ip_last_3."""
    import data_mlb
    today = date(2026, 4, 15)

    # Game 2 days ago: 3.0 IP from relievers
    game_2d = date(2026, 4, 13).isoformat()
    # Game 4 days ago: 4.0 IP from relievers
    game_4d = date(2026, 4, 11).isoformat()

    fake_resp = {
        "dates": [
            {
                "date": game_2d,
                "games": [{
                    "status": {"abstractGameState": "Final"},
                    "teams": {
                        "home": {
                            "pitchers": [100, 101],
                            "players": {
                                "ID100": {"stats": {"pitching": {"inningsPitched": "5.0"}}},
                                "ID101": {"stats": {"pitching": {"inningsPitched": "3.0"}}},
                            },
                            "team": {"id": 147},
                        },
                        "away": {"pitchers": [], "players": {}, "team": {"id": 9999}},
                    },
                }],
            },
            {
                "date": game_4d,
                "games": [{
                    "status": {"abstractGameState": "Final"},
                    "teams": {
                        "home": {
                            "pitchers": [200, 201],
                            "players": {
                                "ID200": {"stats": {"pitching": {"inningsPitched": "5.0"}}},
                                "ID201": {"stats": {"pitching": {"inningsPitched": "4.0"}}},
                            },
                            "team": {"id": 147},
                        },
                        "away": {"pitchers": [], "players": {}, "team": {"id": 9999}},
                    },
                }],
            },
        ]
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_resp
    mock_resp.raise_for_status = MagicMock()

    with patch("data_mlb.requests.get", return_value=mock_resp):
        result = data_mlb.fetch_bullpen_recent_usage(147, as_of_date=today)

    assert result["ip_last_3"] == pytest.approx(3.0)   # only the 2-day-ago game
    assert result["ip_last_5"] == pytest.approx(7.0)   # both games: 3.0 + 4.0
    assert result["games_last_3"] == 1
    assert result["games_last_5"] == 2


def _make_bullpen_game(home_era=4.00, away_era=4.00,
                       home_whip=1.30, away_whip=1.30,
                       home_k9=8.5, away_k9=8.5,
                       home_sv=20, home_svo=25,
                       away_sv=20, away_svo=25,
                       home_usage=None, away_usage=None):
    """Helper: build a game dict for score_bullpen tests."""
    return {
        "home_pitching": {
            "era": home_era, "whip": home_whip, "k_per_9": home_k9,
            "saves": home_sv, "save_opportunities": home_svo,
        },
        "away_pitching": {
            "era": away_era, "whip": away_whip, "k_per_9": away_k9,
            "saves": away_sv, "save_opportunities": away_svo,
        },
        "home_bullpen_usage": home_usage or {},
        "away_bullpen_usage": away_usage or {},
    }


def test_score_bullpen_no_fatigue_unaffected():
    """Equal bullpens, no fatigue → score near 0."""
    import analysis
    game = _make_bullpen_game()
    result = analysis.score_bullpen(game)
    assert abs(result["score"]) < 0.05


def test_score_bullpen_home_fatigue_reduces_home_edge():
    """Home bullpen heavily fatigued (ip_last_3 > 12) → score shifts negative."""
    import analysis
    game = _make_bullpen_game(
        home_era=3.80, away_era=4.20,  # home slightly better on paper
        home_usage={"ip_last_3": 14.0, "ip_last_5": 18.0},
        away_usage={},
    )
    result = analysis.score_bullpen(game)
    # Heavy fatigue penalty (-0.15) should push score below 0.05
    assert result["score"] < 0.05


def test_score_bullpen_away_fatigue_benefits_home():
    """Away bullpen heavily fatigued → score shifts positive (home advantage)."""
    import analysis
    game = _make_bullpen_game(
        away_usage={"ip_last_3": 13.0, "ip_last_5": 17.0},
        home_usage={},
    )
    result = analysis.score_bullpen(game)
    assert result["score"] > 0.05


def test_score_bullpen_moderate_fatigue():
    """Moderate fatigue (ip_last_3 between 8 and 12) → smaller penalty (-0.08)."""
    import analysis
    game = _make_bullpen_game(
        away_usage={"ip_last_3": 10.0, "ip_last_5": 14.0},
        home_usage={},
    )
    result = analysis.score_bullpen(game)
    # Moderate fatigue (+0.08) on equal bullpens — should be positive but not huge
    assert 0.02 < result["score"] < 0.15


def test_score_bullpen_fatigue_detail_included():
    """Fatigue IP values appear in detail dict."""
    import analysis
    game = _make_bullpen_game(
        home_usage={"ip_last_3": 13.0, "ip_last_5": 17.0},
        away_usage={"ip_last_3": 2.0, "ip_last_5": 4.0},
    )
    result = analysis.score_bullpen(game)
    assert "home_bp_ip_last_3" in result["detail"]
    assert "away_bp_ip_last_3" in result["detail"]
    assert result["detail"]["home_bp_ip_last_3"] == 13.0
    assert result["detail"]["away_bp_ip_last_3"] == 2.0


def test_bullpen_fatigue_penalty_thresholds():
    """Verify exact threshold behavior at boundaries."""
    import analysis
    assert analysis._bullpen_fatigue_penalty({}) == 0.0
    assert analysis._bullpen_fatigue_penalty({"ip_last_3": 0.0}) == 0.0
    assert analysis._bullpen_fatigue_penalty({"ip_last_3": 8.0}) == 0.0   # boundary: 8.0 is fresh
    assert analysis._bullpen_fatigue_penalty({"ip_last_3": 8.1}) == 0.08  # just over moderate threshold
    assert analysis._bullpen_fatigue_penalty({"ip_last_3": 12.0}) == 0.08 # boundary: 12.0 is moderate
    assert analysis._bullpen_fatigue_penalty({"ip_last_3": 12.1}) == 0.15 # just over heavy threshold
    assert analysis._bullpen_fatigue_penalty({"ip_last_3": 20.0}) == 0.15 # heavy
