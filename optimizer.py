#!/usr/bin/env python3
"""
MLB Picks Engine — Weekly Optimizer
=====================================
Runs every Sunday at 9pm. Analyzes the full pipeline, picks the highest-impact
improvement, implements it, runs tests, commits, and sends a Discord report.

Schedule: weekly via launchd (com.marc.mlb-picks-engine.optimizer.plist)
Output:   git commit + Discord notification with what changed
"""

import os
import re
import sys
import json
import textwrap
import subprocess
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import DISCORD_WEBHOOK_URL, WEIGHTS, MIN_CONFIDENCE, MIN_EDGE_SCORE, DATABASE_PATH

LOG_PATH = PROJECT_ROOT / "engine.log"
COMPLETED_PATH = PROJECT_ROOT / "COMPLETED_IMPROVEMENTS.md"
CLAUDE_BIN = "/Users/marc/.local/bin/claude"

# ─────────────────────────────────────────────────────────────────────────────
#  BACKTEST REFERENCE BASELINE  (2024 + 2025, 4,855 games — run 2026-04-10)
#
#  These are the ground-truth lift scores from the historical backtest.
#  They serve as a stable prior when live data is thin (< 100 graded picks).
#  As live picks accumulate the optimizer blends live signal in progressively.
#
#  lift  = win_rate_high_signal - win_rate_low_signal (higher = more predictive)
#  calibration = actual win rate per confidence bucket (model was underconfident 5-14pp)
# ─────────────────────────────────────────────────────────────────────────────

BACKTEST_REFERENCE = {
    "seasons": [2024, 2025],
    "total_games": 4855,
    "overall_win_rate": 0.589,
    "agent_lift": {
        "pitching":  0.080,   # strongest signal — short rest / handedness matchup
        "bullpen":   0.050,   # underweighted at 10%, raised to 17% after backtest
        "offense":   0.041,   # solid, weight justified at 20%
        "advanced": -0.009,   # near-zero — likely end-of-season stats artifact
        "momentum":  None,    # untestable — neutral placeholder used historically
        "weather":   None,    # untestable — no historical weather data
        "market":    None,    # untestable — no historical odds data
    },
    "calibration": {
        # confidence bucket → actual win rate in backtest (model was underconfident)
        # format: {conf: actual_rate}  — model predicted X%, actually hit Y%
        8: 0.852,   # 46/54 games
        7: 0.723,   # 188/260 games
        # lower buckets not tracked — below pick threshold
    },
    "notes": (
        "Momentum/weather/market lifts are None because backtest used neutral "
        "placeholders (no historical data). Do not use lift=0 as a signal to "
        "downweight these agents."
    ),
}


def load_backtest_lift():
    """
    Load agent lift scores from backtest, blending cached results if available.
    Falls back to BACKTEST_REFERENCE constants when backtest cache isn't queryable.

    Returns dict: {agent: lift_score} for all 7 agents.
    None = untestable historically (momentum, weather, market).
    """
    # Try to re-derive lift from backtest_cache.db results table if available
    cache_path = PROJECT_ROOT / "backtest_cache.db"
    if cache_path.exists():
        try:
            conn = sqlite3.connect(str(cache_path))
            conn.row_factory = sqlite3.Row
            # Check if we have a pre-computed results cache table
            table_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='optimizer_lift_cache'"
            ).fetchone()

            if table_exists:
                rows = conn.execute(
                    "SELECT agent, lift FROM optimizer_lift_cache ORDER BY computed_at DESC"
                ).fetchall()
                conn.close()
                if rows:
                    lift = {r["agent"]: r["lift"] for r in rows}
                    # Fill in untestable agents from reference
                    for agent, val in BACKTEST_REFERENCE["agent_lift"].items():
                        if agent not in lift:
                            lift[agent] = val
                    return lift
            conn.close()
        except Exception:
            pass

    # Fall back to hardcoded reference
    return dict(BACKTEST_REFERENCE["agent_lift"])


def cache_backtest_lift(lift_scores: dict):
    """
    Persist freshly-computed lift scores to backtest_cache.db so future optimizer
    runs don't need to re-run the full 4,855-game analysis.
    Called after a fresh backtest run.
    """
    cache_path = PROJECT_ROOT / "backtest_cache.db"
    if not cache_path.exists():
        return
    try:
        conn = sqlite3.connect(str(cache_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS optimizer_lift_cache (
                agent       TEXT NOT NULL,
                lift        REAL,
                computed_at TEXT NOT NULL,
                PRIMARY KEY (agent, computed_at)
            )
        """)
        today = date.today().isoformat()
        for agent, lift in lift_scores.items():
            conn.execute(
                "INSERT OR REPLACE INTO optimizer_lift_cache VALUES (?,?,?)",
                (agent, lift, today)
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  DATABASE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────────────────────────────────────
#  FULL PIPELINE SNAPSHOT
#  Gives the optimizer (and any Claude task prompt) a complete, current picture
#  of the entire engine — files, DB state, data coverage, gaps.
# ─────────────────────────────────────────────────────────────────────────────

def snapshot_pipeline():
    """
    Build a comprehensive snapshot of the entire engine state.

    Returns a dict with:
      - file_summary: key files + sizes
      - db_tables: all tables with row counts and schema summaries
      - data_coverage: what stats/fields are populated vs sparse
      - data_gaps: fields that exist but are mostly null/empty
      - collection_health: rolling stats freshness per table
    """
    today = date.today()

    # ── File inventory ──
    key_files = [
        "engine.py", "analysis.py", "data_mlb.py", "data_odds.py",
        "discord_bot.py", "database.py", "config.py", "monitor.py",
        "optimizer.py", "backtest.py",
        "CLAUDE.md", "PIPELINE.md", "INSIGHTS.md", "COMPLETED_IMPROVEMENTS.md",
    ]
    file_summary = {}
    for f in key_files:
        p = PROJECT_ROOT / f
        if p.exists():
            lines = len(p.read_text(errors="replace").splitlines())
            file_summary[f] = lines
        else:
            file_summary[f] = None  # missing

    # ── DB table row counts + schema ──
    conn = get_db()
    tables = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()

    db_tables = {}
    for t in tables:
        name = t["name"]
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        # Pull column names from schema
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({name})").fetchall()]
        db_tables[name] = {"rows": count, "columns": cols}

    # ── Data coverage: nulls in key columns ──
    data_coverage = {}

    # pitcher_game_logs: check opponent_team_id population
    if db_tables.get("pitcher_game_logs", {}).get("rows", 0) > 0:
        total = db_tables["pitcher_game_logs"]["rows"]
        with_opp = conn.execute(
            "SELECT COUNT(*) FROM pitcher_game_logs WHERE opponent_team_id IS NOT NULL"
        ).fetchone()[0]
        recent_dates = conn.execute(
            "SELECT game_date FROM pitcher_game_logs ORDER BY game_date DESC LIMIT 1"
        ).fetchone()
        data_coverage["pitcher_game_logs"] = {
            "total_rows": total,
            "with_opponent_id": with_opp,
            "pct_complete": round(with_opp / total * 100, 1) if total else 0,
            "most_recent": recent_dates["game_date"] if recent_dates else None,
        }

    # team_game_logs: check coverage
    if db_tables.get("team_game_logs", {}).get("rows", 0) > 0:
        total = db_tables["team_game_logs"]["rows"]
        recent_dates = conn.execute(
            "SELECT game_date FROM team_game_logs ORDER BY game_date DESC LIMIT 1"
        ).fetchone()
        games_last_7 = conn.execute(
            "SELECT COUNT(DISTINCT game_date) FROM team_game_logs WHERE game_date >= ?",
            ((today - timedelta(days=7)).isoformat(),)
        ).fetchone()[0]
        data_coverage["team_game_logs"] = {
            "total_rows": total,
            "most_recent": recent_dates["game_date"] if recent_dates else None,
            "game_dates_last_7d": games_last_7,
        }

    # analysis_log: agent score coverage
    if db_tables.get("analysis_log", {}).get("rows", 0) > 0:
        total = db_tables["analysis_log"]["rows"]
        with_pitching = conn.execute(
            "SELECT COUNT(*) FROM analysis_log WHERE score_pitching IS NOT NULL"
        ).fetchone()[0]
        graded = conn.execute(
            "SELECT COUNT(*) FROM analysis_log WHERE ml_status != 'pending'"
        ).fetchone()[0]
        data_coverage["analysis_log"] = {
            "total_rows": total,
            "with_agent_scores": with_pitching,
            "graded_rows": graded,
        }

    # picks: ev_score and odds coverage
    if db_tables.get("picks", {}).get("rows", 0) > 0:
        total = db_tables["picks"]["rows"]
        with_ev = conn.execute(
            "SELECT COUNT(*) FROM picks WHERE ev_score IS NOT NULL"
        ).fetchone()[0]
        with_odds = conn.execute(
            "SELECT COUNT(*) FROM picks WHERE ml_odds IS NOT NULL OR ou_odds IS NOT NULL"
        ).fetchone()[0]
        with_kelly = conn.execute(
            "SELECT COUNT(*) FROM picks WHERE kelly_fraction IS NOT NULL"
        ) if "kelly_fraction" in db_tables.get("picks", {}).get("columns", []) else None
        data_coverage["picks"] = {
            "total_rows": total,
            "with_ev_score": with_ev,
            "with_odds": with_odds,
        }

    # opening_lines: coverage
    if db_tables.get("opening_lines", {}).get("rows", 0) > 0:
        total = db_tables["opening_lines"]["rows"]
        data_coverage["opening_lines"] = {"total_rows": total}

    conn.close()

    # ── Data gaps: what stats could be collected but aren't ──
    data_gaps = []

    pitcher_rows = db_tables.get("pitcher_game_logs", {}).get("rows", 0)
    team_rows = db_tables.get("team_game_logs", {}).get("rows", 0)

    if pitcher_rows < 100:
        data_gaps.append(
            "pitcher_game_logs sparse — less than 100 starter rows; "
            "rolling ERA blending mostly inactive"
        )
    if team_rows < 200:
        data_gaps.append(
            "team_game_logs sparse — rolling team R/G blending may be limited"
        )

    # Check if pitch-level metrics are missing (velocity, spin, etc.)
    pitcher_cols = db_tables.get("pitcher_game_logs", {}).get("columns", [])
    if "avg_velocity" not in pitcher_cols:
        data_gaps.append(
            "pitcher_game_logs missing velocity/spin columns — "
            "pitch quality trends not tracked (TASK: pitcher_velocity_trends)"
        )

    # Check if batter-level logs exist
    if "batter_game_logs" not in db_tables:
        data_gaps.append(
            "No batter_game_logs table — individual batter form/hot-cold streaks "
            "not tracked; confirmed lineup scoring uses team avg only"
        )

    # Check if pitcher-vs-team matchup history exists
    if "pitcher_vs_team_logs" not in db_tables:
        data_gaps.append(
            "No pitcher_vs_team_logs — historical SP ERA vs specific opponent "
            "not tracked beyond rolling logs"
        )

    return {
        "snapshot_date": today.isoformat(),
        "file_summary": file_summary,
        "db_tables": db_tables,
        "data_coverage": data_coverage,
        "data_gaps": data_gaps,
    }


def build_claude_context():
    """
    Build a self-contained context block to prepend to every Claude task prompt.

    Includes current file structure, DB state, and what's already implemented
    so Claude doesn't re-implement completed work or make stale assumptions.
    """
    snap = snapshot_pipeline()
    today = snap["snapshot_date"]

    # Read CLAUDE.md for authoritative current state
    claude_md_path = PROJECT_ROOT / "CLAUDE.md"
    claude_md = claude_md_path.read_text(errors="replace") if claude_md_path.exists() else ""

    # Completed improvements
    completed = sorted(get_completed_ids())

    # DB summary
    db_lines = []
    for tname, info in snap["db_tables"].items():
        db_lines.append(f"  {tname}: {info['rows']} rows | cols: {', '.join(info['columns'][:8])}{'...' if len(info['columns']) > 8 else ''}")

    # Data gaps
    gap_lines = "\n".join(f"  - {g}" for g in snap["data_gaps"]) if snap["data_gaps"] else "  None identified"

    # File sizes
    file_lines = []
    for fname, lines in snap["file_summary"].items():
        if lines is not None:
            file_lines.append(f"  {fname}: {lines} lines")
        else:
            file_lines.append(f"  {fname}: MISSING")

    context = f"""
================================================================================
OPTIMIZER CONTEXT — {today}
This block is auto-generated. It gives you accurate current state so you don't
re-implement completed work or make stale assumptions.
================================================================================

PROJECT ROOT: {PROJECT_ROOT}
Python: 3.9 (no float|None union syntax — use Optional[float] or "float | None")

FILE STRUCTURE:
{chr(10).join(file_lines)}

DATABASE TABLES (mlb_picks.db):
{chr(10).join(db_lines)}

DATA GAPS IDENTIFIED:
{gap_lines}

COMPLETED IMPROVEMENTS (never re-implement these):
{', '.join(completed) if completed else 'none'}

KEY ARCHITECTURE (from CLAUDE.md — read this carefully):
{claude_md[:4000]}
... (see CLAUDE.md for full reference)
================================================================================
END OPTIMIZER CONTEXT
================================================================================

"""
    return context


# ─────────────────────────────────────────────────────────────────────────────
#  ANALYSIS FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def analyze_pick_performance(days=30):
    """Win/loss by confidence bucket and pick type from sent picks."""
    conn = get_db()
    since = (date.today() - timedelta(days=days)).isoformat()

    rows = conn.execute("""
        SELECT p.confidence, p.pick_type, p.status,
               p.edge_pitching, p.edge_offense, p.edge_advanced,
               p.edge_bullpen, p.edge_weather, p.edge_market, p.edge_score
        FROM picks p
        JOIN games g ON p.game_id = g.id
        WHERE g.game_date >= ? AND p.discord_sent = 1
          AND p.status IN ('won', 'lost', 'push')
        ORDER BY g.game_date DESC
    """, (since,)).fetchall()
    conn.close()

    total = len(rows)
    if total == 0:
        return None

    wins = sum(1 for r in rows if r["status"] == "won")
    losses = sum(1 for r in rows if r["status"] == "lost")
    pushes = sum(1 for r in rows if r["status"] == "push")

    by_confidence = {}
    by_type = {}

    for r in rows:
        conf = r["confidence"]
        pt = r["pick_type"]
        s = r["status"]

        if conf not in by_confidence:
            by_confidence[conf] = {"won": 0, "lost": 0, "push": 0}
        by_confidence[conf][s] = by_confidence[conf].get(s, 0) + 1

        if pt not in by_type:
            by_type[pt] = {"won": 0, "lost": 0, "push": 0}
        by_type[pt][s] = by_type[pt].get(s, 0) + 1

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": round(wins / max(wins + losses, 1) * 100, 1),
        "by_confidence": by_confidence,
        "by_type": by_type,
    }


def analyze_model_accuracy(days=30):
    """Full-field ML + O/U accuracy from analysis_log."""
    conn = get_db()
    since = (date.today() - timedelta(days=days)).isoformat()

    rows = conn.execute("""
        SELECT ml_status, ou_status, ml_confidence, ou_confidence
        FROM analysis_log
        WHERE game_date >= ? AND ml_status NOT IN ('pending', 'none')
    """, (since,)).fetchall()
    conn.close()

    if not rows:
        return None

    ml_correct   = sum(1 for r in rows if r["ml_status"] == "correct")
    ml_incorrect = sum(1 for r in rows if r["ml_status"] == "incorrect")
    ou_correct   = sum(1 for r in rows if r["ou_status"] == "correct")
    ou_incorrect = sum(1 for r in rows if r["ou_status"] == "incorrect")

    calibration = {}
    for r in rows:
        conf = r["ml_confidence"]
        if conf not in calibration:
            calibration[conf] = {"correct": 0, "incorrect": 0}
        if r["ml_status"] in ("correct", "incorrect"):
            calibration[conf][r["ml_status"]] += 1

    return {
        "total": len(rows),
        "ml_correct": ml_correct,
        "ml_incorrect": ml_incorrect,
        "ml_accuracy": round(ml_correct / max(ml_correct + ml_incorrect, 1) * 100, 1),
        "ou_correct": ou_correct,
        "ou_incorrect": ou_incorrect,
        "ou_accuracy": round(ou_correct / max(ou_correct + ou_incorrect, 1) * 100, 1),
        "calibration": calibration,
    }


def analyze_agent_signals(days=30):
    """
    Compare avg agent score on winning vs losing picks per agent.
    Reads real float scores from analysis_log (joined via games.mlb_game_id).
    Blends live signal with the 4,855-game backtest baseline.

    Blending weight:
      - < 20 live picks:  100% backtest
      - 20-99 picks:      backtest weighted, live signal slowly introduced
      - 100+ picks:       50/50 blend
      - 200+ picks:       live data takes precedence

    Returns dict: {agent: {differential, live_differential, backtest_lift,
                            blended_differential, n_won, n_lost, blend_weight_live}}
    """
    conn = get_db()
    since = (date.today() - timedelta(days=days)).isoformat()

    rows = conn.execute("""
        SELECT p.status,
               al.score_pitching, al.score_offense, al.score_bullpen,
               al.score_advanced, al.score_momentum, al.score_weather, al.score_market
        FROM picks p
        JOIN games g ON p.game_id = g.id
        JOIN analysis_log al ON g.mlb_game_id = al.mlb_game_id
        WHERE g.game_date >= ? AND p.discord_sent = 1
          AND p.status IN ('won', 'lost')
          AND p.pick_type IN ('moneyline', 'f5_ml')
    """, (since,)).fetchall()
    conn.close()

    backtest_lift = load_backtest_lift()
    n_graded = len(rows)

    # Blend weight for live signal: 0 at <20 picks, ramps to 0.5 at 100, 0.8 at 200
    if n_graded < 20:
        live_weight = 0.0
    elif n_graded < 100:
        live_weight = round((n_graded - 20) / 80 * 0.5, 3)
    elif n_graded < 200:
        live_weight = round(0.5 + (n_graded - 100) / 100 * 0.3, 3)
    else:
        live_weight = 0.8

    agent_col = {
        "pitching": "score_pitching",
        "offense":  "score_offense",
        "bullpen":  "score_bullpen",
        "advanced": "score_advanced",
        "momentum": "score_momentum",
        "weather":  "score_weather",
        "market":   "score_market",
    }
    result = {}

    for agent, col in agent_col.items():
        won_vals, lost_vals = [], []
        for r in rows:
            val = r[col]
            if val is None:
                continue
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
            (won_vals if r["status"] == "won" else lost_vals).append(val)

        avg_won  = sum(won_vals)  / len(won_vals)  if won_vals  else 0.0
        avg_lost = sum(lost_vals) / len(lost_vals) if lost_vals else 0.0
        live_diff = round(avg_won - avg_lost, 4)

        bt_lift = backtest_lift.get(agent)  # None = untestable

        # Blended differential — if backtest is None (untestable), use live only
        if bt_lift is None:
            blended = live_diff if n_graded >= 20 else 0.0
        else:
            blended = round((1 - live_weight) * bt_lift + live_weight * live_diff, 4)

        result[agent] = {
            "live_differential":    live_diff,
            "backtest_lift":        bt_lift,
            "blended_differential": blended,
            "differential":         blended,   # alias used by select_improvement
            "n_won":  len(won_vals),
            "n_lost": len(lost_vals),
            "blend_weight_live":    live_weight,
        }

    return result


def analyze_rolling_data():
    """
    Report on pitcher_game_logs and team_game_logs coverage.

    Returns stats the optimizer uses to:
      - Detect stale data (collect_boxscores didn't run)
      - Know how many pitchers have crossed blend thresholds
      - Flag if rolling data is too sparse to be meaningful
    """
    conn = get_db()
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    cutoff_21d = (today - timedelta(days=21)).isoformat()
    cutoff_14d = (today - timedelta(days=14)).isoformat()

    issues = []

    # ── Pitcher game logs ──
    total_pitcher_rows = conn.execute(
        "SELECT COUNT(*) FROM pitcher_game_logs WHERE is_starter=1"
    ).fetchone()[0]

    yesterday_pitchers = conn.execute(
        "SELECT COUNT(*) FROM pitcher_game_logs WHERE game_date=? AND is_starter=1",
        (yesterday,)
    ).fetchone()[0]

    # How many starters have enough logs for blending (last 21 days)
    blend_counts = conn.execute("""
        SELECT pitcher_id, COUNT(*) as gs
        FROM pitcher_game_logs
        WHERE is_starter=1 AND game_date >= ?
        GROUP BY pitcher_id
    """, (cutoff_21d,)).fetchall()

    pitchers_5plus  = sum(1 for r in blend_counts if r["gs"] >= 5)
    pitchers_10plus = sum(1 for r in blend_counts if r["gs"] >= 10)
    pitchers_20plus = sum(1 for r in blend_counts if r["gs"] >= 20)
    total_starters  = len(blend_counts)

    # ── Team game logs ──
    total_team_rows = conn.execute(
        "SELECT COUNT(*) FROM team_game_logs"
    ).fetchone()[0]

    yesterday_teams = conn.execute(
        "SELECT COUNT(*) FROM team_game_logs WHERE game_date=?",
        (yesterday,)
    ).fetchone()[0]

    # Teams with 5+ games in last 14 days (rolling R/G blend threshold)
    team_blend_counts = conn.execute("""
        SELECT team_id, COUNT(*) as games
        FROM team_game_logs
        WHERE game_date >= ?
        GROUP BY team_id
    """, (cutoff_14d,)).fetchall()

    teams_5plus  = sum(1 for r in team_blend_counts if r["games"] >= 5)
    teams_10plus = sum(1 for r in team_blend_counts if r["games"] >= 10)

    conn.close()

    # Flag stale data
    if yesterday_pitchers == 0 and today.weekday() != 0:  # not Monday (off-day possible)
        issues.append(
            f"No pitcher_game_logs for {yesterday} — collect_boxscores may have failed"
        )
    if yesterday_teams == 0 and today.weekday() != 0:
        issues.append(
            f"No team_game_logs for {yesterday} — collect_boxscores may have failed"
        )

    return {
        "pitcher_logs": {
            "total_starter_rows": total_pitcher_rows,
            "yesterday_starters": yesterday_pitchers,
            "active_starters_21d": total_starters,
            "blend_5plus": pitchers_5plus,   # 40% rolling blend active
            "blend_10plus": pitchers_10plus,  # 60% rolling blend active
            "blend_20plus": pitchers_20plus,  # 75% rolling blend active
        },
        "team_logs": {
            "total_rows": total_team_rows,
            "yesterday_teams": yesterday_teams,
            "teams_5plus_14d": teams_5plus,   # rolling R/G blend active
            "teams_10plus_14d": teams_10plus,
        },
        "issues": issues,
    }


def analyze_log_issues():
    """Scan engine.log for recurring errors and API fallback patterns."""
    if not LOG_PATH.exists():
        return {"issues": [], "error_count": 0, "fallback_count": 0}

    lines = LOG_PATH.read_text(errors="replace").splitlines()[-1000:]
    error_count = 0
    fallback_count = 0
    api_failures = {}
    issues = []

    for line in lines:
        ll = line.lower()
        if "error" in ll:
            error_count += 1
        if "fallback" in ll or "unavailable" in ll or "failed to fetch" in ll:
            fallback_count += 1
        for api in ["statcast", "mlb api", "odds api", "open-meteo"]:
            if api in ll and ("fail" in ll or "error" in ll or "timeout" in ll):
                api_failures[api] = api_failures.get(api, 0) + 1

    if fallback_count > 10:
        issues.append(f"High fallback rate: {fallback_count} fallbacks in recent logs")
    for api, count in api_failures.items():
        if count > 3:
            issues.append(f"{api} failing repeatedly ({count} errors in recent logs)")

    return {
        "issues": issues,
        "error_count": error_count,
        "fallback_count": fallback_count,
        "api_failures": api_failures,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  COMPLETED IMPROVEMENTS TRACKER
# ─────────────────────────────────────────────────────────────────────────────

def get_completed_ids():
    if not COMPLETED_PATH.exists():
        return set()
    return set(re.findall(r'<!-- id: (\w+) -->', COMPLETED_PATH.read_text()))


def mark_complete(improvement_id, name, description):
    entry = (
        f"\n## {date.today().isoformat()} — {name}\n"
        f"<!-- id: {improvement_id} -->\n"
        f"{description}\n"
    )
    if not COMPLETED_PATH.exists():
        COMPLETED_PATH.write_text("# Completed Weekly Improvements\n\n")
    with open(COMPLETED_PATH, "a") as f:
        f.write(entry)


# ─────────────────────────────────────────────────────────────────────────────
#  IMPROVEMENT IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────────────────────

def apply_weight_rebalance(signal: dict) -> dict:
    """
    Nudge WEIGHTS toward agents with strongest live differential.
    Max shift: ±0.02 per agent per week. Weights always sum to 1.0.
    """
    current = dict(WEIGHTS)
    adjustable = ["pitching", "offense", "bullpen", "advanced", "momentum", "market"]

    # Compute desired direction per agent
    adjustments = {}
    for agent in adjustable:
        if agent not in signal:
            adjustments[agent] = 0.0
            continue
        diff = signal[agent]["differential"]
        # Positive diff → agent reliably contributes to wins → slight boost
        # Negative diff → agent misleads → slight reduction
        adjustments[agent] = max(-0.02, min(0.02, diff * 0.1))

    # Normalize so sum stays at 1.0
    total_shift = sum(adjustments.values())
    # Spread residual evenly across weather (fixed minor weight)
    new_weights = {}
    for k in current:
        new_weights[k] = round(current[k] + adjustments.get(k, 0.0), 4)
    new_weights["weather"] = round(1.0 - sum(v for k, v in new_weights.items() if k != "weather"), 4)

    # Clamp to valid range
    for k in new_weights:
        new_weights[k] = max(0.02, min(0.40, new_weights[k]))

    # Re-normalize to exactly 1.0
    total = sum(new_weights.values())
    for k in new_weights:
        new_weights[k] = round(new_weights[k] / total, 4)
    # Fix rounding remainder on pitching
    remainder = round(1.0 - sum(new_weights.values()), 4)
    new_weights["pitching"] = round(new_weights["pitching"] + remainder, 4)

    changes = {k: (current[k], new_weights[k]) for k in current if current[k] != new_weights[k]}

    # Write to config.py
    config_path = PROJECT_ROOT / "config.py"
    content = config_path.read_text()

    weights_block = "WEIGHTS = {\n"
    for k, v in new_weights.items():
        old_comment = ""
        for line in content.split("\n"):
            if f'"{k}"' in line and "#" in line:
                old_comment = line.split("#", 1)[1].strip()
                break
        comment = f"  # live signal rebalance {date.today().isoformat()}"
        weights_block += f'    "{k}":    {v},{comment}\n'
    weights_block += "}"

    new_content = re.sub(r'WEIGHTS\s*=\s*\{[^}]+\}', weights_block, content, flags=re.DOTALL)
    config_path.write_text(new_content)

    return {"changes": changes, "new_weights": new_weights}


def apply_threshold_tune(direction: str, current_win_rate: float) -> dict:
    """Adjust MIN_CONFIDENCE by ±1 based on live win rate signal."""
    config_path = PROJECT_ROOT / "config.py"
    content = config_path.read_text()

    current = MIN_CONFIDENCE
    if direction == "down":
        new_val = max(6, current - 1)
    else:
        new_val = min(9, current + 1)

    if new_val == current:
        return {"changed": False, "reason": "Already at boundary"}

    new_content = re.sub(
        r'(MIN_CONFIDENCE\s*=\s*)\d+',
        f"\\g<1>{new_val}",
        content
    )
    config_path.write_text(new_content)
    return {"changed": True, "old": current, "new": new_val, "win_rate": current_win_rate}


def apply_via_claude(task_prompt: str) -> dict:
    """
    Dispatch a code improvement task to Claude Code CLI in non-interactive mode.
    Prepends a full pipeline context block so Claude has accurate current state.
    Returns dict with success, output, and what changed (git diff summary).
    """
    if not Path(CLAUDE_BIN).exists():
        return {"success": False, "error": "claude CLI not found"}

    try:
        context = build_claude_context()
        full_prompt = context + task_prompt
    except Exception as e:
        print(f"[OPTIMIZER] Warning: could not build context ({e}) — using raw prompt")
        full_prompt = task_prompt

    try:
        result = subprocess.run(
            [
                CLAUDE_BIN, "-p", full_prompt,
                "--dangerously-skip-permissions",
                "--model", "claude-sonnet-4-6",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr[:500]}

        # Get git diff summary
        diff = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True
        ).stdout.strip()

        return {
            "success": True,
            "output": result.stdout[-1000:],  # last 1000 chars
            "diff_stat": diff,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Claude task timed out after 10 minutes"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  CLAUDE TASK PROMPTS
#  Each prompt is self-contained — Claude needs no session context.
# ─────────────────────────────────────────────────────────────────────────────

TASK_API_ERROR_HANDLING = textwrap.dedent("""
    You are fixing a data quality issue in the MLB picks engine at /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.

    TASK: Add retry logic and structured error logging to all external API calls.

    CURRENT STATE:
    - data_mlb.py and data_odds.py make external API calls with basic try/except but no retry
    - engine.log shows recurring "Error fetching travel context: fromisoformat: argument must be str" for all 15 teams — this fires every run and is never retried
    - Some failures fall back silently (no WARNING logged) making it hard to diagnose
    - The travel context error is a date parsing bug: fetch_travel_context() receives game_date as a non-string (likely a date object) and calls fromisoformat() on it

    WHAT TO FIX:
    1. Fix the travel context bug in data_mlb.py fetch_travel_context():
       - The function calls fromisoformat() on game_date — guard with str(game_date) before parsing
       - This is causing the repeated error for all 15 teams every run

    2. For functions that call external APIs (MLB Stats API, Statcast, Open-Meteo, The Odds API):
       - Add retry logic (max 2 retries, 2s delay) for ConnectionError and Timeout only
       - On final failure after retries: log [WARNING] with function name and error
       - Keep existing fallback return values (None, {}, []) — just add the warning log

    3. Do not change business logic, agent scoring, or return value shapes.

    CONSTRAINTS:
    - Run: pytest tests/ -v after changes — all existing tests must pass
    - Commit: "fix: add API retry logic and fix travel context date parsing bug"
""").strip()


TASK_CORRELATED_PICK_CAP = textwrap.dedent("""
    You are implementing one improvement to the MLB picks engine at /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.

    TASK: Add a correlated pick cap — prevent sending both ML and O/U picks for the same game.

    CURRENT STATE:
    - analysis.py risk_filter() can approve both a moneyline pick AND an over/under pick for the same game
    - Also possible: both ML and F5 ML for same game
    - These are highly correlated bets — if the ML pick wins, the O/U result is already partially determined by the same factors
    - Sending both picks inflates apparent pick count and creates correlated risk exposure
    - MAX_PICKS_PER_DAY = 5 in config.py; correlated picks should count as 1.5 slots

    WHAT TO BUILD:
    1. In analysis.py risk_filter(): after the existing confidence/edge/EV gates:
       - Sort approved picks by confidence descending (already done)
       - When selecting picks to send (up to MAX_PICKS_PER_DAY=5):
         * If a game already has an approved ML pick, a same-game O/U is allowed but counts as 0.5 extra slots
         * If a game already has an approved ML pick, a same-game F5 ML is NOT added (too correlated)
         * Maximum 1 F5 pick total per day (already implied by pitching_score threshold, but enforce explicitly)
       - Log when a pick is dropped due to correlation: "[CORR CAP] Skipped {pick} — correlated with {existing}"

    2. In config.py: add ALLOW_SAME_GAME_OU = True (lets operator toggle whether same-game O/U is allowed alongside ML)

    3. No Discord format changes needed.

    CONSTRAINTS:
    - If ALLOW_SAME_GAME_OU = True (default): ML + O/U same game is allowed but counts as 1.5 slots toward MAX_PICKS
    - F5 + ML same game: always blocked (too correlated)
    - Run: pytest tests/ -v after changes; add at least 2 tests for the cap logic
    - Commit: "feat: add correlated pick cap — prevent same-game ML+F5, cap ML+OU"
""").strip()


TASK_PITCHER_VELOCITY_TRENDS = textwrap.dedent("""
    You are implementing one improvement to the MLB picks engine at /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.

    TASK: Add pitcher velocity trend signal to the pitching agent.

    CURRENT STATE:
    - analysis.py score_pitching() uses ERA/WHIP/K9/BB9 and opponent-adjusted rolling ERA
    - No signal for whether a pitcher's stuff is trending up or down within the season
    - Baseball Savant already provides pitcher-level Statcast data (xERA endpoint already fetched)
    - pitcher_game_logs table has per-start data: IP, ER, K, BB, H, HR

    WHAT TO BUILD:
    1. In database.py: add get_pitcher_velocity_trend(pitcher_id, days=21) that:
       - Queries pitcher_game_logs for last N days (is_starter=1)
       - Computes K/9 trend: avg K/9 in last 2 starts vs avg K/9 in starts 3-5 prior
       - Returns: {"k9_trend": float, "recent_k9": float, "prior_k9": float, "starts": int}
         k9_trend > 0 = improving velo/stuff; < 0 = declining
       - Returns None if fewer than 4 starts available

    2. In analysis.py score_pitching(): incorporate velocity trend when available:
       - k9_trend >= +1.5 (K/9 up by 1.5+): small bonus +0.04 for that pitcher
       - k9_trend <= -1.5 (K/9 down by 1.5+): small penalty -0.04
       - Applied symmetrically to home/away SP
       - Include in edge string: "SP K/9 trend: +1.8 (improving)" when triggered

    3. In data_mlb.py collect_game_data(): call get_pitcher_velocity_trend() for both starters,
       attach as game_info["away_pitcher_trend"] and game_info["home_pitcher_trend"]

    4. In tests/: add at least 3 tests:
       - get_pitcher_velocity_trend returns correct keys and computes trend correctly
       - Returns None with fewer than 4 starts
       - score_pitching applies bonus/penalty correctly

    CONSTRAINTS:
    - Requires pitcher_game_logs data — graceful None return when insufficient data
    - Do not change agent weights in config.py
    - Run: pytest tests/ -v after changes
    - Commit: "feat: add pitcher K/9 velocity trend signal to pitching agent"
""").strip()


TASK_BATTER_GAME_LOGS = textwrap.dedent("""
    TASK: Add per-batter game logs table and hot/cold streak signal to the offense agent.

    WHY: The offense agent currently uses team-level season OPS/OBP/SLG and team rolling R/G.
    It has no visibility into individual batter form. A team with 3 hot bats in the lineup
    is meaningfully different from the same team mid-slump, even if season averages look identical.
    The confirmed lineup scoring adjusts for lineup OPS vs team avg but has no recency signal.

    WHAT TO BUILD:

    1. In database.py: add batter_game_logs table:
       CREATE TABLE IF NOT EXISTS batter_game_logs (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           mlb_game_id INTEGER,
           game_date TEXT,
           batter_id INTEGER,
           batter_name TEXT,
           team_id INTEGER,
           at_bats INTEGER,
           hits INTEGER,
           doubles INTEGER,
           triples INTEGER,
           home_runs INTEGER,
           rbi INTEGER,
           walks INTEGER,
           strikeouts INTEGER,
           created_at TEXT,
           UNIQUE(mlb_game_id, batter_id)
       )
       Add index: idx_batter_logs_batter ON batter_game_logs(batter_id, game_date)
       Add index: idx_batter_logs_team ON batter_game_logs(team_id, game_date)

    2. In database.py: add collect_batter_boxscores(game_date) that:
       - Calls /game/{gamePk}/boxscore for each completed game on game_date (same endpoint as pitcher logs)
       - Parses teams.{side}.batters + teams.{side}.players[id].stats.batting
       - Extracts: atBats, hits, doubles, triples, homeRuns, rbi, baseOnBalls, strikeOuts
       - Skips pitchers (is_starter or IP > 0) — batters only
       - INSERT OR IGNORE (idempotent)
       - Returns count of rows inserted

    3. In database.py: add get_team_batter_hot_cold(team_id, days=10):
       - Queries batter_game_logs for team's batters in last N days
       - For each batter with >= 3 at_bats total in window: compute BA and OPS proxy
       - "Hot" batter: BA >= .320 in last 10d with >= 12 AB
       - "Cold" batter: BA <= .180 in last 10d with >= 12 AB
       - Returns: {"hot_count": int, "cold_count": int, "avg_ba_10d": float, "sample_abs": int}
       - Returns None if fewer than 30 total AB in window

    4. In engine.py: call collect_batter_boxscores(today) at end of --results run
       (same place collect_boxscores is called for pitchers)

    5. In analysis.py score_offense(): incorporate hot/cold signal when available:
       - Call get_team_batter_hot_cold() for both teams
       - hot_count - cold_count >= 2: slight bonus +0.04 for that team's offense
       - cold_count - hot_count >= 2: slight penalty -0.04
       - Include in edge string: "3 batters hot (last 10d)" when triggered
       - Graceful fallback: if None, score_offense unchanged

    6. In tests/: add test_batter_logs.py with at least 4 tests:
       - batter_game_logs table created by init_db
       - collect_batter_boxscores parses boxscore correctly (mock API response)
       - get_team_batter_hot_cold returns correct keys
       - get_team_batter_hot_cold returns None with insufficient data

    CONSTRAINTS:
    - collect_batter_boxscores uses same /game/{gamePk}/boxscore endpoint already used by collect_boxscores — do not add a new API endpoint
    - Batters only: skip any player with pitching stats (check players[id].stats.pitching)
    - Do not change agent weights or thresholds
    - Run: pytest tests/ -v after changes
    - Commit: "feat: add batter game logs and hot/cold streak signal to offense agent"
""").strip()


TASK_PITCHER_VS_TEAM = textwrap.dedent("""
    TASK: Track pitcher historical performance vs specific opponents and use it in the pitching agent.

    WHY: The pitching agent uses season ERA, home/away splits, and opponent-adjusted rolling ERA.
    But it has no record of how a specific SP performs against a specific team historically.
    Some pitchers consistently dominate certain lineups; others struggle against specific teams
    regardless of their season ERA. This matchup history is a meaningful independent signal.

    WHAT TO BUILD:

    1. In database.py: the pitcher_game_logs table already has opponent_team_id.
       Add get_pitcher_vs_team_history(pitcher_id, opponent_team_id, days=365):
       - Queries pitcher_game_logs WHERE pitcher_id=? AND opponent_team_id=? AND game_date >= ?
       - Requires >= 2 starts vs this opponent in window
       - Returns: {"starts": int, "era_vs_team": float, "whip_vs_team": float,
                   "k9_vs_team": float, "avg_ip": float}
         era_vs_team = (earned_runs * 9) / max(innings_pitched, 0.1)
         whip_vs_team = (hits + walks) / max(innings_pitched, 0.1)
         k9_vs_team = strikeouts * 9 / max(innings_pitched, 0.1)
       - Returns None if fewer than 2 starts

    2. In analysis.py score_pitching(): incorporate matchup history when available:
       - Call get_pitcher_vs_team_history() for away SP vs home team, home SP vs away team
       - Compare era_vs_team to pitcher's rolling ERA:
         era_vs_team < (rolling_era - 0.75): SP historically dominates this lineup → +0.06
         era_vs_team > (rolling_era + 0.75): SP historically struggles vs this lineup → -0.06
       - Include in edge string: "SP ERA vs this team (3 starts): 1.42 — dominates" when triggered
       - Graceful fallback: if None (too few starts), no adjustment

    3. In data_mlb.py collect_game_data(): add opponent team IDs so analysis.py can call
       get_pitcher_vs_team_history. The game dict already has away_team_id and home_team_id
       as integer MLB team IDs in g["away_team_id"] / g["home_team_id"] — use these directly.
       No new API calls needed.

    4. In tests/: add test_pitcher_vs_team.py with at least 3 tests:
       - get_pitcher_vs_team_history computes ERA/WHIP/K9 correctly from mock logs
       - Returns None with fewer than 2 starts
       - score_pitching applies bonus/penalty correctly

    CONSTRAINTS:
    - No new tables — uses existing pitcher_game_logs with opponent_team_id column
    - No new API calls — data already collected nightly via collect_boxscores
    - Do not change agent weights or thresholds
    - Run: pytest tests/ -v after changes
    - Commit: "feat: add pitcher-vs-team matchup history to pitching agent"
""").strip()


TASK_TEAM_SITUATIONAL_STATS = textwrap.dedent("""
    TASK: Add situational team stats (home/away splits, day/night, last 7 days) to the offense and momentum agents.

    WHY: The team_game_logs table accumulates per-game team batting stats (R, H, HR, K, BB, AB).
    Currently only rolling R/G is used. But team_game_logs enables richer situational splits
    that are directly relevant to today's game: how does this team hit at home vs away?
    How are they performing specifically in the last 7 days vs last 30?

    WHAT TO BUILD:

    1. In database.py: add get_team_situational_stats(team_id, is_away, days_recent=7, days_season=60):
       - Queries team_game_logs for team_id
       - Returns:
         * recent_rpg: avg runs/game in last days_recent days (is_away matches today's role)
         * recent_ops_proxy: (hits + walks) / at_bats for last days_recent days (OBP proxy)
         * recent_k_rate: strikeouts / at_bats for last days_recent days
         * home_away_rpg: avg runs/game in home or away games over last days_season days
         * recent_games: count of games in recent window
       - Returns None if fewer than 3 games in recent window

    2. In analysis.py score_offense(): update to use situational stats:
       - Call get_team_situational_stats(team_id, is_away=True/False) for both teams
       - Replace or blend rolling R/G (currently from get_team_rolling_stats) with situational recent_rpg
       - Use home_away_rpg as the primary rolling signal (more relevant than overall rolling R/G):
         blend = 0.5 * season_rpg + 0.3 * home_away_rpg + 0.2 * recent_rpg
       - High recent_k_rate (>= 0.27): slight penalty -0.03 (team struggling to make contact)
       - Graceful fallback: if None, use existing rolling R/G logic unchanged

    3. In analysis.py score_momentum(): use recent_rpg as a momentum signal:
       - If recent_rpg significantly above season_rpg (>= +0.8 runs/game over 7d): small hot streak bonus +0.04
       - If significantly below (<= -0.8 runs/game over 7d): small cold streak penalty -0.04
       - This supplements the existing win streak signal

    4. In tests/: add test_situational_stats.py with at least 3 tests:
       - get_team_situational_stats returns correct averages from mock data
       - Returns None with fewer than 3 games
       - score_offense applies home_away_rpg blend correctly

    CONSTRAINTS:
    - Uses existing team_game_logs — no new tables or API calls
    - Requires team_game_logs to be populated (fails gracefully if sparse)
    - Do not change agent weights or thresholds
    - Run: pytest tests/ -v after changes
    - Commit: "feat: add team situational stats from game logs to offense and momentum agents"
""").strip()


# ─────────────────────────────────────────────────────────────────────────────
#  PRIORITIZER — picks the best improvement this week
# ─────────────────────────────────────────────────────────────────────────────

def select_improvement(perf, model, log_issues, signal):
    """
    Return the highest-priority actionable improvement.

    Runs nightly. Config changes (weights/thresholds) re-evaluate daily but
    are throttled to once per 7 days so the system can measure each change.
    Code changes (Claude CLI) are also throttled to once per 7 days.
    Data analysis and Discord reporting always happen regardless.
    """
    completed = get_completed_ids()
    graded = perf["total"] if perf else 0
    days_since_action = _days_since_last_optimizer_commit()

    # 1. Data quality — always highest priority, but still respect code throttle
    if log_issues["issues"] and "api_error_handling" not in completed:
        if days_since_action >= 7:
            return {
                "id": "api_error_handling",
                "name": "API Error Handling & Retry Logic",
                "description": "Reduce fallback rate by adding retries and fixing travel context date parsing bug",
                "type": "claude",
                "task": TASK_API_ERROR_HANDLING,
                "priority_reason": f"Log issues: {'; '.join(log_issues['issues'][:2])}",
            }

    # 2. Weight rebalance — need 20+ graded picks, strong signal, and 7-day cooldown
    # No one-shot completed guard — weights should re-evaluate as live data accumulates
    if graded >= 20 and signal and days_since_action >= 7:
        best = max(signal.items(), key=lambda x: abs(x[1]["differential"]))
        agent, sig = best
        if abs(sig["differential"]) > 0.05:
            return {
                "id": "weight_rebalance",
                "name": "Agent Weight Rebalance",
                "description": (
                    f"Live data ({graded} picks): {agent} has "
                    f"strongest differential ({sig['differential']:+.4f}). "
                    f"Nudging weights toward signal."
                ),
                "type": "config_weights",
                "signal": signal,
                "priority_reason": f"{graded} graded picks, {agent} differential {sig['differential']:+.4f}",
            }

    # 3. Threshold tuning — need 30+ graded picks and 7-day cooldown
    # No one-shot completed guard — thresholds should re-evaluate as data grows
    if graded >= 30 and perf and days_since_action >= 7:
        if perf["win_rate"] < 50.0:
            return {
                "id": "threshold_up",
                "name": "Raise Confidence Threshold",
                "description": f"Win rate {perf['win_rate']}% below breakeven. Raising MIN_CONFIDENCE.",
                "type": "config_threshold",
                "direction": "up",
                "win_rate": perf["win_rate"],
                "priority_reason": f"Win rate {perf['win_rate']}% < 50% over {graded} picks",
            }
        # If confidence 7 picks are hitting 65%+, loosen threshold
        calib = model["calibration"] if model else {}
        conf7 = calib.get(7, {})
        c7_total = conf7.get("correct", 0) + conf7.get("incorrect", 0)
        if c7_total >= 10:
            c7_rate = conf7.get("correct", 0) / c7_total
            if c7_rate >= 0.65:
                return {
                    "id": "threshold_down",
                    "name": "Lower Confidence Threshold",
                    "description": f"Conf 7 picks hitting {c7_rate*100:.0f}% ({c7_total} picks). Lowering MIN_CONFIDENCE.",
                    "type": "config_threshold",
                    "direction": "down",
                    "win_rate": c7_rate * 100,
                    "priority_reason": f"Conf 7 hitting {c7_rate*100:.0f}% over {c7_total} picks — well above 52% target",
                }

    # 4. Ordered code improvements — one per 7-day window, never repeat
    code_queue = [
        # Structural / risk management
        # api_error_handling — COMPLETED 2026-04-13 (retry logic in data_mlb.py + data_odds.py)
        # correlated_pick_cap — REMOVED: ML + O/U are now intentionally independent picks (design decision 2026-04-13)
        # Per-game data collection → smarter picks over time
        ("batter_game_logs",          "Batter Game Logs & Hot/Cold Streaks",      TASK_BATTER_GAME_LOGS),
        ("pitcher_vs_team",           "Pitcher vs Team Matchup History",          TASK_PITCHER_VS_TEAM),
        ("team_situational_stats",    "Team Situational Stats (Home/Away/Recent)", TASK_TEAM_SITUATIONAL_STATS),
        # Signal improvements (require sufficient game log data first)
        ("pitcher_velocity_trends",   "Pitcher Velocity Trend Signal",            TASK_PITCHER_VELOCITY_TRENDS),
    ]

    for imp_id, imp_name, task in code_queue:
        if imp_id not in completed:
            if days_since_action < 7:
                return {
                    "id": "cooldown",
                    "name": "Cooldown — Code Change Throttle",
                    "description": (
                        f"Next queued improvement is '{imp_name}', but a code change "
                        f"was applied {days_since_action}d ago. Waiting for 7-day window."
                    ),
                    "type": "report_only",
                    "priority_reason": (
                        f"Code throttle: last change {days_since_action}d ago "
                        f"(need 7d gap to measure impact)"
                    ),
                }
            return {
                "id": imp_id,
                "name": imp_name,
                "description": f"Implementing known gap: {imp_name}",
                "type": "claude",
                "task": task,
                "priority_reason": "Next item in code improvement queue",
            }

    # 5. Nothing left — full maintenance pass
    return {
        "id": "maintenance",
        "name": "Pipeline Health Review",
        "description": "All planned improvements complete. No new code changes needed.",
        "type": "report_only",
        "priority_reason": "All improvements in queue are done",
    }


# ─────────────────────────────────────────────────────────────────────────────
#  TEST RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run_tests():
    """Run pytest and return (passed, output)."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    passed = result.returncode == 0
    # Last 20 lines of output
    output = "\n".join(result.stdout.splitlines()[-20:])
    return passed, output


# ─────────────────────────────────────────────────────────────────────────────
#  GIT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _days_since_last_optimizer_commit() -> int:
    """
    Return days since the last optimizer-applied git commit.
    All optimizer commits include 'optimizer:' in the message.
    Returns 999 when no optimizer commit found in the last 90 days.
    """
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=short",
             "--grep=optimizer:"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        last = result.stdout.strip()
        if last:
            return (date.today() - date.fromisoformat(last)).days
    except Exception:
        pass
    return 999


def git_commit(message: str):
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(PROJECT_ROOT), capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=str(PROJECT_ROOT), capture_output=True
    )


def git_diff_stat():
    r = subprocess.run(
        ["git", "diff", "HEAD~1", "--stat"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True
    )
    return r.stdout.strip()


def git_revert():
    subprocess.run(
        ["git", "checkout", "--", "."],
        cwd=str(PROJECT_ROOT), capture_output=True
    )


# ─────────────────────────────────────────────────────────────────────────────
#  DISCORD REPORT
# ─────────────────────────────────────────────────────────────────────────────

def send_discord_report(report: dict):
    perf   = report.get("perf")
    model  = report.get("model")
    signal = report.get("signal")
    imp    = report.get("improvement")
    result = report.get("result", {})
    week   = date.today().strftime("%B %d, %Y")

    # Performance vs backtest baseline
    bt_win_rate = round(BACKTEST_REFERENCE["overall_win_rate"] * 100, 1)
    if perf:
        delta = round(perf["win_rate"] - bt_win_rate, 1)
        delta_str = f"{delta:+.1f}pp vs backtest ({bt_win_rate}%)"
        perf_block = (
            f"**Picks (30d):** {perf['wins']}W-{perf['losses']}L-{perf['pushes']}P "
            f"({perf['win_rate']}% win rate, {delta_str})"
        )
    else:
        perf_block = f"**Picks:** No graded picks yet (backtest baseline: {bt_win_rate}%)"

    if model:
        model_block = (
            f"**Model ML:** {model['ml_correct']}W-{model['ml_incorrect']}L "
            f"({model['ml_accuracy']}% accuracy, {model['total']} games)\n"
            f"**Model O/U:** {model['ou_correct']}W-{model['ou_incorrect']}L "
            f"({model['ou_accuracy']}% accuracy)"
        )
    else:
        model_block = "**Model:** No graded games yet"

    # Agent signal comparison: live vs backtest
    signal_block = ""
    if signal:
        live_w = signal.get("pitching", {}).get("blend_weight_live", 0)
        blend_pct = round(live_w * 100)
        bt_pct = 100 - blend_pct
        signal_lines = [f"**Agent Signals** (blend: {bt_pct}% backtest / {blend_pct}% live):"]
        for agent in ["pitching", "bullpen", "offense", "advanced", "momentum", "market"]:
            if agent not in signal:
                continue
            s = signal[agent]
            bt = s.get("backtest_lift")
            live = s.get("live_differential", 0.0)
            blended = s.get("blended_differential", 0.0)
            bt_str  = f"{bt:+.3f}" if bt is not None else "N/A"
            diverge = ""
            if bt is not None and s["n_won"] + s["n_lost"] >= 5:
                gap = live - bt
                if abs(gap) >= 0.03:
                    diverge = f" ⚠️ live diverging {gap:+.3f}"
            signal_lines.append(
                f"- {agent}: backtest={bt_str} | live={live:+.3f} | blended={blended:+.3f}{diverge}"
            )
        signal_block = "\n".join(signal_lines) + "\n\n"

    # Rolling data coverage block
    rolling_data = report.get("rolling")
    rolling_block = ""
    if rolling_data:
        pl = rolling_data["pitcher_logs"]
        tl = rolling_data["team_logs"]
        stale_warn = " ⚠️ STALE" if rolling_data["issues"] else ""
        rolling_block = (
            f"**Rolling Stats Pipeline{stale_warn}:**\n"
            f"- Pitcher logs: {pl['total_starter_rows']} rows | "
            f"yesterday: {pl['yesterday_starters']} SPs | "
            f"blend-ready (21d): {pl['blend_5plus']} SPs ≥5gs / "
            f"{pl['blend_10plus']} ≥10gs / {pl['blend_20plus']} ≥20gs\n"
            f"- Team logs: {tl['total_rows']} rows | "
            f"yesterday: {tl['yesterday_teams']} teams | "
            f"{tl['teams_5plus_14d']}/30 teams blend-ready (14d)\n"
        )
        if rolling_data["issues"]:
            rolling_block += f"- ⚠️ {'; '.join(rolling_data['issues'])}\n"
        # Add batter logs if they exist
        snap_data = report.get("snap", {})
        batter_rows = snap_data.get("db_tables", {}).get("batter_game_logs", {}).get("rows")
        if batter_rows is not None:
            rolling_block += f"- Batter logs: {batter_rows} rows\n"
        # Data gaps summary
        gaps = snap_data.get("data_gaps", [])
        if gaps:
            rolling_block += f"- Gaps identified: {len(gaps)} — next queue items address these\n"
        rolling_block += "\n"

    # Improvement result block
    imp_name   = imp.get("name", "Unknown") if imp else "None"
    imp_reason = imp.get("priority_reason", "") if imp else ""

    if result.get("skipped"):
        action_block = f"**Tonight:** No action — {result.get('reason', 'unknown')}"
    elif result.get("success"):
        diff = result.get("diff_stat", "").strip()
        action_block = (
            f"**Tonight:** ✅ {imp_name}\n"
            f"**Why:** {imp_reason}\n"
        )
        if diff:
            action_block += f"```\n{diff[:400]}\n```"
        action_block += "\n✅ All tests passing" if result.get("tests_passed") else \
                        f"\n⚠️ Tests:\n```{result.get('test_output','')[:200]}```"
    else:
        action_block = (
            f"**Tonight:** ❌ {imp_name} — FAILED\n"
            f"**Error:** {result.get('error', 'unknown')[:200]}"
        )

    msg = (
        f"⚙️ **MLB ENGINE — DAILY OPTIMIZER REPORT**\n"
        f"**{week}** | Backtest baseline: {BACKTEST_REFERENCE['total_games']:,} games "
        f"({', '.join(str(s) for s in BACKTEST_REFERENCE['seasons'])})\n\n"
        f"**Pipeline Performance (Last 30 Days):**\n"
        f"{perf_block}\n"
        f"{model_block}\n\n"
        f"{rolling_block}"
        f"{signal_block}"
        f"{action_block}"
    )

    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": msg},
            timeout=10
        )
        if resp.status_code == 204:
            print("[DISCORD] Daily optimizer report sent.")
        else:
            print(f"[DISCORD] Failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"[DISCORD] Error sending report: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"  MLB DAILY OPTIMIZER — {date.today().strftime('%B %d, %Y')}")
    print("=" * 60)

    # ── 1. Analysis ──
    print("\n[1/4] Analyzing pipeline performance...")
    perf   = analyze_pick_performance(days=30)
    model   = analyze_model_accuracy(days=30)
    signal  = analyze_agent_signals(days=30)
    log     = analyze_log_issues()
    rolling = analyze_rolling_data()

    snap = snapshot_pipeline()

    graded = perf["total"] if perf else 0
    print(f"  Graded picks (30d): {graded}")
    if model:
        print(f"  Model ML accuracy: {model['ml_accuracy']}% ({model['total']} games)")
    if log["issues"]:
        print(f"  Log issues: {log['issues']}")
    else:
        print(f"  Log: clean (errors={log['error_count']}, fallbacks={log['fallback_count']})")

    pl = rolling["pitcher_logs"]
    tl = rolling["team_logs"]
    print(f"  Rolling data — pitchers: {pl['total_starter_rows']} rows, "
          f"blend active: {pl['blend_5plus']} SPs (40%), {pl['blend_10plus']} (60%), {pl['blend_20plus']} (75%)")
    print(f"  Rolling data — teams: {tl['total_rows']} rows, "
          f"{tl['teams_5plus_14d']}/30 teams blend-ready (14d)")
    if rolling["issues"]:
        print(f"  Rolling data issues: {rolling['issues']}")
    if snap["data_gaps"]:
        print(f"  Data gaps: {len(snap['data_gaps'])} identified")
        for g in snap["data_gaps"]:
            print(f"    - {g}")

    # ── 2. Select improvement ──
    print("\n[2/4] Selecting improvement...")
    # Merge rolling data issues into log issues for priority selection
    combined_log = dict(log)
    combined_log["issues"] = log["issues"] + rolling["issues"]

    improvement = select_improvement(perf, model, combined_log, signal)
    print(f"  Selected: [{improvement['id']}] {improvement['name']}")
    print(f"  Reason: {improvement['priority_reason']}")

    result = {}

    # ── 3. Implement ──
    print("\n[3/4] Implementing...")
    imp_type = improvement["type"]

    if imp_type == "report_only":
        result = {"skipped": True, "reason": improvement["description"]}
        print("  Nothing to implement this week.")

    elif imp_type == "config_weights":
        impl = apply_weight_rebalance(improvement["signal"])
        changes = impl.get("changes", {})
        if changes:
            tests_passed, test_output = run_tests()
            if tests_passed:
                git_commit(
                    f"chore: optimizer: weight rebalance {date.today().isoformat()} "
                    f"— {', '.join(f'{k}: {v[0]}→{v[1]}' for k,v in changes.items())}"
                )
                diff = git_diff_stat()
                mark_complete(improvement["id"], improvement["name"], improvement["description"])
                result = {"success": True, "diff_stat": diff, "tests_passed": True, "changes": changes}
                print(f"  Weights adjusted: {changes}")
            else:
                git_revert()
                result = {"success": False, "error": "Tests failed after weight change", "test_output": test_output}
                print(f"  Tests failed — reverted.")
        else:
            result = {"skipped": True, "reason": "Weights already at boundary, no change needed"}

    elif imp_type == "config_threshold":
        impl = apply_threshold_tune(improvement["direction"], improvement["win_rate"])
        if impl.get("changed"):
            tests_passed, test_output = run_tests()
            if tests_passed:
                git_commit(
                    f"chore: optimizer: adjust MIN_CONFIDENCE {impl['old']}→{impl['new']} "
                    f"based on {improvement['win_rate']:.1f}% win rate"
                )
                diff = git_diff_stat()
                mark_complete(improvement["id"], improvement["name"], improvement["description"])
                result = {"success": True, "diff_stat": diff, "tests_passed": True}
                print(f"  MIN_CONFIDENCE: {impl['old']} → {impl['new']}")
            else:
                git_revert()
                result = {"success": False, "error": "Tests failed after threshold change", "test_output": test_output}
        else:
            result = {"skipped": True, "reason": impl.get("reason", "No change needed")}

    elif imp_type == "claude":
        print(f"  Dispatching to Claude Code CLI...")
        impl = apply_via_claude(improvement["task"])
        if impl.get("success"):
            tests_passed, test_output = run_tests()
            if tests_passed:
                git_commit(f"feat: optimizer: {improvement['name']}")
                diff = git_diff_stat()
                mark_complete(improvement["id"], improvement["name"], improvement["description"])
                result = {
                    "success": True,
                    "diff_stat": impl.get("diff_stat", diff),
                    "tests_passed": True,
                }
                print(f"  ✅ Claude implementation succeeded.")
            else:
                # Tests failed — revert Claude's changes
                git_revert()
                result = {
                    "success": False,
                    "error": "Tests failed after Claude implementation — reverted",
                    "test_output": test_output,
                }
                print(f"  ❌ Tests failed — reverted Claude's changes.")
        else:
            result = {"success": False, "error": impl.get("error", "Unknown error")}
            print(f"  ❌ Claude implementation failed: {impl.get('error')}")

    # ── 4. Discord report ──
    print("\n[4/4] Sending Discord report...")
    send_discord_report({
        "perf":        perf,
        "model":       model,
        "signal":      signal,
        "rolling":     rolling,
        "snap":        snap,
        "improvement": improvement,
        "result":      result,
    })

    print("\n✅ Daily optimizer complete.")


if __name__ == "__main__":
    main()
