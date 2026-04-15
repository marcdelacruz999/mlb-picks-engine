# tests/test_f5_bullpen_gate.py
import pytest
from unittest.mock import patch
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import _analyze_f5_pick

GAME = {
    "home_team_name": "Cubs",
    "away_team_name": "Cardinals",
}

F5_ODDS = {
    "consensus": {
        "home_ml": -130,
        "away_ml": +110,
        "total_line": 4.5,
    }
}


def test_f5_fires_when_strong_sp_and_weak_opponent_pen():
    """F5 should fire when pitching >= 0.20 AND opponent bullpen <= -0.10."""
    result = _analyze_f5_pick(GAME, F5_ODDS, pitching_score=0.25, opponent_bullpen_score=-0.15)
    assert result is not None
    assert result["pick"] == "f5_home"
    assert result["pick_type"] == "f5_ml"


def test_f5_blocked_when_opponent_pen_is_decent():
    """F5 should NOT fire when opponent bullpen is above -0.10 (pen not weak enough)."""
    result = _analyze_f5_pick(GAME, F5_ODDS, pitching_score=0.25, opponent_bullpen_score=0.05)
    assert result is None


def test_f5_blocked_when_opponent_pen_exactly_at_threshold():
    """Boundary: bullpen == -0.10 should pass (<=)."""
    result = _analyze_f5_pick(GAME, F5_ODDS, pitching_score=0.25, opponent_bullpen_score=-0.10)
    assert result is not None


def test_f5_edge_string_includes_bullpen_context():
    """Edge string should mention both pitching score and bullpen score."""
    result = _analyze_f5_pick(GAME, F5_ODDS, pitching_score=0.30, opponent_bullpen_score=-0.18)
    assert result is not None
    edge = result["edge"]
    assert "pen" in edge.lower() or "bullpen" in edge.lower()
    assert "0.18" in edge
