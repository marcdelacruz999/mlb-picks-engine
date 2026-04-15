# F5 Bullpen Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opponent bullpen weakness as a required second gate for F5 picks — F5 now fires only when SP edge is strong AND opponent bullpen score ≤ -0.10, making F5 a precision play that isolates SP quality against a vulnerable pen.

**Architecture:** Single function change in `analysis.py`. `_analyze_f5_pick()` gains a `bullpen_score` parameter and checks the opponent's bullpen score before returning a pick. The call site at line 917 passes `bullpen["score"]`. Direction logic (home pick → check away bullpen, away pick → check home bullpen) lives inside the function. Edge string updated to include bullpen context.

**Tech Stack:** Python 3.9, SQLite, pytest

---

## File Structure

| File | Change |
|------|--------|
| `analysis.py` | Modify `_analyze_f5_pick()` signature + body; update call site at line 917 |
| `tests/test_f5_bullpen_gate.py` | New test file — 4 tests covering gate logic |

---

### Task 1: Write failing tests for the bullpen gate

**Files:**
- Create: `tests/test_f5_bullpen_gate.py`

Context: `_analyze_f5_pick` lives in `analysis.py`. It currently takes `(game, f5_odds, pitching_score)` and fires when `|pitching_score| >= 0.20`. After this change it will also require `opponent_bullpen_score <= -0.10`. The `bullpen_score` parameter represents the team being PICKED's opponent pen (positive = strong pen, negative = weak pen). Direction: if pitching_score > 0 (home advantage), opponent is away → `bullpen_score` is the away bullpen score. If pitching_score < 0 (away advantage), opponent is home → `bullpen_score` is the home bullpen score. The caller resolves direction and passes the correct opponent score.

- [ ] **Step 1: Write the test file**

```python
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
    assert "-0.18" in edge or "−0.18" in edge or "0.18" in edge
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
pytest tests/test_f5_bullpen_gate.py -v
```

Expected: 4 failures — `TypeError: _analyze_f5_pick() got an unexpected keyword argument 'opponent_bullpen_score'`

---

### Task 2: Update `_analyze_f5_pick()` signature and gate logic

**Files:**
- Modify: `analysis.py` — function at line 1194, call site at line 917

- [ ] **Step 1: Update the function signature and add bullpen gate**

Find the function at line 1194. Replace the entire function with:

```python
def _analyze_f5_pick(game: dict, f5_odds: dict, pitching_score: float,
                     opponent_bullpen_score: float = 0.0) -> "dict | None":
    """
    Determine if there's an F5 (First 5 Innings) pick.
    Fires when:
      - |pitching_score| >= 0.20  (SP has a clear edge)
      - opponent_bullpen_score <= -0.10  (opponent pen is meaningfully weak)

    The caller resolves direction: if picking home (pitching_score > 0),
    pass away bullpen score as opponent_bullpen_score; if picking away,
    pass home bullpen score.

    Pick format:
      {"pick": "f5_home" | "f5_away", "pick_team": str,
       "pick_type": "f5_ml",
       "confidence": int, "edge": str, "ml_odds": int}
    """
    if not f5_odds or not f5_odds.get("consensus"):
        return None

    consensus = f5_odds["consensus"]
    if not consensus.get("home_ml") or not consensus.get("away_ml"):
        return None

    # Gate 1: SP must have a clear edge
    if abs(pitching_score) < 0.20:
        return None

    # Gate 2: Opponent bullpen must be meaningfully weak
    if opponent_bullpen_score > -0.10:
        return None

    if pitching_score > 0:
        pick = "f5_home"
        pick_team = game.get("home_team_name", "Home")
        ml_odds = consensus.get("home_ml")
        direction = "Home SP advantage"
    else:
        pick = "f5_away"
        pick_team = game.get("away_team_name", "Away")
        ml_odds = consensus.get("away_ml")
        direction = "Away SP advantage"

    if abs(pitching_score) >= 0.35:
        conf = 9
    elif abs(pitching_score) >= 0.25:
        conf = 8
    else:
        conf = 7

    edge = (f"F5 {direction} (pitching {pitching_score:+.3f}, opp pen {opponent_bullpen_score:+.3f}) — "
            f"SP quality isolated, weak opponent pen neutralized")

    return {
        "pick": pick,
        "pick_team": pick_team,
        "pick_type": "f5_ml",
        "confidence": conf,
        "ml_odds": ml_odds,
        "edge": edge,
        "f5_total_line": consensus.get("total_line"),
    }
```

- [ ] **Step 2: Update the call site at line 917**

Find this line (around line 915-917):
```python
    # ── F5 pick (only when strong pitching edge) ──
    f5_odds = game.get("f5_odds", {})
    f5_pick = _analyze_f5_pick(game, f5_odds, pitching["score"])
```

Replace with:
```python
    # ── F5 pick (strong SP edge + weak opponent bullpen) ──
    f5_odds = game.get("f5_odds", {})
    # score_bullpen() sign convention: positive = home pen stronger, negative = away pen stronger.
    # For a home pick (pitching > 0), opponent is away — away pen is weak when bullpen score > 0
    #   → negate so weak opponent = negative number, matching gate <= -0.10
    # For an away pick (pitching < 0), opponent is home — home pen is weak when bullpen score < 0
    #   → use as-is
    _bp = bullpen["score"]
    opponent_bullpen_score = -_bp if pitching["score"] > 0 else _bp
    f5_pick = _analyze_f5_pick(game, f5_odds, pitching["score"], opponent_bullpen_score)
```

Sign convention verified from `score_bullpen()` docstring: `"Positive score = home edge; negative = away edge."` The negation for home picks converts "home pen stronger" (positive) into "away pen weaker" (negative), which hits the `<= -0.10` threshold correctly.

- [ ] **Step 3: Run the new tests**

```bash
cd /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine
pytest tests/test_f5_bullpen_gate.py -v
```

Expected: 4 PASS

- [ ] **Step 4: Run the full test suite to check for regressions**

```bash
pytest tests/ -v 2>&1 | tail -20
```

Expected: All previously passing tests still pass. There are 3 known pre-existing failures unrelated to this change (documented in `docs/testing.md`).

- [ ] **Step 5: Commit**

```bash
git add analysis.py tests/test_f5_bullpen_gate.py
git commit -m "feat: add opponent bullpen gate to F5 picks — requires opp pen <= -0.10"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ F5 gate requires `|pitching_score| >= 0.20` — preserved
- ✅ F5 gate requires `opponent_bullpen_score <= -0.10` — added in Task 2
- ✅ Direction logic: home pick → away bullpen, away pick → home bullpen — handled at call site
- ✅ Confidence levels unchanged (9/8/7) — preserved
- ✅ Edge string updated with bullpen context — done in Task 2
- ✅ No schema changes — correct, analysis.py only

**Placeholder scan:** None found.

**Type consistency:** `opponent_bullpen_score: float = 0.0` default means existing callers that don't pass the arg default to 0.0 (above threshold → F5 blocked). This is conservative and safe for any test that calls `_analyze_f5_pick` without the new param.
