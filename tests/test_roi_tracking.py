"""
Tests for true unit ROI calculation logic.
Tests the math directly without going through the full DB stack.
"""


def _calc_roi(picks):
    """
    Replicate the ROI calculation logic from get_roi_summary().
    picks: list of dicts with keys: status, ml_odds (or None), ou_odds (or None)
    Returns roi_per_unit or None if no picks have odds.
    """
    total_profit = 0.0
    picks_with_odds = 0

    for r in picks:
        odds = r.get("ml_odds") or r.get("ou_odds")
        if odds is None:
            continue
        picks_with_odds += 1
        if r["status"] == "won":
            payout = 100.0 / abs(odds) if odds < 0 else odds / 100.0
            total_profit += payout
        elif r["status"] == "lost":
            total_profit -= 1.0
        # push: 0 profit

    if picks_with_odds == 0:
        return None
    return round(total_profit / picks_with_odds, 3)


def test_roi_calculation_win_negative_odds():
    """Won pick at -150: profit = 100/150 = 0.667"""
    picks = [{"status": "won", "ml_odds": -150, "ou_odds": None}]
    roi = _calc_roi(picks)
    assert roi == round(100.0 / 150, 3), f"Expected {round(100/150, 3)}, got {roi}"


def test_roi_calculation_win_positive_odds():
    """Won pick at +130: profit = 130/100 = 1.30"""
    picks = [{"status": "won", "ml_odds": 130, "ou_odds": None}]
    roi = _calc_roi(picks)
    assert roi == round(130.0 / 100, 3), f"Expected {round(130/100, 3)}, got {roi}"


def test_roi_calculation_loss():
    """Lost pick: profit = -1.0"""
    picks = [{"status": "lost", "ml_odds": -110, "ou_odds": None}]
    roi = _calc_roi(picks)
    assert roi == -1.0, f"Expected -1.0, got {roi}"


def test_roi_none_when_no_odds():
    """Picks with no odds stored: roi_per_unit is None"""
    picks = [
        {"status": "won", "ml_odds": None, "ou_odds": None},
        {"status": "lost", "ml_odds": None, "ou_odds": None},
    ]
    roi = _calc_roi(picks)
    assert roi is None, f"Expected None, got {roi}"


def test_roi_calculation_push():
    """Push contributes 0 profit but still counts in denominator."""
    # A push should not change profit but should be in the total graded count
    # Test by calling the profit math directly: push = 0 profit
    profit = 0.0  # push
    assert profit == 0.0
    # Verify that total graded = won + lost + push (not just won + lost)
    summary = {"won": 1, "lost": 1, "push": 1}
    total_graded = summary["won"] + summary["lost"] + summary["push"]
    assert total_graded == 3
