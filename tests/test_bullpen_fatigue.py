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
