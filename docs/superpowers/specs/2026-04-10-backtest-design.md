# MLB Picks Engine — Backtester Design
**Date:** 2026-04-10  
**Status:** Approved  
**Goal:** Empirically validate and tune agent weights using 2024 and 2025 MLB season data

---

## Problem

The current model weights (pitching 30%, offense 20%, advanced 15%, etc.) are assumptions with no empirical backing. The engine launched 2026-04-10 with real money on the line and no historical validation. This backtester provides evidence-based weight tuning before more money is at risk.

---

## Architecture

A new `backtest.py` module alongside the existing engine. It reuses `analysis.py` scoring functions unchanged — agents accept historical stat dicts the same way they accept live data. The backtester feeds historical data into those functions, collects per-agent scores, and compares against known outcomes.

**Three phases:**
1. **Data collection** — fetch 2024 + 2025 regular season schedule with final scores, pitcher/batting/bullpen/Statcast stats
2. **Scoring** — run all 7 agents per game, record per-agent scores and composite
3. **Analysis** — compare model picks vs actual outcomes, output calibration reports and suggested weights

---

## Data Collection

### Sources (all free, all already used by the engine)

| Data | Source | Notes |
|------|--------|-------|
| Schedule + final scores | MLB Stats API | `sportId=1&season=YYYY&gameType=R` |
| Probable pitchers | MLB Stats API schedule hydrate | Same as live engine |
| Pitcher season stats | MLB Stats API | ERA, WHIP, K/BB, K/9 — end-of-season |
| Team batting stats | MLB Stats API | OPS, OBP, SLG, runs/game — end-of-season |
| Team records / streaks | MLB Stats API | Win %, last 10, streak |
| Team Statcast (batting + pitching) | Baseball Savant | Same CSV endpoints, pass `season=YYYY` |

### Exclusions

| Data | Reason |
|------|--------|
| Weather | Open-Meteo has no historical game-time forecasts — agent scores neutral (0.0) |
| HP Umpire | Not bulk-fetchable for historical games — scores neutral |
| Odds / market | The Odds API historical data requires paid tier — market agent scores neutral (0.0) |

These three excluded agents account for ~15% of total weight (weather 5%, market 10%). The remaining 85% (pitching, offense, advanced, bullpen, momentum) is fully backtestable.

### Caching

Historical data is cached to a `backtest_cache.db` SQLite file (separate from `mlb_picks.db`) on first fetch. Re-runs use cache — no re-fetching. One fetch per team per season for stats; one schedule fetch per season for games.

---

## Scoring

For each of ~4,860 regular season games per year (~9,720 total across 2024 + 2025):

1. Build `pitcher_stats`, `batting_stats`, `bullpen_stats`, `team_record` dicts in the format `analysis.py` expects
2. Call each agent's scoring function directly (no changes to `analysis.py`)
3. Record: per-agent raw score, weighted composite score, predicted pick + confidence, actual outcome (home win / away win / final score)

**Skipped games:**
- No recorded probable pitcher for either team
- Game not completed (postponed, suspended — no final score)
- Early season games where team has < 20 PA of Statcast data (flagged, optionally excluded)

**Pick simulation:** Apply same live rules — composite score ≥ 0.12, confidence ≥ 7, max 5/day. Record pick vs actual result per game.

**Moneyline only:** O/U picks require a historical total line to determine over/under direction. Without The Odds API historical data, only moneyline picks can be fully simulated. The composite score (positive = home edge, negative = away edge) maps directly to ML picks. O/U backtesting is out of scope unless a free historical totals source is identified.

---

## Analysis & Output

### Report 1: Win Rate by Confidence Level
Does confidence 8 actually win more than confidence 7? Validates the confidence scale is meaningful.

| Confidence | Picks | Wins | Win Rate | Expected |
|------------|-------|------|----------|----------|
| 10 | N | N | X% | >65% |
| 9 | N | N | X% | ~60% |
| 8 | N | N | X% | ~55% |
| 7 | N | N | X% | ~52% |

### Report 2: Per-Agent Signal Correlation
For winning picks: which agent's edge was highest?  
For losing picks: which agent was most misleading?  
Outputs a correlation coefficient per agent — directly tells you which weights to raise or lower.

### Report 3: Calibration Curve
Model-predicted win probability vs actual win rate. If the model says 65% but wins 52%, it's overconfident and thresholds need tightening.

### Report 4: Suggested Weights
Based on per-agent correlation with outcomes, output a recommended `WEIGHTS` dict to compare against current config. User reviews and applies manually.

---

## CLI

```bash
python3 backtest.py                      # backtest both 2024 + 2025 (default)
python3 backtest.py --season 2024        # single season
python3 backtest.py --season 2024,2025   # explicit both seasons
python3 backtest.py --suggest-weights    # output recommended WEIGHTS dict
python3 backtest.py --no-cache           # force re-fetch all data
```

---

## Files Changed / Created

| File | Change |
|------|--------|
| `backtest.py` | New — backtesting module |
| `backtest_cache.db` | New — auto-created SQLite cache |
| `analysis.py` | No changes — reused as-is |
| `data_mlb.py` | Minor additions — historical season fetch variants |
| `config.py` | No changes |

---

## Constraints & Edge Cases

- **MLB Stats API rate limiting:** Batch requests by team, not by game. ~30 teams × 2 seasons = 60 stat fetches, not 9,700.
- **End-of-season stats approximation:** Using final season stats for all games is imperfect (a May game uses stats accrued through October). Acceptable for directional weight calibration; not suitable for exact probability modeling.
- **Season boundaries:** 2024 games use 2024 stats only. No cross-season bleed.
- **Market agent:** Excluded from weight analysis. Its 10% weight is held constant — only the other 6 agents are re-tuned.

---

## Success Criteria

- Win rate by confidence level shows monotonic increase (conf 8 > conf 7 > conf 6)
- At least one agent has meaningfully higher correlation with outcomes than its current weight reflects
- Suggested weights differ from current by at least one agent ≥ ±5 percentage points
- Full 2024 + 2025 backtest completes in under 10 minutes on first run (with caching)
