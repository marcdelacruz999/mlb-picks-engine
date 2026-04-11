"""Tests for EV gate in risk_filter."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from analysis import _calculate_ev, risk_filter
from config import MIN_CONFIDENCE, MIN_EDGE_SCORE


class TestCalculateEv:
    def test_negative_odds_ev_calculation(self):
        # -150 odds, 65% win prob → EV = 0.65*(100/150) - 0.35 = 0.433 - 0.35 = +0.083
        ev = _calculate_ev(65.0, -150)
        assert ev == pytest.approx(0.0833, abs=0.001)

    def test_positive_odds_ev_calculation(self):
        # +130 odds, 55% win prob → EV = 0.55*1.30 - 0.45 = 0.715 - 0.45 = +0.265
        ev = _calculate_ev(55.0, 130)
        assert ev == pytest.approx(0.265, abs=0.001)

    def test_negative_ev_high_juice(self):
        # -200 odds, 55% win prob → EV = 0.55*0.5 - 0.45 = 0.275 - 0.45 = -0.175
        ev = _calculate_ev(55.0, -200)
        assert ev < 0

    def test_none_odds_returns_none(self):
        assert _calculate_ev(60.0, None) is None

    def test_ev_gate_rejects_bad_odds(self):
        """Conf 7 pick at -200 odds (heavy juice) should be EV-rejected."""
        # Build minimal analysis that passes conf/edge but fails EV
        analysis = _make_analysis(confidence=7, edge=0.15, win_prob=55.0, home_ml=-200, away_ml=170)
        result = risk_filter([analysis])
        # Should be rejected by EV gate (EV = 0.55*0.5 - 0.45 = -0.175 < -0.02)
        assert len(result) == 0

    def test_ev_gate_approves_good_value(self):
        """Conf 7 pick at +130 odds should pass EV gate."""
        analysis = _make_analysis(confidence=7, edge=0.15, win_prob=55.0, home_ml=130, away_ml=-150)
        result = risk_filter([analysis])
        assert len(result) == 1
        assert result[0]["ev_score"] is not None

    def test_ev_score_in_approved_pick(self):
        """ev_score field present in approved pick dict."""
        analysis = _make_analysis(confidence=7, edge=0.15, win_prob=60.0, home_ml=-120, away_ml=100)
        result = risk_filter([analysis])
        assert len(result) == 1
        assert "ev_score" in result[0]


def _make_analysis(confidence, edge, win_prob, home_ml, away_ml):
    """Minimal analysis dict for testing risk_filter."""
    return {
        "game": "Away @ Home",
        "away_team": "Away",
        "home_team": "Home",
        "ml_pick_side": "home",
        "ml_pick_team": "Home",
        "ml_win_probability": win_prob,
        "ml_edge_score": edge,
        "ml_confidence": confidence,
        "mlb_game_id": 1,
        "game_time_utc": "",
        "projected_away_score": 3.5,
        "projected_home_score": 4.0,
        "lineup_status": "TBD",
        "lineups_confirmed": False,
        "ou_pick": {"pick": None, "confidence": 0, "edge": ""},
        "agents": {
            "pitching": {"edge": "test", "score": 0.1},
            "offense":  {"edge": "test", "score": 0.0},
            "advanced": {"edge": "test", "score": 0.0},
            "bullpen":  {"edge": "test", "score": 0.0},
            "weather":  {"edge": "test", "score": 0.0},
            "market":   {
                "edge": "test", "score": 0.0,
                "detail": {
                    "home_ml": home_ml, "away_ml": away_ml,
                    "home_implied_prob": 0.5, "away_implied_prob": 0.5,
                    "total_line": 8.5, "over_price": -110, "under_price": -110,
                    "home_rl": -1.5, "away_rl": 1.5,
                    "home_rl_price": 130, "away_rl_price": -150,
                },
            },
        },
    }
