# MLB Picks Engine — Claude Code Reference

## Project Location
`/Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/`

## What This Is
A fully automated MLB betting picks engine. It collects live game data, runs a 7-agent weighted analysis, filters for high-confidence plays only, and sends formatted alerts to a private Discord channel via webhook. No manual intervention needed day-to-day.

---

## Daily Automated Schedule (launchd)

| Time | Command | What it does |
|------|---------|--------------|
| 8:00 AM | `engine.py` | Full analysis — sends approved picks to Discord (catches 10am games) |
| 11:00 AM | `engine.py --refresh` | Re-validates picks, sends cancel/reduce alerts if edge changed |
| 1:00 PM | `engine.py --refresh` | Same |
| 3:00 PM | `engine.py --refresh` | Same |
| 5:00 PM | `engine.py --refresh` | Same |
| 11:00 PM | `engine.py --results` | Grades picks vs final scores, sends recap to Discord |
| Every 30 min | `monitor.py` | Pitcher scratch monitor — checks active picks for SP changes, sends Discord alert |
| Sunday 9pm | `optimizer.py` | Weekly autonomous improvement — analyzes performance, implements one change, sends Discord report |

Plists at `~/Library/LaunchAgents/com.marc.mlb-picks-engine.*.plist`. Wrappers: `run.sh` (daily), `run_optimizer.sh` (weekly).
All output logged to `engine.log`.

---

## CLI Usage

```bash
python3 engine.py              # Full analysis + send picks to Discord
python3 engine.py --test       # Dry run: analyze only, no Discord
python3 engine.py --refresh    # Re-validate sent picks, send updates if changed
python3 engine.py --results    # Grade today's picks after games finish
                               # Guards: no-ops if no picks sent today, no final games, or nothing newly graded
python3 engine.py --status     # Print 30-day tracking snapshot
python3 engine.py --game X     # Full 7-agent analysis of game(s) matching team name X, sent to Discord
                               # Bypasses confidence threshold — analysis only, no approved picks flow
                               # Examples: --game marlins | --game "marlins tigers" | --game yankees
python3 engine.py --collect DATE  # Collect/store post-game boxscores for DATE (YYYY-MM-DD); auto-runs after --results
```

---

## Analysis System — 7 Agents

Each agent scores from -1.0 (away edge) to +1.0 (home edge).

| Agent | Weight | Data Source |
|-------|--------|-------------|
| Pitching | 25% | MLB Stats API — ERA, WHIP, K/BB, K/9, handedness matchup, pitcher rest days |
| Offense | 20% | MLB Stats API — OPS, OBP, SLG, runs per game |
| Bullpen | 17% | MLB Stats API — team ERA, WHIP, save %, holds + recent usage fatigue signal |
| Advanced Metrics | 13% | Baseball Savant Statcast — xwOBA luck diff, barrel rate, hard-hit rate, pitcher xERA regression |
| Momentum | 10% | MLB Stats API — win streaks, win %, last 10 |
| Market Value | 10% | The Odds API — model prob vs implied prob edge |
| Weather/Environment | 5% | Open-Meteo + park factors + HP umpire tendencies |

### Statcast Logic (Advanced Agent)
- **xwOBA luck diff** (wOBA - xwOBA): positive = team outperforming contact quality (lucky, expect regression); negative = underperforming (unlucky, expect bounce back)
- **Barrel rate differential**: ≥2% gap triggers a score adjustment
- **Hard-hit rate differential**: ≥4% gap triggers a score adjustment
- **Pitcher xERA vs ERA**: ERA - xERA < -0.60 = pitcher getting lucky (ERA likely to rise); > +0.75 = unlucky (ERA likely to fall)
- Falls back to plate discipline + WHIP/ERA proxy if Statcast unavailable
- Data source: Baseball Savant (free, no API key) — 3 endpoints cached daily

### Handedness Logic
- Pitcher L/R hand shown in Discord alerts
- LHP vs high-K-rate lineup gets a ±0.06 score adjustment
- K-rate threshold: league avg (22.5%) + 2%

### Pitcher Rest Logic
- ≤3 days rest (short rest): ±0.12 score penalty for that team's SP
- 4 days rest (normal): neutral
- 5-6 days rest (extra rest): ±0.05 bonus
- ≥8 days rest (extended layoff): ±0.03 rust penalty
- Data: MLB Stats API game log endpoint per pitcher, fetched per game

### Bullpen Fatigue Logic
- `fetch_bullpen_recent_usage(team_id)` — fetches last 5 days of completed boxscores, sums relief IP (all pitchers after starter)
- `_bullpen_fatigue_penalty(usage)` in `analysis.py` — applies penalty based on `ip_last_3`:
  - ≤ 8.0 IP → no penalty
  - 8–12 IP → −0.08 (moderate fatigue)
  - > 12 IP → −0.15 (heavy fatigue)
- Score adjustment: `score += away_penalty - home_penalty` (fatigued team loses edge)
- Fatigue note included in Discord edge summary when triggered

### Weather/Environment Logic (5% weight agent covers all three)
**Weather:**
- Cold (<45°F) → -0.15 (suppresses offense)
- Wind out (E/SE/NE, ≥10mph) → up to +0.20 (hitter-friendly)
- Wind in (W/SW/NW, ≥10mph) → up to -0.20 (pitcher-friendly)
- Rain ≥70% → -0.10
- Coordinates pulled from MLB venue API, forecast from Open-Meteo

**Park Factors** (`config.py PARK_FACTORS`, keyed by home team abbreviation):
- Factor applied as `(park_factor - 1.0) * 0.5` score adjustment
- Also applied directly to projected run totals in score projection
- Range: COL 1.28 (extreme hitter) → SF 0.89 (extreme pitcher)
- 30-park static table (multi-year FanGraphs averages)

**HP Umpire Tendencies** (`config.py UMPIRE_TENDENCIES`):
- HP umpire fetched from MLB API `officials` hydrate at game fetch time
- `run_factor`: +/- effect on scoring (positive = more runs)
- `k_factor`: +/- effect on strikeouts
- 14 known extreme umps in table; unknown umps default to neutral (0.0)

### Lineup Cards
- Fetched via MLB API `lineups` hydrate in schedule call (~3.5 hrs before game)
- If both lineups confirmed: pick notes say "Lineups confirmed"
- If not confirmed: pick notes say "Lineup TBD — monitor before first pitch"
- Watchlist entries reflect unconfirmed lineup risk

---

## Pick Rules

- Allowed types: **moneyline**, **over**, **under**
- Minimum confidence: **7/10**
- Minimum edge score: **0.12**
- Maximum picks per day: **5**
- If nothing qualifies → outputs **PASS**, nothing sent to Discord

---

## Operator Output (each run)

1. **Game Analysis Board** — all 15 games with composite scores
2. **Approved Picks Board** — ranked by confidence
3. **Watchlist** — up to 5 games near threshold (conf 5-6) to monitor
4. **Discord Payload** — exact JSON sent to webhook
5. **Tracking Snapshot** — 30-day record, win rate, ROI

---

## Discord Message Types

| Trigger | Format |
|---------|--------|
| Approved pick | `🚨 MLB HIGH-CONFIDENCE PICK` |
| Pick update (cancel/reduce) | `⚠️ MLB PICK UPDATE` |
| Evening results recap | `✅ MLB DAILY RESULTS` |

Each pick alert includes a **Date/Time** line (game start in PT, `America/Los_Angeles`).
Source: `g["gameDate"]` (UTC ISO) from MLB API → `game_time_utc` field → `discord_bot._format_game_time()`.

Update alerts fire automatically during `--refresh` runs when:
- A pick drops below threshold → **Cancel** alert
- Confidence drops 2+ points → **Reduce Confidence** alert

Each pick alert includes:
- Game, pick, confidence, win probability, projected score
- **Current Odds**: ML, run line (±1.5), total with over/under prices
- **Edge Summary**: Pitching, Offense, Advanced (Statcast), Bullpen, Weather, Market
- **Notes**: lineup status (confirmed or TBD), total line for O/U picks

---

## File Structure

```
mlb-picks-engine/
├── engine.py               — Main orchestrator (run this)
├── optimizer.py            — Weekly autonomous improvement engine (Sunday 9pm)
├── analysis.py             — 7-agent weighted scoring + _bullpen_fatigue_penalty() + risk filter + watchlist
├── data_mlb.py             — MLB Stats API (schedule, pitchers, batting, records, officials,
│                             lineups, pitcher rest, bullpen recent usage); Statcast; Open-Meteo weather
├── data_odds.py            — The Odds API (moneyline, run line, totals; pre-game filter; consensus)
├── discord_bot.py          — Webhook sender (pick, update, results); formats edge summary + odds block
├── database.py             — SQLite tracking (picks, games, results, ROI)
├── config.py               — API keys, weights, thresholds, PARK_FACTORS, UMPIRE_TENDENCIES
├── backtest.py             — Historical backtester (python3 backtest.py to run 2024+2025 seasons)
├── backtest_cache.py       — SQLite cache for backtest historical data (backtest_cache.db)
├── monitor.py              — Pitcher scratch monitor; runs every 30 min via launchd
├── monitor.sh              — Wrapper for monitor.py
├── run.sh                  — Wrapper for daily engine runs (launchd)
├── run_optimizer.sh        — Wrapper for weekly optimizer (launchd, full PATH for claude CLI)
├── mlb_picks.db            — Auto-created SQLite database
├── backtest_cache.db       — Auto-created backtest cache
├── engine.log              — All run output
├── COMPLETED_IMPROVEMENTS.md — Tracks every optimizer-implemented improvement
├── PIPELINE.md             — Full pipeline framework overview (architecture, agents, flow, weights)
├── SETUP.md                — First-time setup instructions
├── INSIGHTS.md             — Daily outcomes, calibration tracker, bias log, weight tuning history
└── CLAUDE.md               — This file
```

---

## Config (config.py)

```python
DISCORD_WEBHOOK_URL = "..."   # Private Discord channel webhook
ODDS_API_KEY = "..."          # The Odds API — 500 free req/month
MAX_PICKS_PER_DAY = 5
MIN_CONFIDENCE = 7
MIN_EDGE_SCORE = 0.12
SEASON_YEAR = 2026
WEIGHTS = {
    "pitching": 0.25, "offense": 0.20, "bullpen": 0.17,
    "advanced": 0.13, "momentum": 0.10, "weather": 0.05, "market": 0.10
}
# Weights updated 2026-04-10 based on 2024+2025 backtest (4,855 games)
# Bullpen raised 10→17% (strongest underweighted signal), pitching lowered 30→25%
PARK_FACTORS = { "COL": 1.28, "CIN": 1.13, ..., "SF": 0.89 }  # 30 parks, keyed by team abbr
UMPIRE_TENDENCIES = { "Laz Diaz": {"run_factor": -0.08, ...}, ... }  # 14 extreme umps
```

---

## Database

SQLite at `mlb_picks.db`. Tables:
- `picks` — approved picks sent to Discord; `discord_sent` flag; outcome status; deduped by `pick_already_sent_today(game_id, pick_type)` — one record per game+type per day
- `analysis_log` — all 15 games logged every run; `UNIQUE(game_date, mlb_game_id)` + `INSERT OR REPLACE` so refreshes update with confirmed lineups; graded by `--results` at 11pm; powers model accuracy stats in `--status`
- `games` — game records with final scores
- `teams` — all 30 MLB teams with mlb_id, abbreviation, division, league
- `daily_results` — wins/losses/pushes/ROI per day

Key DB functions:
- `pick_already_sent_today(game_id, pick_type)` — dedup guard before insert
- `save_analysis_log(entry)` — upsert via INSERT OR REPLACE
- `get_today_analysis_log()` — all 15 today's entries
- `update_analysis_log_result(log_id, ml_status, ou_status, ...)` — grade after results
- `get_model_accuracy_summary(days)` — ML + O/U accuracy across all logged games

## --status Output Format

```
PICKS SENT:  5W - 2L - 0P  (71.4% win rate)  [7 graded]
MODEL ML:    9W - 6L  (60.0% accuracy)  [15 games]
MODEL O/U:   7W - 4L  (63.6% accuracy)  [11 games]
```

PICKS SENT = only approved sent picks. MODEL = all 15 games (raw pipeline signal).

---

## Data Sources Summary

| Source | What it provides | Cost |
|--------|-----------------|------|
| MLB Stats API | Schedule, probable pitchers, pitcher game logs (rest), team batting/pitching/records, venue coords, HP officials, confirmed lineups | Free |
| Baseball Savant | Team batting Statcast, team pitching Statcast, pitcher xERA (437+ pitchers) — 3 CSV endpoints cached daily | Free |
| Open-Meteo | Game-time weather forecast via venue lat/lon | Free |
| The Odds API | Moneyline, run line, totals from multiple bookmakers | Free (500 req/month) |
| Discord Webhook | Pick/update/results delivery | Free |

## Weekly Optimizer (`optimizer.py`)

Runs every Sunday at 9pm. Fully autonomous improvement cycle:
1. Analyzes pick win rates, model calibration, agent signal differentials, log errors
2. Blends live agent signals against 4,855-game backtest baseline (2024+2025)
   - `BACKTEST_REFERENCE` dict stores known lift scores per agent (pitching +0.080, bullpen +0.050, etc.)
   - Blend weight shifts from 100% backtest → 80% live as live picks accumulate (20→200 picks)
   - `load_backtest_lift()` queries `backtest_cache.db.optimizer_lift_cache` if available, else uses constants
3. Selects highest-priority improvement (data quality → weight rebalance → threshold tune → code gaps)
4. Implements via direct Python edit (config changes) or `claude -p --dangerously-skip-permissions` (code changes)
5. Runs `pytest` — reverts on failure
6. Commits + sends Discord report: live vs backtest comparison per agent, what changed, test status
7. `COMPLETED_IMPROVEMENTS.md` tracks all implemented items to prevent repeats

## Python Version & Environment

- Python 3.9 on this machine. No `float | None` union syntax in signatures — use `"float | None"` string annotation or `Optional[float]`.
- `zoneinfo` is stdlib (3.9+). No pip install needed for timezone work.
- `%-I` strftime format works on macOS despite being glibc-documented.

## Config Pattern

All tunable thresholds must live in `config.py` — the optimizer reads config.py to tune values. Never hardcode thresholds inline in analysis.py.
- Current thresholds: `MIN_CONFIDENCE`, `MIN_EDGE_SCORE`, `MIN_EV`, `MAX_PICKS_PER_DAY`

## Testing Notes

- **Pre-existing failing test**: `tests/test_analysis_log.py::test_run_results_grades_analysis_log` — fails as of 2026-04-11, unrelated to new work. Do not attempt to fix unless explicitly tasked.
- **Mock patch target**: When `fetch_lineup_batting` (or any function) is imported at module level in `analysis.py`, mock it as `analysis.fetch_lineup_batting`, not `data_mlb.fetch_lineup_batting`.
- **DB migration pattern**: Use `except sqlite3.OperationalError: pass` (not bare `except Exception`) when adding columns.
- Run tests: `python3 -m pytest tests/ -v --tb=short`

## COMPLETED_IMPROVEMENTS.md

Uses `<!-- id: improvement_id -->` HTML comment markers for optimizer dedup. Each entry must have this marker or the optimizer will re-implement it. File is at project root.

## Known Limitations / Future Improvements

- Rolling stats active: 21-day SP ERA/WHIP/K9/BB9, 14-day team R/G+OBP, 14-day bullpen ERA/WHIP; blend threshold <5 games → season only
- MLB API: `schedule?hydrate=boxscore` does NOT return player-level pitcher stats for historical dates — `collect_boxscores()` uses `/game/{gamePk}/boxscore` per game instead (verified fix 2026-04-12)
- Home/away pitcher splits not yet fetched — uses season ERA only
- Line movement tracking (opening vs current) not stored
- FanGraphs scraping non-functional (requires JS) — wRC+ not available

## Consensus Odds Logic (`data_odds.py`)

| Market | Line | Price |
|--------|------|-------|
| Moneyline | implied probability average → converted back to American | N/A |
| Run line | mode (most common ±1.5 line across books) | average across books |
| Totals | mode (most common line across books) | average across books |

Notes:
- ML values > ±500 are excluded (outlier/prop markets)
- Only standard ±1.5 run lines are used (not alternate spreads)
- Pre-game filter: games with `commence_time ≤ now_utc` are skipped — no live in-game odds
- F5 vs full-game disambiguation: when multiple entries match a game, the one with the highest total line (full game) is used
