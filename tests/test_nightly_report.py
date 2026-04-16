# tests/test_nightly_report.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from discord_bot import _format_nightly_report

# Shared fixtures
RESULTS = {
    "wins": 3,
    "losses": 0,
    "pushes": 0,
    "roi": 100.0,
    "ml_correct": 12,
    "ml_incorrect": 3,
    "ou_correct": 1,
    "ou_incorrect": 5,
    "pick_lines": [
        "✅ ATL Braves (moneyline) — WON",
        "✅ TB Rays (moneyline) — WON",
        "✅ HOU Astros (moneyline) — WON",
    ],
}

LOG_ENTRIES = [
    {
        "mlb_game_id": 1, "away_team": "Miami Marlins", "home_team": "Atlanta Braves",
        "ml_pick_team": "Atlanta Braves", "ml_confidence": 7, "ml_status": "correct",
        "ou_pick": None, "ou_line": None, "ou_status": "none",
        "actual_away_score": 3, "actual_home_score": 6,
    },
    {
        "mlb_game_id": 2, "away_team": "Tampa Bay Rays", "home_team": "Chicago White Sox",
        "ml_pick_team": "Tampa Bay Rays", "ml_confidence": 6, "ml_status": "correct",
        "ou_pick": None, "ou_line": None, "ou_status": "none",
        "actual_away_score": 8, "actual_home_score": 3,
    },
    {
        "mlb_game_id": 3, "away_team": "Kansas City Royals", "home_team": "Detroit Tigers",
        "ml_pick_team": "Detroit Tigers", "ml_confidence": 4, "ml_status": "correct",
        "ou_pick": "under", "ou_line": 8.0, "ou_status": "correct",
        "actual_away_score": 1, "actual_home_score": 2,
    },
    {
        "mlb_game_id": 4, "away_team": "San Francisco Giants", "home_team": "Cincinnati Reds",
        "ml_pick_team": "Cincinnati Reds", "ml_confidence": 8, "ml_status": "correct",
        "ou_pick": "under", "ou_line": 8.5, "ou_status": "incorrect",
        "actual_away_score": 3, "actual_home_score": 8,
    },
]

# Game IDs for sent picks (game_id=1 and game_id=2 map to mlb_game_id 1 and 2)
SENT_PICKS_BY_GAME = {
    10: [{"pick_type": "moneyline", "pick_team": "Atlanta Braves", "confidence": 7,
          "win_probability": 63.0, "ml_odds": -150}],
    20: [{"pick_type": "moneyline", "pick_team": "Tampa Bay Rays", "confidence": 7,
          "win_probability": 52.8, "ml_odds": -100}],
}

# mlb_game_id -> local game_id mapping (needed for sent pick lookup)
MLB_TO_LOCAL = {1: 10, 2: 20, 3: 30, 4: 40}


def test_report_contains_header():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "MLB NIGHTLY REPORT" in msg

def test_report_contains_confidence_section():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "CONFIDENCE PICKS" in msg

def test_report_contains_ml_board_section():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "ML BOARD" in msg

def test_report_contains_ou_board_section():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "O/U BOARD" in msg

def test_confidence_picks_show_result():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "WON" in msg or "✅" in msg

def test_ml_board_shows_all_games():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "Marlins" in msg or "MIA" in msg or "MAR" in msg
    assert "Reds" in msg or "CIN" in msg or "RED" in msg

def test_ou_board_only_shows_games_with_ou_pick():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "Tigers" in msg or "DET" in msg
    lines = msg.split("\n")
    ou_start = next((i for i, l in enumerate(lines) if "O/U BOARD" in l), None)
    assert ou_start is not None
    ou_section = "\n".join(lines[ou_start:])
    assert "Rays" not in ou_section

def test_ml_record_summary():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "12W-3L" in msg or "12W - 3L" in msg

def test_ou_record_summary():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "1W-5L" in msg or "1W - 5L" in msg

def test_confidence_picks_roi():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "ROI" in msg
    assert "100" in msg

def test_no_pass_day_when_picks_exist():
    msg = _format_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert "PASS" not in msg or "WON" in msg

def test_pass_day_when_no_picks():
    msg = _format_nightly_report(
        {**RESULTS, "wins": 0, "losses": 0, "pushes": 0, "roi": 0.0, "pick_lines": []},
        LOG_ENTRIES, {}, MLB_TO_LOCAL
    )
    assert "PASS" in msg

from unittest.mock import patch, MagicMock
from discord_bot import send_nightly_report

def test_send_nightly_report_no_webhook(monkeypatch):
    monkeypatch.setattr("discord_bot.DISCORD_WEBHOOK_URL", "")
    result = send_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert result is False

def test_send_nightly_report_success(monkeypatch):
    monkeypatch.setattr("discord_bot.DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/fake")
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    with patch("discord_bot.requests.post", return_value=mock_resp):
        result = send_nightly_report(RESULTS, LOG_ENTRIES, SENT_PICKS_BY_GAME, MLB_TO_LOCAL)
    assert result is True
