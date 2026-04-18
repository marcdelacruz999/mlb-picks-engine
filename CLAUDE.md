# MLB Picks Engine — Claude Reference

**Project:** `/Users/marc/Projects/Claude/Projects/Shenron/mlb-picks-engine/`
**What:** Automated MLB betting picks engine. Collects live data, runs 7-agent weighted analysis, sends high-confidence plays to Discord via webhook. No manual intervention day-to-day.

---

## Daily Schedule (launchd)

| Time | Command | What it does |
|------|---------|--------------|
| 8:00 AM–5:00 PM (hourly) | `engine.py` | Full re-analysis every hour — 10 separate plists (run-8 through run-17); dedup blocks resends, lineup penalty (-1 conf) holds borderline picks until lineups confirm |
| Every 30 min | `monitor.py` | Pitcher scratch monitor — Discord alert on SP change |
| 11:00 PM | `engine.py --results` | Grades picks vs final scores; collects boxscores, game totals, and batter logs |
| 11:30 PM | `optimizer.py` | Nightly optimizer — analyzes data, implements one improvement (7-day code cooldown) |
| 1:45 AM | `export_db_snapshot.py` | Exports DB state to `DB_SNAPSHOT.md`, commits + pushes to GitHub so remote CEO agent has live context before it fires at 2:00 AM |
| Monday 7:00 AM | `calibrate.py` | Weekly signal calibration — posts Discord report; run with `--apply` to update weights (fires before 8 AM engine run) |

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
python3 engine.py --collect DATE  # Collect all post-game data for DATE: pitcher/team logs, game totals, batter logs
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

**All thresholds in config.py** — never hardcode in analysis.py (optimizer reads config.py to tune values). Includes HOME_FIELD_ADVANTAGE and BULLPEN_ERA_RUST_THRESHOLD (moved Apr 16).

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

**database.py is module-level functions** — NOT a class. Uses `get_connection()` per call. `import database as db` in data_mlb.py. See `docs/database.md` for full table/function reference.

**New DB tests pattern** — use `monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))`, then call `database.init_db()` and `database.get_connection()` directly. `get_connection()` reads `DB_PATH` (not `DATABASE_PATH`). See `tests/test_database.py`.

**MLB /schedule linescore endpoint** — does NOT return `abbreviation` field, only `team_id`. Use `db.get_team_abbr_by_mlb_id(team_id)` to look up abbreviations from the `teams` table.

**fetch_venue_weather() uses forecast API** — Open-Meteo `/v1/forecast` only returns reliable data for today + future dates. For historical game dates, use `fetch_venue_weather_archive()` (uses `archive-api.open-meteo.com/v1/archive`). `backfill_game_totals_weather()` calls the archive variant and is safe for historical backfill.

**_parse_total_line() returns None on failure** — engine.py O/U grading pushes when total line can't be parsed or is out of range (5–15). Never returns 0.0. If adding new O/U grading paths, always guard: `if total_line is None: status = "push"`.

**SQLite date('now') is local time** — in tests, use `datetime.now().isoformat()` (not `datetime.utcnow()`) for `created_at` inserts, or queries filtering by `date('now')` will miss the row.

**run_results() conn lifecycle** — conn is closed after pick grading, then reopened for the mlb_to_local build. Any code added after the grading loop must call `db.get_connection()` fresh — do not reuse a closed conn.

**--collect runs all three collectors** — `collect_boxscores`, `collect_game_totals`, `collect_batter_boxscores`. Missing any leaves gaps in batter streaks and O/U bias tracking.

**fetch_pitcher_stats() returns {} on no season stats** — if a pitcher hasn't started yet, `home_pitcher_stats` is `{}` and `home_p.get('name','?')` → `'?'`. Always fall back to `game.get('home_pitcher_name')` (populated from `probablePitcher.fullName` in schedule fetch). Fixed in `score_pitching()` in analysis.py.

**Manual Discord pick edit** — to patch an existing pick message: build a pick dict with `game`, `away_team`, `home_team`, `game_time_utc`, `kelly_fraction`, and all `edge_*` fields, then call `discord_bot.send_pick_edit(discord_message_id, pick_dict)`. The `discord_message_id` is stored in the `picks` table.

**Nightly report / board grading gotchas** — see `docs/discord.md` (Nightly Report section): confidence picks use `picks.status` not `analysis_log.ml_status`; boards use `db.get_today_sent_picks_with_game()`; `analysis_log.ml_pick_team` ≠ sent pick; how to fix stuck 0-0 games.

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
