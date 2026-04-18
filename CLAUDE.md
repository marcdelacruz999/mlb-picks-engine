# MLB Picks Engine — Claude Reference

**Project:** `/Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/`
**What:** Automated MLB betting picks engine. Collects live data, runs 7-agent weighted analysis, sends high-confidence plays to Discord via webhook. No manual intervention day-to-day.

---

## Daily Schedule (launchd)

| Time | Command | What it does |
|------|---------|--------------|
| 8:00 AM–5:00 PM (hourly) | `engine.py` | Full re-analysis every hour — 10 separate plists (run-8 through run-17) |
| Every 30 min | `monitor.py` | Pitcher scratch monitor — Discord alert on SP change |
| 11:00 PM | `engine.py --results` | Grades picks vs final scores; collects boxscores (pitch count, GB/FB, inherited runners, team pitching), game totals, batter logs |
| 11:30 PM | `optimizer.py` | Nightly optimizer — one improvement per run (7-day code cooldown) |
| 1:45 AM | `export_db_snapshot.py` | Exports DB to `DB_SNAPSHOT.md`, commits + pushes for 2 AM CEO agent |
| Monday 7:00 AM | `calibrate.py` | Weekly signal calibration — posts Discord report, optionally applies weights |

Plists at `~/Library/LaunchAgents/com.marc.mlb-picks-engine.*.plist`. Output logged to `engine.log`.

---

## CLI Usage

```bash
python3 engine.py                 # Full analysis + send picks
python3 engine.py --test          # Dry run — no Discord
python3 engine.py --refresh       # Re-validate sent picks, send updates
python3 engine.py --results       # Grade today's picks after games finish
python3 engine.py --status        # Print 30-day tracking snapshot
python3 engine.py --game X        # 7-agent analysis of game(s) matching team X (bypasses threshold)
python3 engine.py --collect DATE  # Collect all post-game data for DATE
python3 engine.py --report        # Re-send nightly report for today
python3 engine.py --report DATE   # Re-send nightly report for DATE (YYYY-MM-DD)
```

---

## Pick Filter

- Min confidence ML: **7/10** (`MIN_CONFIDENCE` in config.py)
- Min confidence O/U: **9/10** (`MIN_CONFIDENCE_OU`) — gap formula only, needs ≥1.5 run gap
- Min edge score: **0.12** (`MIN_EDGE_SCORE`) | Min EV: **−0.02** (`MIN_EV`)
- Max picks/day: **20** (`MAX_PICKS_PER_DAY`) — ML and O/U tracked independently
- Pick types: `moneyline` | `over` | `under` | `f5_ml`
- SP TBD cap: one unknown → max 3/10; both unknown → max 1/10
- If nothing qualifies → PASS, nothing sent

---

## File Map

```
engine.py       — orchestrator + all CLI flags
analysis.py     — 7 agents, kelly_stake(), risk_filter(), _analyze_over_under(), _analyze_f5_pick()
data_mlb.py     — MLB Stats API, Statcast, Open-Meteo, collect_boxscores()
data_odds.py    — The Odds API (full-game + F5); consensus ML/RL/total logic
discord_bot.py  — all webhook formatting and sends; daily boards (ML + O/U)
database.py     — SQLite — module-level functions, get_connection() per call
config.py       — WEIGHTS, PARK_FACTORS (30), UMPIRE_TENDENCIES (43), all thresholds
monitor.py      — pitcher scratch monitor
optimizer.py    — nightly improvement engine
calibrate.py    — weekly signal calibration (--test / --apply / --days N)
backtest.py     — 2024+2025 historical validator (4,855 games)
```

---

## Critical Rules

**All thresholds in config.py** — never hardcode in analysis.py; optimizer reads config.py to tune values.

**`analysis_log.ml_pick_team` ≠ sent pick** — `picks` table is ground truth. They diverge when a later run flips the side. See `docs/discord.md` for nightly report grading details.

**`--collect` runs all three collectors** — missing any leaves gaps in batter streaks and O/U bias tracking.

**Python 3.9** — no `float | None` union syntax; use `Optional[float]`. `zoneinfo` is stdlib.

---

## Reference Docs

Read these only when working in that area:

- **`docs/agents.md`** — 7-agent scoring logic, weights, rolling blend, lineup cards, gotchas
- **`docs/database.md`** — all tables, key functions, migration pattern, gotchas
- **`docs/discord.md`** — message types, pick alert format, nightly report rules, board gotchas
- **`docs/odds.md`** — consensus odds logic, F5 details, EV + Kelly formulas
- **`docs/optimizer.md`** — optimizer mechanics, backtest reference, code queue, 7-day throttle
- **`docs/testing.md`** — test run command, mock patch rules, DB test pattern, pre-existing failures
- **`docs/gotchas.md`** — Python/env quirks, MLB API surprises, operational traps
- **`docs/infrastructure.md`** — VPS backup schedule, disaster recovery, API key locations
- **`PIPELINE.md`** — full architecture diagram, agent pipeline, pick filter gates, launchd schedule
- **`INSIGHTS.md`** — calibration data, bias log, weight tuning history
