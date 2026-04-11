"""
MLB Picks Engine — Historical Backtester
==========================================
Runs the 7-agent model against 2024/2025 season data to validate
weight assumptions and produce calibration reports.

Usage:
    python3 backtest.py                      # both seasons (default)
    python3 backtest.py --season 2024        # single season
    python3 backtest.py --season 2024,2025   # explicit both
    python3 backtest.py --suggest-weights    # output recommended WEIGHTS dict
    python3 backtest.py --no-cache           # force re-fetch all data
"""

import argparse
import time
from datetime import date

import data_mlb
from backtest_cache import BacktestCache
from analysis import analyze_game
from config import WEIGHTS, MIN_CONFIDENCE, MIN_EDGE_SCORE

DEFAULT_SEASONS = [2024, 2025]


# ══════════════════════════════════════════════
#  PHASE 1 — DATA LOADING
# ══════════════════════════════════════════════

def load_season_games(season: int, cache: BacktestCache, force: bool = False) -> list:
    """
    Return game rows for the season. Uses cache if populated, fetches otherwise.
    force=True clears cache and re-fetches.
    """
    if force:
        cache.conn.execute("DELETE FROM season_games WHERE season = ?", (season,))
        cache.conn.commit()

    if cache.is_season_games_cached(season):
        games = cache.load_season_games(season)
        print(f"[CACHE] Loaded {len(games)} games for {season} from cache.")
        return games

    print(f"[FETCH] Fetching {season} schedule from MLB Stats API...")
    games = data_mlb.fetch_season_schedule(season)
    if games:
        cache.save_season_games(season, games)
    return games


def _load_or_fetch_team_stats(cache: BacktestCache, season: int, team_id: int, stat_type: str) -> dict:
    """Return team stats from cache or fetch + cache them."""
    cached = cache.load_team_stats(season, team_id, stat_type)
    if cached is not None:
        return cached

    if stat_type == "batting":
        stats = data_mlb.fetch_team_batting(team_id, season=season)
    else:
        stats = data_mlb.fetch_team_pitching(team_id, season=season)

    if stats:
        cache.save_team_stats(season, team_id, stat_type, stats)
    time.sleep(0.2)  # be polite to the API
    return stats or {}


def _load_or_fetch_pitcher_stats(cache: BacktestCache, season: int, pitcher_id: int) -> dict:
    """Return pitcher stats from cache or fetch + cache them."""
    if not pitcher_id:
        return {}
    cached = cache.load_pitcher_stats(season, pitcher_id)
    if cached is not None:
        return cached
    stats = data_mlb.fetch_pitcher_stats(pitcher_id, season=season)
    if stats:
        # days_rest not available historically — set neutral (None skips rest adjustment)
        stats["days_rest"] = None
        cache.save_pitcher_stats(season, pitcher_id, stats)
    time.sleep(0.2)
    return stats or {}


def _load_or_fetch_statcast(cache: BacktestCache, season: int) -> tuple:
    """Return (sc_batting, sc_pitching, sc_pitchers) from cache or fetch."""
    sc_bat = cache.load_statcast_batting(season)
    if not sc_bat:
        print(f"[FETCH] Fetching {season} Statcast batting...")
        sc_bat = data_mlb.fetch_statcast_team_batting(season=season)
        if sc_bat:
            cache.save_statcast_batting(season, sc_bat)

    sc_pit = cache.load_statcast_pitching(season)
    if not sc_pit:
        print(f"[FETCH] Fetching {season} Statcast pitching...")
        sc_pit = data_mlb.fetch_statcast_team_pitching(season=season)
        if sc_pit:
            cache.save_statcast_pitching(season, sc_pit)

    sc_sp = cache.load_statcast_pitchers(season)
    if not sc_sp:
        print(f"[FETCH] Fetching {season} Statcast pitcher xERA...")
        sc_sp = data_mlb.fetch_statcast_pitcher_xera(season=season)
        if sc_sp:
            cache.save_statcast_pitchers(season, sc_sp)

    return sc_bat, sc_pit, sc_sp


def build_game_dict(game_row: dict, cache: BacktestCache) -> dict:
    """
    Build the game dict that analysis.py expects, using cached historical stats.
    Weather, umpire, and lineup data are unavailable historically — set to neutral.
    days_rest set to None (no rest adjustment for historical games).
    team_record uses neutral placeholder (no point-in-time streak data available).
    """
    season = game_row["season"]

    away_id = game_row["away_team_id"]
    home_id = game_row["home_team_id"]
    away_pid = game_row.get("away_pitcher_id")
    home_pid = game_row.get("home_pitcher_id")

    sc_bat, sc_pit, sc_sp = _load_or_fetch_statcast(cache, season)

    away_bat = _load_or_fetch_team_stats(cache, season, away_id, "batting")
    home_bat = _load_or_fetch_team_stats(cache, season, home_id, "batting")
    away_pit = _load_or_fetch_team_stats(cache, season, away_id, "pitching")
    home_pit = _load_or_fetch_team_stats(cache, season, home_id, "pitching")
    away_ps = _load_or_fetch_pitcher_stats(cache, season, away_pid)
    home_ps = _load_or_fetch_pitcher_stats(cache, season, home_pid)

    # Neutral record placeholder — no point-in-time streak data available historically
    neutral_record = {
        "wins": 0, "losses": 0, "win_pct": 0.500,
        "streak_type": "", "streak_number": 0,
        "home_wins": 0, "home_losses": 0, "last_10_wins": 0,
    }

    return {
        # Team identifiers
        "mlb_game_id": game_row["mlb_game_id"],
        "away_team_name": game_row["away_team_name"],
        "home_team_name": game_row["home_team_name"],
        "away_team_abbr": game_row["away_team_abbr"],
        "home_team_abbr": game_row["home_team_abbr"],
        "away_pitcher_name": game_row["away_pitcher_name"],
        "home_pitcher_name": game_row["home_pitcher_name"],

        # Stats dicts for agents
        "away_pitcher_stats": away_ps,
        "home_pitcher_stats": home_ps,
        "away_batting": away_bat,
        "home_batting": home_bat,
        "away_pitching": away_pit,
        "home_pitching": home_pit,
        "away_record": neutral_record.copy(),
        "home_record": neutral_record.copy(),

        # Statcast
        "away_statcast_bat": sc_bat.get(game_row["away_team_abbr"], {}),
        "home_statcast_bat": sc_bat.get(game_row["home_team_abbr"], {}),
        "away_statcast_pit": sc_pit.get(game_row["away_team_abbr"], {}),
        "home_statcast_pit": sc_pit.get(game_row["home_team_abbr"], {}),
        "away_pitcher_statcast": sc_sp.get(away_pid or 0, {}),
        "home_pitcher_statcast": sc_sp.get(home_pid or 0, {}),

        # Neutral for unavailable historical data
        "weather": {},       # Open-Meteo has no historical forecasts
        "hp_umpire": "",     # HP umpire not bulk-fetchable historically
        "home_lineup_confirmed": False,
        "away_lineup_confirmed": False,
    }


# ══════════════════════════════════════════════
#  PHASE 2 — SCORING
# ══════════════════════════════════════════════

def score_historical_games(game_rows: list, cache: BacktestCache) -> list:
    """
    Score each historical game using the existing analysis.py agents.
    Returns a list of result dicts — one per scoreable game.

    Skips games where both pitchers are unknown (no meaningful pitching score).
    Market agent is excluded (no historical odds data) — scored neutral.
    """
    results = []
    total = len(game_rows)

    for i, game_row in enumerate(game_rows):
        away_pid = game_row.get("away_pitcher_id")
        home_pid = game_row.get("home_pitcher_id")

        # Skip games with no pitcher data at all
        if not away_pid and not home_pid:
            continue

        game_dict = build_game_dict(game_row, cache)

        try:
            # Run analysis with no odds data (market agent scores 0.0)
            analysis = analyze_game(game_dict, odds_data=None)
        except Exception as e:
            print(f"[BACKTEST] Error scoring game {game_row['mlb_game_id']}: {e}")
            continue

        # Extract per-agent scores (excluding market — not backtestable)
        agent_scores = {
            agent: analysis["agents"][agent]["score"]
            for agent in ["pitching", "offense", "bullpen", "advanced", "momentum", "weather"]
        }

        result = {
            "mlb_game_id": game_row["mlb_game_id"],
            "season": game_row["season"],
            "game_date": game_row["game_date"],
            "away_team": game_row["away_team_name"],
            "home_team": game_row["home_team_name"],
            "away_team_abbr": game_row["away_team_abbr"],
            "home_team_abbr": game_row["home_team_abbr"],
            "away_score": game_row["away_score"],
            "home_score": game_row["home_score"],
            "home_team_won": game_row["home_team_won"],

            # Model output
            "model_pick_side": analysis["ml_pick_side"],
            "model_correct": (
                (analysis["ml_pick_side"] == "home" and game_row["home_team_won"]) or
                (analysis["ml_pick_side"] == "away" and not game_row["home_team_won"])
            ),
            "ml_confidence": analysis["ml_confidence"],
            "ml_edge_score": analysis["ml_edge_score"],
            "ml_win_probability": analysis["ml_win_probability"],
            "composite_score": analysis["composite_score"],

            # Per-agent scores for correlation analysis
            "agent_scores": agent_scores,
        }
        results.append(result)

        if (i + 1) % 200 == 0:
            print(f"[BACKTEST] Scored {i + 1}/{total} games...")

    print(f"[BACKTEST] Scored {len(results)} games (skipped {total - len(results)} missing pitchers).")
    return results


# ══════════════════════════════════════════════
#  PHASE 3 — REPORTS
# ══════════════════════════════════════════════

def confidence_report(results: list) -> dict:
    """
    Win rate grouped by model confidence level.
    Returns: {confidence_level: {"picks": N, "wins": N, "win_rate": float}}
    """
    if not results:
        return {}

    buckets = {}
    for r in results:
        conf = r["ml_confidence"]
        if conf not in buckets:
            buckets[conf] = {"picks": 0, "wins": 0}
        buckets[conf]["picks"] += 1
        if r["model_correct"]:
            buckets[conf]["wins"] += 1

    for conf, b in buckets.items():
        b["win_rate"] = round(b["wins"] / b["picks"], 3) if b["picks"] > 0 else 0.0

    return buckets


def calibration_curve(results: list) -> list:
    """
    Bucket results by model-predicted win probability and compare to actual win rate.
    Returns list of dicts sorted by prob_low.
    Buckets: 50-55%, 55-60%, 60-65%, 65-70%, 70%+
    """
    if not results:
        return []

    buckets_def = [(50, 55), (55, 60), (60, 65), (65, 70), (70, 100)]
    buckets = {lo: {"prob_low": lo, "prob_high": hi, "picks": 0, "wins": 0, "actual_win_rate": 0.0}
               for lo, hi in buckets_def}

    for r in results:
        prob = r.get("ml_win_probability", 50.0)
        for lo, hi in buckets_def:
            if lo <= prob < hi:
                buckets[lo]["picks"] += 1
                if r["model_correct"]:
                    buckets[lo]["wins"] += 1
                break

    for b in buckets.values():
        b["actual_win_rate"] = round(b["wins"] / b["picks"], 3) if b["picks"] > 0 else 0.0

    return [b for b in sorted(buckets.values(), key=lambda x: x["prob_low"]) if b["picks"] > 0]


def agent_correlation(results: list) -> dict:
    """
    For each agent, split results into high-score games vs low-score games
    and compare win rates. "Lift" = win_rate_high - win_rate_low.
    High lift = agent is predictive. Low/negative lift = agent is noise.

    High = agent score magnitude in top 50% of games. Low = bottom 50%.
    """
    if not results:
        return {}

    agents = ["pitching", "offense", "bullpen", "advanced", "momentum", "weather"]
    corr = {}

    for agent in agents:
        scores = [abs(r["agent_scores"][agent]) for r in results if agent in r["agent_scores"]]
        if not scores:
            corr[agent] = {"win_rate_high": 0.0, "win_rate_low": 0.0, "lift": 0.0, "n_high": 0, "n_low": 0}
            continue

        median_score = sorted(scores)[len(scores) // 2]

        high_wins = high_total = low_wins = low_total = 0
        for r in results:
            score_mag = abs(r["agent_scores"].get(agent, 0.0))
            if score_mag > median_score:
                high_total += 1
                if r["model_correct"]:
                    high_wins += 1
            else:
                low_total += 1
                if r["model_correct"]:
                    low_wins += 1

        win_rate_high = high_wins / high_total if high_total > 0 else 0.0
        win_rate_low = low_wins / low_total if low_total > 0 else 0.0

        corr[agent] = {
            "win_rate_high": round(win_rate_high, 3),
            "win_rate_low": round(win_rate_low, 3),
            "lift": round(win_rate_high - win_rate_low, 3),
            "n_high": high_total,
            "n_low": low_total,
        }

    return corr


def suggest_weights(corr: dict) -> dict:
    """
    Derive recommended weights from agent_correlation lift scores.
    Market agent weight is held constant (not backtestable without historical odds).
    Suggested weights for the 6 backtestable agents are scaled by their lift,
    floored at 0.01, then normalized to sum to (1 - market_weight).
    """
    market_weight = WEIGHTS["market"]
    remaining = 1.0 - market_weight

    agents = ["pitching", "offense", "bullpen", "advanced", "momentum", "weather"]

    lifts = {a: corr[a]["lift"] for a in agents if a in corr}
    if not lifts:
        return WEIGHTS.copy()

    min_lift = min(lifts.values())
    shift = max(0, -min_lift) + 0.01
    adjusted = {a: lifts[a] + shift for a in agents}

    total_lift = sum(adjusted.values())
    raw_weights = {a: adjusted[a] / total_lift * remaining for a in agents}

    # Floor each agent at 0.01 and renormalize
    floored = {a: max(0.01, raw_weights[a]) for a in agents}
    floor_total = sum(floored.values())
    normalized = {a: round(floored[a] / floor_total * remaining, 3) for a in agents}

    # Adjust rounding error on largest weight
    diff = round(remaining - sum(normalized.values()), 3)
    max_agent = max(normalized, key=lambda a: normalized[a])
    normalized[max_agent] = round(normalized[max_agent] + diff, 3)

    normalized["market"] = market_weight
    return normalized


def print_reports(results: list):
    """Print all four reports to stdout."""
    print("\n" + "=" * 60)
    print("  BACKTESTER RESULTS")
    print("=" * 60)
    print(f"  Total games scored: {len(results)}")
    overall_correct = sum(1 for r in results if r["model_correct"])
    if results:
        print(f"  Overall win rate:   {overall_correct / len(results):.1%}")
    print()

    # Report 1: Confidence calibration
    print("── WIN RATE BY CONFIDENCE LEVEL ──────────────────────────")
    cal = confidence_report(results)
    for conf in sorted(cal.keys(), reverse=True):
        b = cal[conf]
        bar = "█" * int(b["win_rate"] * 20)
        print(f"  Conf {conf:2d}: {b['win_rate']:.1%}  ({b['wins']}/{b['picks']})  {bar}")
    print()

    # Report 2: Calibration curve
    print("── CALIBRATION CURVE ──────────────────────────────────────")
    print("  (Are high-confidence predictions actually more accurate?)")
    curve = calibration_curve(results)
    for b in curve:
        bar = "█" * int(b["actual_win_rate"] * 20)
        expected_mid = (b["prob_low"] + b["prob_high"]) / 2
        diff = b["actual_win_rate"] * 100 - expected_mid
        sign = "+" if diff >= 0 else ""
        print(f"  {b['prob_low']:3d}-{b['prob_high']:3d}%: actual={b['actual_win_rate']:.1%}  "
              f"({sign}{diff:.1f}pp vs model)  n={b['picks']}  {bar}")
    print()

    # Report 3: Agent correlation
    print("── PER-AGENT SIGNAL LIFT ──────────────────────────────────")
    print("  (Lift = win rate when agent fires vs when it doesn't)")
    corr = agent_correlation(results)
    for agent, data in sorted(corr.items(), key=lambda x: -x[1]["lift"]):
        arrow = "▲" if data["lift"] > 0.02 else ("▼" if data["lift"] < -0.02 else "─")
        print(f"  {agent:12s}: lift={data['lift']:+.3f}  {arrow}  "
              f"high={data['win_rate_high']:.1%} (n={data['n_high']})  "
              f"low={data['win_rate_low']:.1%} (n={data['n_low']})")
    print()

    # Report 4: Suggested weights
    suggested = suggest_weights(corr)
    print("── SUGGESTED WEIGHT ADJUSTMENTS ───────────────────────────")
    print(f"  {'Agent':12s}  {'Current':>8}  {'Suggested':>9}  {'Change':>7}")
    print(f"  {'-'*12}  {'-'*8}  {'-'*9}  {'-'*7}")
    for agent in ["pitching", "offense", "bullpen", "advanced", "momentum", "weather", "market"]:
        current = WEIGHTS.get(agent, 0)
        new = suggested.get(agent, current)
        diff = new - current
        arrow = "▲" if diff > 0.005 else ("▼" if diff < -0.005 else " ")
        print(f"  {agent:12s}  {current:8.1%}  {new:9.1%}  {arrow}{abs(diff):6.1%}")

    print()
    print("── PASTE INTO config.py ────────────────────────────────────")
    print("  WEIGHTS = {")
    for agent, w in suggested.items():
        print(f'      "{agent}": {w},')
    print("  }")
    print("=" * 60)
