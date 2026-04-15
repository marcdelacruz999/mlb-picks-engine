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
