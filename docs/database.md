# MLB Picks Engine — Database Reference

SQLite at `mlb_picks.db`. Initialized via `database.init_db()` on every run.

## Tables

| Table | Purpose |
|-------|---------|
| `picks` | Approved picks sent to Discord; `pick_type`: moneyline / over / under / f5_ml |
| `analysis_log` | All 15 games per day; 7 agent score columns; UNIQUE(game_date, mlb_game_id) + INSERT OR REPLACE |
| `pitcher_game_logs` | Per-start: IP, ER, K, BB, H, HR, opponent_team_id, pitch_count, GB/FB counts, inherited runners |
| `team_game_logs` | Per-game: R, H, HR, K, BB, AB per team, is_away flag, team pitching stats (K, BB, H, ER, pitches) |
| `batter_game_logs` | Per-game per batter: OPS, K, BB, AB, H (hot/cold streak signal), runs, SB, HBP, PA |
| `opening_lines` | First captured odds per game per day (INSERT OR IGNORE — opening preserved) |
| `scratch_alerts` | Pitcher scratch dedup: UNIQUE(game_date, mlb_game_id, side) |
| `lineup_alerts` | Lineup OPS weakness dedup: UNIQUE(mlb_game_id, game_date) |
| `games` | Game records with final scores |
| `teams` | 30 teams: mlb_id, abbreviation, division, league |
| `players` | Player records: mlb_id, name, position, team_id |
| `pitcher_stats` | Season pitcher stats snapshot (ERA, WHIP, K9, BB9, K/BB) |
| `team_batting` | Season team batting snapshot (AVG, OBP, SLG, OPS, runs/game) |
| `bullpen_stats` | Season bullpen snapshot (ERA, WHIP, saves, blown saves) |
| `odds` | Raw odds captures per game per run |
| `daily_results` | W/L/P/ROI per day |
| `daily_board` | Discord ML picks board message ID tracking (for edits) |
| `daily_ou_board` | Discord O/U picks board message ID tracking (for edits) |

### picks columns
`id, game_id, pick_type, confidence, win_probability, edge_score, ev_score, kelly_fraction,
ml_odds, ou_odds, edge_pitching, edge_offense, edge_advanced, edge_bullpen, edge_weather,
edge_market, discord_sent, status, created_at`

### analysis_log columns
`id, game_date, mlb_game_id, away_team, home_team, ml_confidence, ou_confidence,
ml_pick, ou_pick, score_pitching, score_offense, score_bullpen, score_advanced,
score_momentum, score_weather, score_market, ml_status, ou_status, created_at`

### pitcher_game_logs columns
`id, mlb_game_id, game_date, pitcher_id, pitcher_name, team_id, opponent_team_id,
is_starter, innings_pitched, earned_runs, strikeouts, walks, hits, home_runs,
pitch_count, batters_faced, ground_outs, fly_outs, inherited_runners, inherited_runners_scored`

### team_game_logs columns
`id, mlb_game_id, game_date, team_id, is_away, runs, hits, home_runs, strikeouts, walks, at_bats,
pitching_strikeouts, pitching_walks, pitching_hits_allowed, pitching_earned_runs, pitching_home_runs_allowed`

### batter_game_logs columns
`id, mlb_game_id, game_date, batter_id, batter_name, team_id, ops, strikeouts, walks, at_bats, hits,
runs, stolen_bases, hit_by_pitch, plate_appearances`

---

## Key Functions (database.py)

| Function | What it does |
|----------|-------------|
| `save_pick(pick)` | Insert approved pick; deduped by `pick_already_sent_today(game_id, pick_type)` |
| `save_analysis_log(entry)` | Upsert via INSERT OR REPLACE |
| `get_today_analysis_log()` | All 15 entries for today |
| `update_analysis_log_result(...)` | Grade ML/OU after --results |
| `get_model_accuracy_summary(days)` | ML + O/U accuracy across logged games |
| `get_roi_summary(days)` | True unit P&L using stored send-time odds × kelly_fraction |
| `save_opening_lines(game_id, odds)` | INSERT OR IGNORE — first capture only |
| `get_opening_lines(game_id)` | Retrieve opening odds for comparison |
| `get_bullpen_top_relievers(team_id)` | Top 3 relievers by IP (last 7d), IP-weighted ERA |
| `get_pitcher_rolling_stats_adjusted(pitcher_id)` | Opponent-weighted rolling ERA/WHIP/K9/BB9 |
| `last_pitch_count(pitcher_id)` | Most recent start pitch count |
| `get_gb_fb_ratio(pitcher_id, n_games)` | Rolling GB% and FB% over last n starts |
| `get_inherited_runner_rate(pitcher_id, n_games)` | Inherited runners scored rate (bullpen signal) |
| `get_team_stolen_base_rate(team_id, n_games)` | Team SB per game rolling over last n games |

---

## --status Output

```
PICKS SENT:  5W - 2L - 0P  (71.4% win rate)  +1.23 units  [7 graded]
MODEL ML:    9W - 6L  (60.0% accuracy)  [15 games]
MODEL O/U:   7W - 4L  (63.6% accuracy)  [11 games]
```

- **PICKS SENT** — approved sent picks only; units = true P&L using stored send-time odds
- **MODEL** — all 15 daily games; raw pipeline signal independent of pick threshold
- ROI denominator = all graded picks (W+L+P); win_rate denominator = W+L only

---

## Migration Pattern

```python
# Add column safely — OperationalError if already exists is expected
try:
    conn.execute("ALTER TABLE picks ADD COLUMN new_col TEXT")
except sqlite3.OperationalError:
    pass
```

Never use bare `except Exception` for migrations.

**Migration probe bug (fixed 2026-04-12):** The picks table migration originally used a test INSERT
with game_id=-1 to detect the CHECK constraint. With `PRAGMA foreign_keys=ON`, this always
triggered FK violation → migration re-ran every init_db() call. Fix: inspect `sqlite_master`
schema text for "moneyline" keyword instead.

```python
picks_sql = conn.execute(
    "SELECT sql FROM sqlite_master WHERE type='table' AND name='picks'"
).fetchone()
needs_migration = picks_sql and "moneyline" in (picks_sql["sql"] or "")
```

---

## DB Reset / Clean Start

Run this to wipe transactional tables and start fresh:
```sql
DELETE FROM picks; DELETE FROM games; DELETE FROM analysis_log;
DELETE FROM opening_lines; DELETE FROM scratch_alerts; DELETE FROM daily_results;
```
(Last done 2026-04-12 — clean start from 2026-04-13 8am run)

---

## Gotchas

**database.py is module-level functions** — NOT a class. Uses `get_connection()` per call. `import database as db` in data_mlb.py.

**analysis_log re-runs use INSERT + UPDATE** — `save_analysis_log()` inserts on first run, then UPDATE on re-runs to refresh analysis fields while preserving `ml_status`/`ou_status` grading columns. Do not revert to INSERT OR IGNORE.

**MLB /schedule linescore endpoint** — does NOT return `abbreviation` field, only `team_id`. Use `db.get_team_abbr_by_mlb_id(team_id)` to look up from `teams` table.

**pitcher_game_logs / team_game_logs are forward-only** — `collect_boxscores()` runs nightly for yesterday only. If DB is empty, run `backfill_boxscores.py` (project root) to populate from Apr 1 through yesterday. Safe to re-run (INSERT OR IGNORE).

**run_results() conn lifecycle** — conn is closed after pick grading, then reopened for mlb_to_local build. Any code added after the grading loop must call `db.get_connection()` fresh — do not reuse a closed conn.

**SQLite date('now') is local time** — in tests use `datetime.now().isoformat()` (not `datetime.utcnow()`) for `created_at` inserts, or queries filtering by `date('now')` will miss the row.

**get_today_sent_picks_with_game()** — joins `picks` + `games` to return `mlb_game_id` alongside pick fields. Use this (not the current run's `approved` list) when building the ML/O/U boards so picks sent in earlier runs are included.

**`store_boxscores()` is the real write path** — `engine.py` calls `db.store_boxscores(pitcher_logs, team_logs)`, not `store_pitcher_game_logs()` / `store_team_game_logs()`. Add new pitcher/team columns here, not in the standalone functions.

**Rolling stat queries use `ORDER BY game_date DESC`** — `rows[0]` = most recent start. Using `ASC` returns the oldest game silently, giving wrong fatigue/form signals.

**INSERT OR IGNORE never updates existing rows** — when new columns are added, already-collected rows keep NULL/0 for those columns. Data fills in on the next nightly `--collect`.
