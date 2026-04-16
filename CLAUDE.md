# MLB Picks Engine — Claude Reference

**Project:** `/Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/`
**What:** Automated MLB betting picks engine. Collects live data, runs 7-agent weighted analysis, sends high-confidence plays to Discord via webhook. No manual intervention day-to-day.

---

## Daily Schedule (launchd)

| Time | Command | What it does |
|------|---------|--------------|
| 8:00 AM–5:00 PM (hourly) | `engine.py` | Full re-analysis every hour — dedup blocks resends, lineup penalty (-1 conf) holds borderline picks until lineups confirm |
| 11:00 PM | `engine.py --results` | Grades picks vs final scores; auto-runs `collect_boxscores()` |
| Every 30 min | `monitor.py` | Pitcher scratch monitor — Discord alert on SP change |
| 11:30 PM | `optimizer.py` | Nightly optimizer — analyzes data, implements one improvement (7-day code cooldown) |
| Monday 9:00 AM | `calibrate.py` | Weekly signal calibration — posts Discord report; run with `--apply` to update weights |

Plists at `~/Library/LaunchAgents/com.marc.mlb-picks-engine.*.plist`. Output logged to `engine.log`.

---

## CLI Usage

```bash
python3 engine.py              # Full analysis + send picks
python3 engine.py --test       # Dry run — no Discord
python3 engine.py --refresh    # Re-validate sent picks, send updates
python3 engine.py --results    # Grade today's picks after games finish
python3 engine.py --status     # Print 30-day tracking snapshot
python3 engine.py --game X     # 7-agent analysis of game(s) matching team X (bypasses threshold)
python3 engine.py --collect DATE  # Collect post-game boxscores for DATE (YYYY-MM-DD)
python3 engine.py --report          # Re-send nightly report for today (already graded)
python3 engine.py --report DATE     # Re-send nightly report for DATE (YYYY-MM-DD)
```

---

## Pick Filter

- Min confidence ML: **7/10** (`MIN_CONFIDENCE` in config.py)
- Min confidence O/U: **9/10** (`MIN_CONFIDENCE_OU`) — gap formula only, needs ≥1.5 run gap
- Min edge score: **0.12** (`MIN_EDGE_SCORE`)
- Min EV: **−0.02** (`MIN_EV`)
- Max picks/day: **20** (`MAX_PICKS_PER_DAY`) — ML and O/U tracked independently
- Pick types: `moneyline` | `over` | `under` | `f5_ml`
- SP TBD cap: one SP unknown → max 3/10 (ML + O/U); both unknown → max 1/10
- If nothing qualifies → PASS, nothing sent

---

## File Map

```
engine.py          — orchestrator + all CLI flags
analysis.py        — 7 agents, kelly_stake(), _calculate_ev(), risk_filter(), _analyze_f5_pick()
                     _analyze_over_under() — O/U with weather/bullpen/SP/park signals
data_mlb.py        — MLB Stats API, Statcast, Open-Meteo, collect_boxscores()
data_odds.py       — The Odds API (full-game + F5); consensus ML/RL/total logic
discord_bot.py     — all webhook formatting and sends
                     send_daily_board() — ML picks board (all games, refreshes every 3h)
                     send_ou_board()    — O/U picks board (all games, refreshes every 3h)
database.py        — SQLite — all tables, queries, rolling stats functions
                     daily_board / daily_ou_board — board message ID tracking
config.py          — WEIGHTS, PARK_FACTORS (30), UMPIRE_TENDENCIES (43), all thresholds
monitor.py         — pitcher scratch monitor
optimizer.py       — nightly improvement engine
backtest.py        — 2024+2025 historical validator (4,855 games)
calibrate.py       — weekly signal calibration; reads picks DB, posts Discord report, optionally applies weights
                     entry points: --test (stdout only), --apply (write config.py + commit), --days N
COMPLETED_IMPROVEMENTS.md  — optimizer dedup (<!-- id: xxx --> markers)
PIPELINE.md        — full architecture + flow diagram
INSIGHTS.md        — calibration log, bias tracker, weight tuning history
```

---

## Critical Rules

**All thresholds in config.py** — never hardcode in analysis.py (optimizer reads config.py to tune values).

**Python 3.9** — no `float | None` union syntax. Use `Optional[float]` or `"float | None"` string annotation. `zoneinfo` is stdlib (no pip install).

**MLB API gotcha** — `schedule?hydrate=boxscore` omits player-level pitcher stats for historical dates. Use `/game/{gamePk}/boxscore` per game instead (verified fix 2026-04-12).

**Mock patch target** — if `fetch_lineup_batting` (or any function) is imported at module level in analysis.py, mock as `analysis.fetch_lineup_batting` not `data_mlb.fetch_lineup_batting`.

**RemoteTrigger is cloud-side** — the 2 AM CEO nightly trigger (`trig_01CAuaYtQCBpWSHfNFnS1gJw`) runs against a fresh GitHub clone, not locally. It survives session restarts. To check if it ran: `git fetch origin && git log origin/main --since="YYYY-MM-DD 02:00"`.

**launchd jobs don't log by default** — none of the plists have `StandardOutPath`. All output goes to `engine.log` via the run_*.sh wrappers. `LastExitStatus = 0` with no `LastRunTime` means the job has never fired since being loaded (likely Mac was asleep at scheduled time).

**pitcher_game_logs / team_game_logs are forward-only** — `collect_boxscores()` is called nightly for yesterday only. If the DB is empty, run `backfill_boxscores.py` (project root) to populate from Apr 1 through yesterday. Safe to re-run (INSERT OR IGNORE).

**DB migration** — use `except sqlite3.OperationalError: pass` (not bare `except Exception`).

**analysis_log re-runs use INSERT + UPDATE** — `save_analysis_log()` inserts on first run, then UPDATE on re-runs to refresh analysis fields while preserving `ml_status`/`ou_status` grading columns. Do not revert to INSERT OR IGNORE.

**O/U confidence is a float before return** — K-rate nudge adds 0.5, bullpen nudge adds 1. Always cast with `int(round(conf))` not `int(conf)` to avoid truncation.

**Wind direction from MLB API is compass** — `_wind_direction_label()` returns `N/NE/E/SE/S/SW/W/NW`. Use these in both `score_weather()` and `_project_score()`. Never use `"out to CF"` style strings.

---

## Reference Docs

For deeper detail, read these only when working in that area:

- **`docs/agents.md`** — 7-agent scoring logic, weights, rest/fatigue/weather/park/umpire rules, rolling blend thresholds, lineup cards
- **`docs/database.md`** — all DB tables, key functions, --status format, migration probe bug, reset procedure
- **`docs/discord.md`** — message types, pick alert format, formatting rules
- **`docs/odds.md`** — consensus odds logic, data sources, F5 details, EV + Kelly formulas
- **`docs/optimizer.md`** — optimizer mechanics, backtest reference, code queue, context injection, 7-day throttle
- **`docs/testing.md`** — test run command, mock patch rules, pre-existing failures (3), optimizer test gate
- **`PIPELINE.md`** — full architecture diagram, agent pipeline, pick filter gates, rolling stats pipeline, launchd schedule
- **`INSIGHTS.md`** — calibration data, bias log, weight tuning history
