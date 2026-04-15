"""
calibrate.py — Weekly Signal Calibration Report

Usage:
    python3 calibrate.py             # analyze + post Discord report
    python3 calibrate.py --apply     # apply suggested weights to config.py + commit
    python3 calibrate.py --days N    # override lookback window (default 7)
    python3 calibrate.py --test      # dry run, print to stdout, no Discord send
"""

import sqlite3
import re
import json
import argparse
import subprocess
from datetime import datetime, timedelta
from typing import Optional, List

import config


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_graded_picks(conn: sqlite3.Connection, days: int = 7,
                       _now: Optional[str] = None) -> List[dict]:
    """Return all discord-sent, fully-graded picks within the last N days."""
    if _now is None:
        _now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cutoff_dt = datetime.strptime(_now, "%Y-%m-%d %H:%M:%S") - timedelta(days=days)
    cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")

    rows = conn.execute("""
        SELECT pick_type, pick_team, confidence, status, edge_score,
               edge_pitching, edge_offense, edge_bullpen, edge_advanced,
               edge_market, edge_weather, notes, ml_odds, ou_odds, ev_score,
               created_at
        FROM picks
        WHERE discord_sent = 1
          AND status IN ('won', 'lost', 'push')
          AND created_at >= ?
    """, (cutoff,)).fetchall()

    return [dict(r) for r in rows]


def _parse_era(text: str, side: str) -> Optional[float]:
    """Extract ERA float from bullpen edge text for given side (home/away)."""
    pattern = rf"{side.capitalize()} top pen \(7d\):.*?([\d]+\.[\d]+) ERA"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _parse_market_pct(text: str) -> Optional[float]:
    """Extract market edge percentage from edge_market text."""
    m = re.search(r"has \+([\d.]+)% edge vs market", text or "", re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _parse_wind_mph(text: str) -> Optional[float]:
    """Extract wind speed from edge_weather text."""
    m = re.search(r"Wind\s+([\d]+)\s*mph", text or "", re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def parse_signals(pick: dict) -> dict:
    """
    Parse a pick dict into boolean signal flags via string matching.
    pick may have a '_pick_side' key ('home'/'away') injected by the caller;
    otherwise we infer from pick_type + pick_team (not always possible).
    """
    ep = pick.get("edge_pitching") or ""
    eo = pick.get("edge_offense") or ""
    eb = pick.get("edge_bullpen") or ""
    ea = pick.get("edge_advanced") or ""
    em = pick.get("edge_market") or ""
    ew = pick.get("edge_weather") or ""
    notes = pick.get("notes") or ""

    # SP signals
    sp_home_adv = bool(re.search(r"Home SP.*clear pitching advantage", ep, re.IGNORECASE))
    sp_away_adv = bool(re.search(r"Away SP.*clear pitching advantage", ep, re.IGNORECASE))
    sp_home_layoff = bool(re.search(r"Home SP extended layoff", ep, re.IGNORECASE))
    sp_away_layoff = bool(re.search(r"Away SP extended layoff", ep, re.IGNORECASE))

    # Offense signals
    offense_home = bool(re.search(r"Home lineup has offensive advantage", eo, re.IGNORECASE))
    offense_away = bool(re.search(r"Away lineup has offensive advantage", eo, re.IGNORECASE))

    # Bullpen signals
    bp_home_stronger = bool(re.search(r"Home bullpen is stronger", eb, re.IGNORECASE))
    bp_away_stronger = bool(re.search(r"Away bullpen is stronger", eb, re.IGNORECASE))
    home_era = _parse_era(eb, "home")
    away_era = _parse_era(eb, "away")
    bp_home_era_bad = home_era is not None and home_era > 5.0
    bp_away_era_bad = away_era is not None and away_era > 5.0

    # Advanced signals
    adv_barrel = bool(re.search(r"barrel rate advantage", ea, re.IGNORECASE))
    adv_hardhit = bool(re.search(r"hard-hit rate edge", ea, re.IGNORECASE))
    adv_xwoba = bool(re.search(r"xwOBA", ea, re.IGNORECASE))

    # Market bucket
    mkt_pct = _parse_market_pct(em)
    mkt_low = mkt_pct is not None and mkt_pct < 2.0
    mkt_mid = mkt_pct is not None and 2.0 <= mkt_pct <= 4.0
    mkt_high = mkt_pct is not None and mkt_pct > 4.0

    # Other
    lineup_confirmed = bool(re.search(r"confirmed", notes, re.IGNORECASE))
    rain_flag = bool(re.search(r"Rain", ew, re.IGNORECASE))
    wind_mph = _parse_wind_mph(ew)
    wind_strong = wind_mph is not None and wind_mph > 12.0

    # Combo flags — need pick_side
    pick_side = pick.get("_pick_side", "")
    rust_home = sp_home_layoff and bp_home_era_bad and pick_side == "home"
    rust_away = sp_away_layoff and bp_away_era_bad and pick_side == "away"

    return {
        "sp_home_advantage": sp_home_adv,
        "sp_away_advantage": sp_away_adv,
        "sp_home_layoff": sp_home_layoff,
        "sp_away_layoff": sp_away_layoff,
        "offense_home_edge": offense_home,
        "offense_away_edge": offense_away,
        "bullpen_home_stronger": bp_home_stronger,
        "bullpen_away_stronger": bp_away_stronger,
        "bullpen_home_era_bad": bp_home_era_bad,
        "bullpen_away_era_bad": bp_away_era_bad,
        "advanced_barrel": adv_barrel,
        "advanced_hardhit": adv_hardhit,
        "advanced_xwoba": adv_xwoba,
        "market_edge_low": mkt_low,
        "market_edge_mid": mkt_mid,
        "market_edge_high": mkt_high,
        "lineup_confirmed": lineup_confirmed,
        "rain_flag": rain_flag,
        "wind_strong": wind_strong,
        "rust_weak_pen_home": rust_home,
        "rust_weak_pen_away": rust_away,
    }


# Signals that map to each agent weight key
_SIGNAL_TO_AGENT = {
    "sp_home_advantage":   "pitching",
    "sp_away_advantage":   "pitching",
    "sp_home_layoff":      "pitching",
    "sp_away_layoff":      "pitching",
    "offense_home_edge":   "offense",
    "offense_away_edge":   "offense",
    "bullpen_home_stronger": "bullpen",
    "bullpen_away_stronger": "bullpen",
    "bullpen_home_era_bad":  "bullpen",
    "bullpen_away_era_bad":  "bullpen",
    "advanced_barrel":     "advanced",
    "advanced_hardhit":    "advanced",
    "advanced_xwoba":      "advanced",
    "market_edge_low":     "market",
    "market_edge_mid":     "market",
    "market_edge_high":    "market",
    "rain_flag":           "weather",
    "wind_strong":         "weather",
    "rust_weak_pen_home":  "pitching",
    "rust_weak_pen_away":  "pitching",
}

MIN_SIGNAL_N = 3   # below this — noise, don't show
MIN_SUGGEST_N = 5  # below this — don't suggest weight changes
DIVERGE_THRESHOLD = 0.08   # signal win rate must differ by this much to trigger nudge
NUDGE = 0.02
MAX_NUDGE = 0.03
MIN_TOTAL_ADJUSTMENT = 0.03  # ignore noise commits below this magnitude


def analyze_signals(picks: list) -> dict:
    """
    For each signal flag, compute N, W, L, win_rate, delta vs baseline.
    Returns {baseline_win_rate, signal_table, pick_count, ml_record, ou_record}.
    """
    if not picks:
        return {"baseline_win_rate": 0.0, "signal_table": {}, "pick_count": 0,
                "ml_record": (0, 0), "ou_record": (0, 0)}

    # Baseline — all picks
    total = len(picks)
    total_wins = sum(1 for p in picks if p["status"] == "won")
    baseline = round(total_wins / total, 4) if total > 0 else 0.0

    # ML vs O/U records
    ml_picks = [p for p in picks if p.get("pick_type") in ("moneyline", "f5_ml")]
    ou_picks  = [p for p in picks if p.get("pick_type") in ("over", "under")]
    ml_rec = (sum(1 for p in ml_picks if p["status"] == "won"),
               sum(1 for p in ml_picks if p["status"] == "lost"))
    ou_rec = (sum(1 for p in ou_picks if p["status"] == "won"),
               sum(1 for p in ou_picks if p["status"] == "lost"))

    # Per-signal tallies
    tallies: dict = {}
    for pick in picks:
        sigs = parse_signals(pick)
        for sig, present in sigs.items():
            if not present:
                continue
            if sig not in tallies:
                tallies[sig] = {"n": 0, "wins": 0, "losses": 0}
            tallies[sig]["n"] += 1
            if pick["status"] == "won":
                tallies[sig]["wins"] += 1
            elif pick["status"] == "lost":
                tallies[sig]["losses"] += 1

    # Build signal_table — filter by MIN_SIGNAL_N
    signal_table = {}
    for sig, t in tallies.items():
        if t["n"] < MIN_SIGNAL_N:
            continue
        win_rate = round(t["wins"] / t["n"], 4) if t["n"] > 0 else 0.0
        delta = round(win_rate - baseline, 4)
        signal_table[sig] = {
            "n": t["n"],
            "wins": t["wins"],
            "losses": t["losses"],
            "win_rate": win_rate,
            "delta": delta,
        }

    return {
        "baseline_win_rate": baseline,
        "signal_table": signal_table,
        "pick_count": total,
        "ml_record": ml_rec,
        "ou_record": ou_rec,
    }


def suggest_weights(current: dict, signal_table: dict,
                    baseline: float, n_picks: int) -> dict:
    """
    Suggest agent weight adjustments based on signal performance.
    Returns a new weights dict (normalized to 1.0), or current if no changes warranted.
    """
    adjustments: dict = {k: 0.0 for k in current}

    for sig, row in signal_table.items():
        if row["n"] < MIN_SUGGEST_N:
            continue
        if abs(row["delta"]) <= DIVERGE_THRESHOLD:
            continue
        agent = _SIGNAL_TO_AGENT.get(sig)
        if agent is None or agent not in current:
            continue
        nudge = NUDGE if row["delta"] > 0 else -NUDGE
        adjustments[agent] += nudge

    # Cap each agent adjustment at ±MAX_NUDGE
    for agent in adjustments:
        adjustments[agent] = max(-MAX_NUDGE, min(MAX_NUDGE, adjustments[agent]))

    total_adj = sum(abs(v) for v in adjustments.values())
    if total_adj < MIN_TOTAL_ADJUSTMENT:
        return current  # below noise threshold

    # Apply adjustments
    new_weights = {k: round(current[k] + adjustments[k], 4) for k in current}

    # Clamp to [0.01, 0.50] per weight
    for k in new_weights:
        new_weights[k] = max(0.01, min(0.50, new_weights[k]))

    # Normalize to sum exactly 1.0
    # If we added more than removed, scale other weights down proportionally
    total = sum(new_weights.values())
    if total > 0 and total != 1.0:
        # Identify which weights received adjustments
        adjusted_keys = {k for k, v in adjustments.items() if v != 0}
        # Identify which weights didn't receive adjustments (these will be scaled)
        other_keys = set(new_weights.keys()) - adjusted_keys

        if other_keys:
            # Scale only the non-adjusted weights
            other_total = sum(new_weights[k] for k in other_keys)
            if other_total > 0:
                target_other_total = 1.0 - sum(new_weights[k] for k in adjusted_keys)
                if target_other_total > 0:
                    scale = target_other_total / other_total
                    for k in other_keys:
                        new_weights[k] = round(new_weights[k] * scale, 4)

        # Fine-tune rounding to ensure sum = 1.0
        total_after = sum(new_weights.values())
        if total_after != 1.0:
            diff = round(1.0 - total_after, 4)
            largest = max(new_weights, key=lambda k: new_weights[k])
            new_weights[largest] = round(new_weights[largest] + diff, 4)

    return new_weights
