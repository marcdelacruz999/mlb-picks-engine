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
    Compare avg edge value on winning vs losing picks per agent.
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
        SELECT p.status, p.edge_pitching, p.edge_offense, p.edge_advanced,
               p.edge_bullpen, p.edge_weather, p.edge_market
        FROM picks p
        JOIN games g ON p.game_id = g.id
        WHERE g.game_date >= ? AND p.discord_sent = 1
          AND p.status IN ('won', 'lost')
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

    agents = ["pitching", "offense", "advanced", "bullpen", "weather", "market"]
    result = {}

    for agent in agents:
        col = f"edge_{agent}"
        won_vals, lost_vals = [], []
        for r in rows:
            raw = r[col]
            if raw is None:
                continue
            if isinstance(raw, str):
                m = re.search(r'[+-]?\d+\.\d+', raw)
                if not m:
                    continue
                val = float(m.group())
            else:
                try:
                    val = float(raw)
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
            "live_differential":   live_diff,
            "backtest_lift":       bt_lift,
            "blended_differential": blended,
            "differential":        blended,   # alias used by select_improvement
            "n_won":  len(won_vals),
            "n_lost": len(lost_vals),
            "blend_weight_live":   live_weight,
        }

    return result


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
    Returns dict with success, output, and what changed (git diff summary).
    """
    if not Path(CLAUDE_BIN).exists():
        return {"success": False, "error": "claude CLI not found"}

    try:
        result = subprocess.run(
            [
                CLAUDE_BIN, "-p", task_prompt,
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

TASK_ROLLING_TRENDS = textwrap.dedent("""
    You are implementing one improvement to the MLB picks engine at /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.

    TASK: Add rolling 7-day team performance stats to the momentum agent.

    CURRENT STATE:
    - analysis.py score_momentum() uses season win% and last-10-game record only
    - data_mlb.py collect_game_data() fetches full season team stats
    - Known limitation: season stats mask hot/cold streaks — documented in INSIGHTS.md

    WHAT TO BUILD:
    1. In data_mlb.py: add fetch_rolling_team_stats(team_id, days=7) that:
       - Calls MLB Stats API schedule endpoint with startDate/endDate for last 7 days
       - Returns: wins_last_7, losses_last_7, runs_scored_last_7, runs_allowed_last_7
       - Endpoint: https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId={team_id}&startDate={start}&endDate={today}&hydrate=linescore
       - Falls back gracefully to None if API fails

    2. In data_mlb.py: call fetch_rolling_team_stats() for both home and away team inside collect_game_data(), attach as game_info["away_rolling"] and game_info["home_rolling"]

    3. In analysis.py: update score_momentum() to incorporate rolling stats:
       - If rolling data available: blend 60% season signal + 40% rolling-7 signal
       - Rolling win% = wins_last_7 / max(wins_last_7 + losses_last_7, 1)
       - Rolling run diff = (runs_scored - runs_allowed) / max(games, 1)
       - Score contribution: (away_rolling_winpct - home_rolling_winpct) * 0.4 capped at ±0.25

    4. In tests/: add test_rolling_trends.py with at least 3 tests:
       - fetch_rolling_team_stats returns expected keys
       - score_momentum uses rolling data when present
       - score_momentum falls back gracefully when rolling is None

    CONSTRAINTS:
    - Do not change agent weights in config.py
    - Do not break existing tests — run pytest after changes
    - Keep fallback behavior: if rolling data unavailable, momentum scores exactly as before
    - Commit all changes with message: "feat: add rolling 7-day team trends to momentum agent"

    Run: pytest tests/ -v to verify before committing.
""").strip()


TASK_HOME_AWAY_SPLITS = textwrap.dedent("""
    You are implementing one improvement to the MLB picks engine at /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.

    TASK: Add home/away pitcher split ERA to the pitching agent.

    CURRENT STATE:
    - analysis.py score_pitching() uses season ERA from pitcher_stats dict
    - data_mlb.py fetch_pitcher_stats() fetches season totals only
    - Known limitation documented in INSIGHTS.md: "Home/away pitcher splits not fetched — model uses season ERA only"

    WHAT TO BUILD:
    1. In data_mlb.py: add fetch_pitcher_splits(pitcher_id) that:
       - Calls: https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats?stats=statSplits&group=pitching&sitCodes=h,a&season=2026
       - Returns: {"home_era": float_or_none, "away_era": float_or_none}
       - Falls back to None/None on any error or missing data

    2. In data_mlb.py: call fetch_pitcher_splits() for home and away starter inside collect_game_data().
       Attach as: game_info["home_pitcher_splits"] and game_info["away_pitcher_splits"]

    3. In analysis.py: update score_pitching() to use split ERA when available:
       - If pitcher is home: prefer home_era over season_era (home ERA more relevant)
       - If pitcher is away: prefer away_era over season_era
       - Blend: 70% split ERA + 30% season ERA when split available
       - Fall back to season ERA if split is None

    4. In tests/: add test_pitcher_splits.py with at least 3 tests:
       - fetch_pitcher_splits returns correct keys
       - score_pitching blends split ERA correctly when available
       - score_pitching uses season ERA when splits are None

    CONSTRAINTS:
    - Do not change weights or thresholds
    - Do not break existing tests — run pytest after changes
    - Commit: "feat: add home/away pitcher ERA splits to pitching agent"

    Run: pytest tests/ -v to verify before committing.
""").strip()


TASK_LINE_MOVEMENT = textwrap.dedent("""
    You are implementing one improvement to the MLB picks engine at /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.

    TASK: Track opening line vs current line as a market signal.

    CURRENT STATE:
    - data_odds.py fetches current consensus ML/RL/total lines
    - database.py picks table has no opening line storage
    - Known limitation: "Line movement tracking (opening vs current) not stored"
    - Market agent uses model prob vs implied prob but ignores line movement direction

    WHAT TO BUILD:
    1. In database.py: add columns to picks table (ALTER TABLE if not exists):
       - opening_ml_away INTEGER, opening_ml_home INTEGER, opening_total REAL

    2. In data_odds.py: add fetch_opening_lines(home_team, away_team) that:
       - Fetches from The Odds API with the same key as current odds
       - Looks for the earliest (lowest last_update timestamp) bookmaker entry as proxy for opening
       - Returns {"opening_ml_away": int_or_none, "opening_ml_home": int_or_none, "opening_total": float_or_none}
       - Falls back to None values on any error

    3. In engine.py run_analysis(): after match_odds_to_game(), call fetch_opening_lines() and store in pick_record before save_pick()

    4. In analysis.py market agent (score_market()):
       - If opening_total and current_total differ by ≥0.5: treat as sharp money signal
       - Total moved up → more runs expected → slight over lean (+0.05)
       - Total moved down → fewer runs expected → slight under lean (+0.05 for under)
       - If opening_ml and current_ml differ by ≥10 points: sharp line movement signal (+0.03)

    5. Add opening line to Discord alerts in discord_bot.py — one extra line under Current Odds:
       "- Line move: Total {opening} → {current}" (only when movement ≥ 0.5)

    CONSTRAINTS:
    - Do not break existing tests
    - Run pytest tests/ -v after changes
    - Commit: "feat: track opening line movement as market signal"
""").strip()


TASK_UMPIRE_EXPANSION = textwrap.dedent("""
    You are implementing one improvement to the MLB picks engine at /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.

    TASK: Expand the umpire tendencies table in config.py from 14 to ~40 MLB umpires.

    CURRENT STATE:
    - config.py UMPIRE_TENDENCIES has 14 extreme umps
    - Most games use unknown umps → default to neutral (0.0) even when data exists
    - Source note: "UmpScorecards multi-year averages"

    WHAT TO BUILD:
    Add the following umpires to UMPIRE_TENDENCIES in config.py based on well-documented
    UmpScorecards data (use your training knowledge of ump tendencies through 2024 season).
    Format: {"run_factor": float, "k_factor": float}
    - run_factor range: -0.10 to +0.10 (neutral = 0.0)
    - k_factor range: -0.08 to +0.08 (neutral = 0.0)

    Add at minimum these known umps (use reasonable estimates from UmpScorecards patterns):
    Gabe Morales, Phil Cuzzi, Marvin Hudson, Greg Gibson, Vic Carapazza,
    Bill Miller, Jim Reynolds, Alfonso Marquez, Sean Barber, Brian O'Nora,
    Doug Eddings, Tripp Gibson, Jeremie Rehak, Ben May, Stu Scheurwater,
    Ryan Additon, Nick Mahrley, Junior Valentine, Nestor Ceja, Brennan Miller,
    Chris Segal, Brian Knight, Hunter Wendelstedt, Joe West (retired 2022 but may appear),
    Tom Hallion, Larry Vanover, Sam Holbrook, Ted Barrett, Mark Carlson, Mark Wegner

    For each: neutral (0.0) is acceptable for umps with no strong documented tendency.
    Only add non-zero values for umps with clear documented bias.

    CONSTRAINTS:
    - Only modify config.py UMPIRE_TENDENCIES dict
    - Do not change any weights, thresholds, or other config values
    - Commit: "feat: expand umpire tendencies table to ~40 MLB umps"
""").strip()


TASK_DATA_QUALITY_FIX = textwrap.dedent("""
    You are fixing a data quality issue in the MLB picks engine at /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.

    TASK: Improve API error handling and reduce fallback rate.

    Review engine.log and the following files for recurring errors:
    - data_mlb.py (MLB Stats API + Statcast fetches)
    - data_odds.py (The Odds API)

    For each function that makes an external API call:
    1. Ensure it has a try/except with specific error logging (not silent failures)
    2. Add retry logic (max 2 retries, 2s delay) for timeout errors only
    3. Log a clear WARNING when falling back to defaults so we can track it

    Do not change business logic — only improve error handling robustness.
    Run: pytest tests/ -v after changes.
    Commit: "fix: improve API error handling and retry logic"
""").strip()


TASK_EV_GATE = textwrap.dedent("""
    You are implementing one improvement to the MLB picks engine at /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.

    TASK: Add Expected Value (EV) gate to the pick approval filter.

    CURRENT STATE:
    - analysis.py risk_filter() approves picks at confidence >= MIN_CONFIDENCE (7) and edge_score >= MIN_EDGE_SCORE (0.12)
    - A pick at conf 7 with -200 ML odds requires 67% win rate just to break even — but still gets approved
    - Picks already have ml_win_probability (model's estimated win%) and odds data from the market agent

    WHAT TO BUILD:
    1. In analysis.py risk_filter(): after existing confidence/edge checks, add EV check for moneyline picks:
       - Extract implied win probability from the pick's ML odds (already available via market agent detail)
       - Calculate EV = (win_prob * payout_on_win) - (loss_prob * stake)
         where payout_on_win = 100/abs(odds) if odds < 0, else odds/100
       - Require EV >= -0.02 (allow slightly negative EV for high-confidence plays, reject clearly -EV)
       - For O/U picks: use over_price/under_price from market agent detail

    2. When a pick is rejected by EV gate (passes conf/edge but fails EV):
       - Log it: print(f"[EV GATE] Rejected {pick_team} — conf {conf} but EV {ev:.3f} at {odds}")
       - Include it in the watchlist with reason "High confidence but poor odds value (EV: {ev:.3f})"
       - Do NOT send to Discord

    3. Add "EV" line to Discord pick alerts in discord_bot.py:
       - Under confidence line: "**Expected Value:** {ev:+.3f} ({win_prob}% model vs {implied_prob}% market)"

    4. Store ev_score in pick_record in engine.py and add ev_score column to picks table in database.py

    CONSTRAINTS:
    - MIN_EV = -0.02 (loose gate — don't over-restrict)
    - Falls back gracefully if odds not available (skip EV check, approve on conf/edge alone)
    - Run: pytest tests/ -v after changes
    - Commit: "feat: add expected value gate to pick approval filter"
""").strip()


TASK_PITCHER_SCRATCH_MONITOR = textwrap.dedent("""
    You are implementing one improvement to the MLB picks engine at /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.

    TASK: Add a lightweight pitcher scratch / lineup change monitor.

    CURRENT STATE:
    - engine.py --refresh runs at 11am/1pm/3pm/5pm — 2-4 hour gaps where a pitcher scratch goes undetected
    - If a SP is scratched after the 8am pick was sent, we have an active pick on a game with a different pitcher
    - database.py picks table has mlb_game_id (via games table), away_pitcher, home_pitcher fields

    WHAT TO BUILD: New file `monitor.py`

    1. `run_monitor()`:
       - Load today's sent picks from DB (discord_sent=1, status='pending')
       - For each pick, fetch the current probable pitcher for that game from MLB Stats API
         Endpoint: https://statsapi.mlb.com/api/v1/schedule?gamePks={mlb_game_id}&hydrate=probablePitcher
       - Compare to the pitcher name stored when the pick was sent (from analysis_log or picks table)
       - If pitcher changed: send Discord alert immediately
         Format: "⚠️ **PITCHER SCRATCH ALERT** — {game}\nOriginal pick based on {old_pitcher}.\nNew probable: {new_pitcher}. Consider revisiting this pick."
       - Do not send duplicate alerts for the same game (track in a daily scratch_alerts table)

    2. `main()`: call run_monitor(), log output

    3. Add scratch_alerts table to database.py:
       CREATE TABLE IF NOT EXISTS scratch_alerts (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           game_date TEXT,
           mlb_game_id INTEGER,
           old_pitcher TEXT,
           new_pitcher TEXT,
           alerted_at TEXT,
           UNIQUE(game_date, mlb_game_id)
       )

    4. Add `monitor.sh` wrapper (same pattern as run.sh):
       #!/bin/bash
       cd "$(dirname "$0")"
       /usr/bin/python3 monitor.py >> engine.log 2>&1

    DO NOT create a launchd plist — that will be done separately.
    Run: pytest tests/ -v after changes (add at least 2 tests for monitor.py).
    Commit: "feat: add pitcher scratch monitor with Discord alerts"
""").strip()


TASK_WEATHER_TIMING = textwrap.dedent("""
    You are implementing one improvement to the MLB picks engine at /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.

    TASK: Fetch weather at the actual game start time instead of at analysis time.

    CURRENT STATE:
    - data_mlb.py fetch_weather() fetches current weather conditions at analysis time (8am)
    - Games start anywhere from 12pm to 10pm ET — weather can shift significantly
    - game_time_utc is already stored in game_info dict (from g["gameDate"])
    - Open-Meteo API supports hourly forecasts: add `hourly=temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability` to the request

    WHAT TO BUILD:
    1. In data_mlb.py fetch_weather(): change from current conditions to hourly forecast:
       - Current URL: https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true
       - New URL: https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability&timezone=auto&forecast_days=1
       - Parse game_time_utc to get the target hour (convert UTC → local via timezone parameter)
       - Extract the forecast values for the matching hour index
       - Fall back to current conditions if game_time_utc is empty or parsing fails

    2. Update the weather dict keys to clarify it's a forecast:
       - Add "forecast_for": game_time_str (human-readable) to the returned dict

    3. No changes needed to analysis.py or discord_bot.py — same weather dict keys

    CONSTRAINTS:
    - Fallback to current conditions if game_time_utc missing or API response malformed
    - Run: pytest tests/ -v after changes
    - Commit: "feat: fetch weather at game-specific start time using hourly forecast"
""").strip()


TASK_ROI_TRACKING = textwrap.dedent("""
    You are implementing one improvement to the MLB picks engine at /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.

    TASK: Store actual odds at send time and calculate true ROI in --status output.

    CURRENT STATE:
    - Picks are sent to Discord with ML odds in the message but odds are NOT stored in the picks table
    - database.py picks table has no odds columns
    - --status shows W/L/P win rate but not actual ROI% (win rate at bad odds loses money)
    - discord_bot.py _format_pick_message() already formats odds — the raw values are in the pick dict

    WHAT TO BUILD:
    1. In database.py: add columns to picks table (ALTER TABLE IF NOT EXISTS pattern):
       - ml_odds INTEGER  (American odds, e.g. -150 or +130)
       - ou_odds INTEGER  (over or under price, whichever applies)

    2. In engine.py run_analysis(): extract odds from pick dict and include in pick_record:
       - For ML picks: ml_odds = market agent away_ml or home_ml depending on pick_team
       - For O/U picks: ou_odds = over_price or under_price depending on pick_type
       - These values are already in the pick dict via market agent detail

    3. In database.py get_roi_summary(): calculate true ROI:
       - For each graded pick with ml_odds stored:
         * Won: profit = 100/abs(odds) if odds < 0, else odds/100 (per 1 unit)
         * Lost: profit = -1.0
       - Sum profit / total picks = ROI per unit
       - Return roi_per_unit and total_units_profit alongside existing fields

    4. In engine.py _print_snapshot(): update PICKS SENT line to show:
       "PICKS SENT:  5W - 2L - 0P  (71.4% win rate)  +1.23 units  [7 graded]"

    CONSTRAINTS:
    - Backward compatible: picks without odds stored show N/A for ROI, don't crash
    - Run: pytest tests/ -v after changes
    - Commit: "feat: store send-time odds and calculate true unit ROI in --status"
""").strip()


TASK_TRAVEL_FATIGUE = textwrap.dedent("""
    You are implementing one improvement to the MLB picks engine at /Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/.

    TASK: Add travel fatigue signal to the momentum agent.

    CURRENT STATE:
    - analysis.py score_momentum() uses win streaks and last-10 record only
    - No signal for road trip length, timezone changes, or back-to-back games
    - MLB teams play 162 games — fatigue from long road trips is a measurable signal

    WHAT TO BUILD:
    1. In data_mlb.py: add fetch_travel_context(team_id, game_date) that:
       - Fetches last 7 days of schedule for the team
         Endpoint: https://statsapi.mlb.com/api/v1/schedule?teamId={team_id}&startDate={7d_ago}&endDate={today}&sportId=1
       - Returns:
         * consecutive_road_games: int (how many consecutive away games including today)
         * timezone_changes_last_5d: int (count of games in different timezone from today's game)
         * days_since_off_day: int
       - Falls back to None on error

    2. In analysis.py score_momentum(): apply travel penalty when context available:
       - consecutive_road_games >= 5: -0.04 penalty
       - timezone_changes_last_5d >= 2 (e.g. east coast team playing west coast): -0.05 penalty
       - Stack: max combined penalty -0.08
       - Applied to away team only (home team has no travel penalty)
       - Include in edge summary if penalty > 0: "Away team on extended road trip ({n} games)"

    CONSTRAINTS:
    - Graceful fallback: if fetch_travel_context fails, momentum scores exactly as before
    - Run: pytest tests/ -v after changes
    - Commit: "feat: add travel fatigue signal to momentum agent"
""").strip()


# ─────────────────────────────────────────────────────────────────────────────
#  PRIORITIZER — picks the best improvement this week
# ─────────────────────────────────────────────────────────────────────────────

def select_improvement(perf, model, log_issues, signal):
    """Return the highest-priority actionable improvement."""
    completed = get_completed_ids()
    graded = perf["total"] if perf else 0

    # 1. Data quality — always highest priority
    if log_issues["issues"] and "data_quality" not in completed:
        return {
            "id": "data_quality",
            "name": "API Error Handling & Retry Logic",
            "description": "Reduce fallback rate by adding retries and structured error logging",
            "type": "claude",
            "task": TASK_DATA_QUALITY_FIX,
            "priority_reason": f"Log issues: {'; '.join(log_issues['issues'][:2])}",
        }

    # 2. Weight rebalance — need 20+ graded picks with meaningful signal
    if graded >= 20 and signal and "weight_rebalance" not in completed:
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

    # 3. Threshold tuning — need 30+ graded picks
    if graded >= 30 and perf:
        if perf["win_rate"] < 50.0 and "threshold_up" not in completed:
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
            if c7_rate >= 0.65 and "threshold_down" not in completed:
                return {
                    "id": "threshold_down",
                    "name": "Lower Confidence Threshold",
                    "description": f"Conf 7 picks hitting {c7_rate*100:.0f}% ({c7_total} picks). Lowering MIN_CONFIDENCE.",
                    "type": "config_threshold",
                    "direction": "down",
                    "win_rate": c7_rate * 100,
                    "priority_reason": f"Conf 7 hitting {c7_rate*100:.0f}% over {c7_total} picks — well above 52% target",
                }

    # 4. Ordered code improvements — one per week, never repeat
    # Ordered by estimated impact on pick quality
    code_queue = [
        # Tier 0: Quick wins (config, data coverage)
        ("umpire_expansion",        "Expand Umpire Tendencies Table",          TASK_UMPIRE_EXPANSION),
        # Tier 1: High impact — pick quality / error prevention
        ("ev_gate",                 "Add Expected Value Gate",                 TASK_EV_GATE),
        ("pitcher_scratch_monitor", "Add Pitcher Scratch Monitor",             TASK_PITCHER_SCRATCH_MONITOR),
        ("roi_tracking",            "Store Odds & Track True ROI",             TASK_ROI_TRACKING),
        # Tier 2: Signal improvements
        ("weather_timing",          "Weather at Game-Specific Start Time",     TASK_WEATHER_TIMING),
        ("rolling_trends",          "Add Rolling 7-Day Team Trends",           TASK_ROLLING_TRENDS),
        ("home_away_splits",        "Add Home/Away Pitcher Splits",            TASK_HOME_AWAY_SPLITS),
        ("travel_fatigue",          "Add Travel Fatigue Signal",               TASK_TRAVEL_FATIGUE),
        # Tier 3: Market / structural
        ("line_movement",           "Track Opening Line Movement",             TASK_LINE_MOVEMENT),
    ]

    for imp_id, imp_name, task in code_queue:
        if imp_id not in completed:
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
        "description": "All planned improvements complete. No new code changes this week.",
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

    # Improvement result block
    imp_name   = imp.get("name", "Unknown") if imp else "None"
    imp_reason = imp.get("priority_reason", "") if imp else ""

    if result.get("skipped"):
        action_block = f"**This week:** No action — {result.get('reason', 'unknown')}"
    elif result.get("success"):
        diff = result.get("diff_stat", "").strip()
        action_block = (
            f"**This week:** ✅ {imp_name}\n"
            f"**Why:** {imp_reason}\n"
        )
        if diff:
            action_block += f"```\n{diff[:400]}\n```"
        action_block += "\n✅ All tests passing" if result.get("tests_passed") else \
                        f"\n⚠️ Tests:\n```{result.get('test_output','')[:200]}```"
    else:
        action_block = (
            f"**This week:** ❌ {imp_name} — FAILED\n"
            f"**Error:** {result.get('error', 'unknown')[:200]}"
        )

    msg = (
        f"⚙️ **MLB ENGINE — WEEKLY OPTIMIZER REPORT**\n"
        f"**Week of {week}** | Backtest baseline: {BACKTEST_REFERENCE['total_games']:,} games "
        f"({', '.join(str(s) for s in BACKTEST_REFERENCE['seasons'])})\n\n"
        f"**Pipeline Performance (Last 30 Days):**\n"
        f"{perf_block}\n"
        f"{model_block}\n\n"
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
            print("[DISCORD] Weekly optimizer report sent.")
        else:
            print(f"[DISCORD] Failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"[DISCORD] Error sending report: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"  MLB WEEKLY OPTIMIZER — {date.today().strftime('%B %d, %Y')}")
    print("=" * 60)

    # ── 1. Analysis ──
    print("\n[1/4] Analyzing pipeline performance...")
    perf   = analyze_pick_performance(days=30)
    model  = analyze_model_accuracy(days=30)
    signal = analyze_agent_signals(days=30)
    log    = analyze_log_issues()

    graded = perf["total"] if perf else 0
    print(f"  Graded picks (30d): {graded}")
    if model:
        print(f"  Model ML accuracy: {model['ml_accuracy']}% ({model['total']} games)")
    if log["issues"]:
        print(f"  Log issues: {log['issues']}")
    else:
        print(f"  Log: clean (errors={log['error_count']}, fallbacks={log['fallback_count']})")

    # ── 2. Select improvement ──
    print("\n[2/4] Selecting improvement...")
    improvement = select_improvement(perf, model, log, signal)
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
                    f"chore: weekly weight rebalance {date.today().isoformat()} "
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
                    f"chore: adjust MIN_CONFIDENCE {impl['old']}→{impl['new']} "
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
                git_commit(f"Weekly optimizer: {improvement['name']}")
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
        "improvement": improvement,
        "result":      result,
    })

    print("\n✅ Weekly optimizer complete.")


if __name__ == "__main__":
    main()
