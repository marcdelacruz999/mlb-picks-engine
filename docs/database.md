# MLB Picks Engine — Database Reference

SQLite at `mlb_picks.db`. Initialized via `database.init_db()` on every run.

## Tables

| Table | Purpose |
|-------|---------|
| `picks` | Approved picks sent to Discord; `pick_type`: moneyline / over / under / f5_ml |
| `analysis_log` | All 15 games per day; 7 agent score columns; UNIQUE(game_date, mlb_game_id) + INSERT OR REPLACE |
| `pitcher_game_logs` | Per-start: IP, ER, K, BB, H, HR, opponent_team_id |
| `team_game_logs` | Per-game: R, H, HR, K, BB, AB per team, is_away flag |
| `batter_game_logs` | Per-game per batter: OPS, K, BB, AB, H (hot/cold streak signal) |
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
is_starter, innings_pitched, earned_runs, strikeouts, walks, hits, home_runs`

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
