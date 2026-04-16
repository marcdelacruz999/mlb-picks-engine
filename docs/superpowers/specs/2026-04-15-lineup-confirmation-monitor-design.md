# Lineup Confirmation Monitor — Design Spec

**Goal:** Extend `monitor.py` to alert on Discord when a pending pick's team lineup posts with significantly weaker OPS than expected, allowing manual review before first pitch.

---

## Architecture

`monitor.py` gets a second check loop: `run_lineup_monitor()`. It runs after `run_monitor()` (pitcher scratch check) in the same `main()` call. No new launchd plist needed — the existing 30-min schedule is sufficient.

The monitor watches pending picks that were sent when at least one lineup was unconfirmed (`home_lineup_confirmed=0` OR `away_lineup_confirmed=0` in `analysis_log` at pick time). When both lineups are now confirmed, it computes actual starter OPS using the same blended formula analysis.py uses, compares to the rolling team OPS stored in `analysis_log`, and fires a Discord alert if the pick team's lineup OPS is >10% weaker than expected.

---

## Components

### `monitor.py`
- Add `run_lineup_monitor()` — main logic function
- Add `get_current_lineups(mlb_game_id)` call (see data_mlb.py below)
- Call `run_lineup_monitor()` from `main()` after `run_monitor()`

### `data_mlb.py`
- Add `get_current_lineups(mlb_game_id: int) -> dict` — fetches current lineup IDs from MLB Stats API using `?hydrate=lineups`. Returns:
  ```python
  {
      "away_ids": [int, ...],
      "home_ids": [int, ...],
      "away_confirmed": bool,
      "home_confirmed": bool,
      "game_status": str,  # "Preview", "Live", "Final", etc.
  }
  ```

### `database.py`
- Add `CREATE TABLE IF NOT EXISTS lineup_alerts` migration:
  ```sql
  CREATE TABLE IF NOT EXISTS lineup_alerts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      mlb_game_id INTEGER NOT NULL,
      game_date TEXT NOT NULL,
      ops_actual REAL,
      ops_expected REAL,
      pct_drop REAL,
      created_at TEXT DEFAULT (datetime('now')),
      UNIQUE(mlb_game_id, game_date)
  );
  ```
- Add `lineup_alert_already_sent(mlb_game_id: int, game_date: str) -> bool`
- Add `save_lineup_alert(mlb_game_id: int, game_date: str, ops_actual: float, ops_expected: float, pct_drop: float)`

### `config.py`
- Add `LINEUP_OPS_DROP_THRESHOLD = 0.10` — minimum fractional OPS drop to trigger alert (10%)
- Add `LINEUP_MIN_PLAYERS_WITH_DATA = 5` — minimum players with OPS data required; fewer than this skips the alert

### `analysis_log` table
- No schema changes. Already stores: `home_lineup_confirmed`, `away_lineup_confirmed`, `away_batting_rolling` (JSON), `home_batting_rolling` (JSON), `ml_pick_team`, `away_team`, `home_team`, `mlb_game_id`.

---

## Data Flow

1. Load today's pending picks via `get_today_picks()`
2. Load today's `analysis_log` entries via `get_today_analysis_log()`; index by `mlb_game_id`
3. For each pending pick:
   a. Find the matching `analysis_log` entry
   b. If both `home_lineup_confirmed` and `away_lineup_confirmed` were True at pick time → skip (nothing to monitor)
   c. If `lineup_alert_already_sent(mlb_game_id, today)` → skip (already alerted)
   d. Call `get_current_lineups(mlb_game_id)`
   e. If `game_status` is `"Live"` or `"Final"` → skip (too late)
   f. If not both currently confirmed → skip (lineups not yet posted)
   g. Determine pick team side (home or away) from `analysis_log.ml_pick_team`
   h. Fetch pick team's confirmed starter IDs, call `fetch_lineup_batting(player_ids)`
   i. Compute blended OPS: for each player, blend rolling OPS (weight 0.8 if ≥20 games, 0.6 if ≥`MIN_BATTER_GAMES`) with season OPS; average across lineup
   j. If fewer than `LINEUP_MIN_PLAYERS_WITH_DATA` (5) players have OPS data → skip
   k. Get expected OPS from `analysis_log` rolling batting stats for pick team
   l. If expected OPS missing → fall back to season OPS from `fetch_team_batting()`; if still missing → skip
   m. Compute `pct_drop = (expected_ops - actual_ops) / expected_ops`
   n. If `pct_drop >= LINEUP_OPS_DROP_THRESHOLD` → send Discord alert, call `save_lineup_alert()`

---

## Discord Alert Format

```
⚠️ LINEUP ALERT — COL @ HOU
Astros confirmed lineup OPS: .698 (expected .771 — 9.5% weaker)
Pick: Astros ML 7/10 — consider revisiting.
```

- Only fires for the pick team (we don't alert on opponent's weak lineup — that would be a positive signal, not a warning)
- One alert per `(mlb_game_id, game_date)` — deduplicated via `lineup_alerts` table

---

## Threshold + Tuning

- `LINEUP_OPS_DROP_THRESHOLD = 0.10` in `config.py` — 10% drop vs rolling team OPS
- `LINEUP_MIN_PLAYERS_WITH_DATA = 5` — guards against early-season / callup data gaps producing false alerts
- Both values are optimizer-readable (in `config.py`) and can be tuned by `calibrate.py` over time

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `fetch_lineup_batting()` returns no data for a player | Skip that player; continue with remaining |
| Fewer than 5 players have OPS data | Skip game entirely — insufficient signal |
| Rolling OPS missing from `analysis_log` | Fall back to season OPS from `fetch_team_batting()` |
| Season OPS also missing | Skip game entirely |
| Both lineups confirmed at pick send time | Skip — nothing to monitor |
| Game status is Live or Final | Skip — too late to act |
| MLB API network error | Log and continue to next game; don't crash |
| Pick team unresolvable from `analysis_log` | Log and skip |

---

## Tests

File: `tests/test_lineup_monitor.py`

| Test | Description |
|------|-------------|
| `test_skips_game_confirmed_at_send_time` | Both lineups confirmed in analysis_log → game skipped, no API call |
| `test_skips_game_already_alerted` | `lineup_alert_already_sent` returns True → no alert fired |
| `test_skips_game_in_progress` | `game_status = "Live"` → game skipped |
| `test_skips_when_lineups_not_yet_posted` | Current lineups not both confirmed → no alert |
| `test_fires_alert_on_ops_drop_above_threshold` | Actual OPS 15% below expected → alert sent, `save_lineup_alert` called |
| `test_no_alert_on_small_drop` | 5% drop (below 10% threshold) → no alert |
| `test_no_alert_on_ops_improvement` | Actual OPS higher than expected → no alert |
| `test_skips_when_fewer_than_5_players_have_data` | Only 3 players return OPS data → no alert fired |
| `test_dedup_prevents_double_alert` | Alert sent once; second monitor run → `lineup_alert_already_sent` blocks resend |
