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
